#!/usr/bin/env python3
"""Convert, build image manifest, and upload artist images to Cloudflare R2.

Usage:
    python sync_artist_images.py <Artist>
    python sync_artist_images.py <Artist> --convert-only
    python sync_artist_images.py <Artist> --manifest-only
    python sync_artist_images.py <Artist> --upload-only
    python sync_artist_images.py <Artist> --force
    python sync_artist_images.py <Artist> --quality 90
    python sync_artist_images.py <Artist> --lossless
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
    p.add_argument("--manifest-only", action="store_true")
    p.add_argument("--convert-only", action="store_true")
    p.add_argument("--upload-only", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--prune", action="store_true",
                   help="After upload, delete local raster originals (.jpg/.jpeg/.png) whose .webp is confirmed on R2")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quality", type=int, default=85)
    p.add_argument("--lossless", action="store_true", help="Convert to lossless WebP instead of lossy quality")
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

    run_all = not (args.manifest_only or args.convert_only or args.upload_only)
    do_convert = run_all or args.convert_only
    do_manifest = run_all or args.manifest_only
    do_upload = run_all or args.upload_only

    # ---- Phase 1: Convert ----
    if do_convert:
        print(f"== Convert == {art_root}")
        if args.dry_run:
            n = 0
            for p in art_root.rglob("*"):
                if p.suffix.lower() in (".jpg", ".jpeg", ".png") and not p.with_suffix(".webp").exists():
                    print(f"  would convert: {p}")
                    n += 1
            print(f"  ({n} files would be converted)")
        else:
            converted = _convert.convert_tree(art_root, quality=args.quality, lossless=args.lossless)
            print(f"  converted {len(converted)} files")

    # ---- Phase 2: Manifest ----
    if do_manifest:
        print(f"== Manifest == {art_root}")
        manifest_rows = _manifest.walk_collection(art_root)

        lp_match_name = display.replace("'", "")  # lp_matches/ filenames drop the apostrophe by historical convention
        manifest_path = repo_root / "lp_matches" / f"{lp_match_name} images.csv"

        if args.dry_run:
            print(f"  would write {len(manifest_rows)} manifest rows to {manifest_path}")
        else:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            _manifest.write_manifest(manifest_path, manifest_rows)
            print(f"  wrote {len(manifest_rows)} manifest rows to {manifest_path}")

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

            uploaded = skipped = failed = unknown = 0
            # WebPs we have *confirmed* are present on R2 (freshly uploaded or HEAD 200).
            # Only these make their sibling raster originals safe to delete.
            confirmed_on_r2: set[Path] = set()

            for webp in sorted(art_root.rglob("*.webp")):
                key = _r2.key_for_local(webp, artist_root=art_root, bandleader_folder_name=bandleader_folder_name)
                url = _r2.public_url(cfg.public_base, key)
                if not args.force:
                    exists = _r2.head_exists(url)
                    if exists is True:
                        skipped += 1
                        confirmed_on_r2.add(webp)
                        continue
                    if exists is _r2.HEAD_UNKNOWN:
                        # Transient/unknown remote state: do NOT blindly re-upload and do NOT
                        # treat as confirmed (so its original won't be deleted). Warn and skip.
                        print(f"  warn: HEAD state unknown for {key}; skipping (no re-upload, no cleanup)", file=sys.stderr)
                        unknown += 1
                        continue
                    # exists is False (404): fall through to upload.
                try:
                    _r2.upload_file(client, bucket=cfg.bucket, key=key, path=webp)
                    uploaded += 1
                    confirmed_on_r2.add(webp)
                except Exception as e:
                    print(f"  fail: {webp} -> {key}: {e}", file=sys.stderr)
                    failed += 1

            print(f"  uploaded={uploaded} skipped={skipped} unknown={unknown} failed={failed}")

            # Delete raster originals (.jpg/.jpeg/.png) whose WebP is confirmed on R2.
            # Gated behind --prune so a normal sync never mass-deletes source rasters;
            # never delete an original whose webp is not confirmed present.
            if args.prune:
                removed = 0
                for webp in confirmed_on_r2:
                    for orig in _sibling_originals(webp):
                        if orig.exists():
                            orig.unlink()
                            removed += 1
                print(f"  pruned {removed} originals (--prune)")
            else:
                pendable = sum(1 for w in confirmed_on_r2 for o in _sibling_originals(w) if o.exists())
                if pendable:
                    print(f"  ({pendable} raster originals have a confirmed webp; run with --prune to delete them)")

    return 0


# Raster suffixes whose presence next to a confirmed .webp marks a deletable original.
_ORIGINAL_SUFFIXES = (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")


def _sibling_originals(webp: Path) -> list[Path]:
    """Sibling raster originals for a given .webp (same stem, raster suffix)."""
    return [webp.with_suffix(suf) for suf in _ORIGINAL_SUFFIXES]


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
