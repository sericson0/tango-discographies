#!/usr/bin/env python3
"""Convert, match, and upload artist images to Cloudflare R2.

Usage:
    python sync_artist_images.py <Artist>
    python sync_artist_images.py <Artist> --convert-only
    python sync_artist_images.py <Artist> --match-only
    python sync_artist_images.py <Artist> --upload-only
    python sync_artist_images.py <Artist> --force
    python sync_artist_images.py <Artist> --quality 90
    python sync_artist_images.py <Artist> --dry-run
    python sync_artist_images.py --all
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import _convert
import _manifest
import _match
import _r2
from _artist_map import ARTIST_DISPLAY
from build import bandleader_folder as _bandleader_folder

# Force UTF-8 stdout/stderr on Windows so accented LP/EP folder names print cleanly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("artist", nargs="?", help="Local folder name under images/")
    p.add_argument("--all", action="store_true", help="Process every folder under images/")
    p.add_argument("--match-only", action="store_true")
    p.add_argument("--convert-only", action="store_true")
    p.add_argument("--upload-only", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quality", type=int, default=85)
    return p.parse_args(argv)


def resolve_artist(local_name: str, repo_root: Path) -> tuple[str, Path]:
    if local_name not in ARTIST_DISPLAY:
        print(f"error: unknown artist '{local_name}'. Add it to _artist_map.py ARTIST_DISPLAY.", file=sys.stderr)
        raise SystemExit(2)
    display = ARTIST_DISPLAY[local_name]
    return display, repo_root / "csv_files" / f"{display}.csv"


def artist_root(local_name: str, repo_root: Path) -> Path:
    return repo_root / "images" / local_name


def _load_discography(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"discography CSV not found: {path}")
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def _local_folder_names(artist_root_path: Path) -> dict[str, dict[str, str]]:
    """Return {kind_subdir: {lowercase_name: actual_name}} for LPs/ and EPs/."""
    out: dict[str, dict[str, str]] = {"LPs": {}, "EPs": {}}
    for sub in ("LPs", "EPs"):
        base = artist_root_path / sub
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.is_dir():
                out[sub][d.name.strip().lower()] = d.name
    return out


def process_artist(local_name: str, args: argparse.Namespace, repo_root: Path) -> int:
    display, discog_csv = resolve_artist(local_name, repo_root)
    art_root = artist_root(local_name, repo_root)
    if not art_root.is_dir():
        print(f"error: {art_root} does not exist", file=sys.stderr)
        return 1

    run_all = not (args.match_only or args.convert_only or args.upload_only)
    do_convert = run_all or args.convert_only
    do_match = run_all or args.match_only
    do_upload = run_all or args.upload_only

    pending_deletes: list[tuple[Path, Path]] = []

    # ---- Phase 1: Convert ----
    if do_convert:
        print(f"== Convert == {art_root}")
        if args.dry_run:
            n = 0
            for p in art_root.rglob("*"):
                if p.suffix.lower() in (".jpg", ".jpeg") and not p.with_suffix(".webp").exists():
                    print(f"  would convert: {p}")
                    n += 1
            print(f"  ({n} files would be converted)")
        else:
            pending_deletes = _convert.convert_tree(art_root, quality=args.quality)
            print(f"  converted {len(pending_deletes)} files")

    # ---- Phase 2: Match ----
    if do_match:
        print(f"== Match == {discog_csv}")
        recs = _match.prepare(_load_discography(discog_csv))
        folder_names = _local_folder_names(art_root)
        all_rows: list[dict] = []

        lps_csv = art_root / "LPs" / "LPs.csv"
        if lps_csv.exists():
            all_rows.extend(_match.match_lps_csv(lps_csv, recs, folder_names["LPs"]))
        eps_csv = art_root / "EPs" / "EPs.csv"
        if eps_csv.exists():
            all_rows.extend(_match.match_eps_csv(eps_csv, recs, folder_names["EPs"]))

        manifest_rows = _manifest.walk_collection(art_root)

        lp_match_name = display.replace("'", "")  # lp_matches/ filenames drop the apostrophe by historical convention
        matches_path = repo_root / "lp_matches" / f"{lp_match_name}.csv"
        manifest_path = repo_root / "lp_matches" / f"{lp_match_name} images.csv"

        if args.dry_run:
            print(f"  would write {len(all_rows)} match rows to {matches_path}")
            print(f"  would write {len(manifest_rows)} manifest rows to {manifest_path}")
        else:
            matches_path.parent.mkdir(parents=True, exist_ok=True)
            _match.write_matches_csv(matches_path, all_rows)
            _manifest.write_manifest(manifest_path, manifest_rows)
            unmatched = sum(1 for r in all_rows if r.get("Match_Status", "").startswith("no_title"))
            print(f"  wrote {len(all_rows)} match rows ({unmatched} unmatched) and {len(manifest_rows)} manifest rows")

    # ---- Phase 3: Upload ----
    if do_upload:
        print("== Upload ==")
        bandleader_folder_name = _bandleader_folder(display)

        if args.dry_run:
            n = 0
            for webp in sorted(art_root.rglob("*.webp")):
                key = _r2.key_for_local(webp, artist_root=art_root, bandleader_folder_name=bandleader_folder_name)
                print(f"  would upload: {webp} -> {key}")
                n += 1
            print(f"  ({n} files would be uploaded)")
        else:
            cfg = _r2.load_env()
            client = _r2.make_client(cfg)

            uploaded = skipped = failed = 0
            succeeded_for_delete: set[Path] = set()

            for webp in sorted(art_root.rglob("*.webp")):
                key = _r2.key_for_local(webp, artist_root=art_root, bandleader_folder_name=bandleader_folder_name)
                url = _r2.public_url(cfg.public_base, key)
                if not args.force and _r2.head_exists(url):
                    skipped += 1
                    succeeded_for_delete.add(webp)
                    continue
                try:
                    _r2.upload_file(client, bucket=cfg.bucket, key=key, path=webp)
                    uploaded += 1
                    succeeded_for_delete.add(webp)
                except Exception as e:
                    print(f"  fail: {webp} -> {key}: {e}", file=sys.stderr)
                    failed += 1

            print(f"  uploaded={uploaded} skipped={skipped} failed={failed}")

            # Delete JPEGs whose WebP made it to R2.
            if pending_deletes:
                removed = 0
                for jpeg, webp in pending_deletes:
                    if webp in succeeded_for_delete and jpeg.exists():
                        jpeg.unlink()
                        removed += 1
                print(f"  cleaned up {removed} JPEG originals")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    repo_root = Path.cwd()
    if args.all:
        images = repo_root / "images"
        if not images.is_dir():
            print(f"error: {images} does not exist", file=sys.stderr)
            return 1
        rc = 0
        for d in sorted(p for p in images.iterdir() if p.is_dir()):
            if d.name not in ARTIST_DISPLAY:
                print(f"skip: {d.name} (not in ARTIST_DISPLAY)")
                continue
            r = process_artist(d.name, args, repo_root)
            rc = rc or r
        return rc
    if not args.artist:
        print("error: must pass an artist name or --all", file=sys.stderr)
        return 2
    return process_artist(args.artist, args, repo_root)


if __name__ == "__main__":
    sys.exit(main())
