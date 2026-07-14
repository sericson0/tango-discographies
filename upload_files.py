#!/usr/bin/env python3
"""Force-upload specific image files to R2 (targeted refresh).

Complement to sync_artist_images.py: that script HEAD-skips keys that already
exist, so a file whose *content* changed (e.g. recropped by crop_labels.py)
never refreshes. This one takes explicit local paths and PUTs them
unconditionally.

Usage:
    python upload_files.py <path> [<path> ...]
    python upload_files.py --from-crop-report <Artist>   # action == cropped
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import _r2
from _artist_map import ARTIST_DISPLAY
from build import bandleader_folder

REPO_ROOT = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def artist_of(path: Path) -> str:
    """images/<Artist>/... -> <Artist>."""
    rel = path.resolve().relative_to((REPO_ROOT / "images").resolve())
    return rel.parts[0]


def upload(paths: list[Path]) -> int:
    cfg = _r2.load_env()
    client = _r2.make_client(cfg)
    ok = failed = 0
    for p in paths:
        if not p.exists():
            print(f"  skip (missing): {p}", file=sys.stderr)
            failed += 1
            continue
        artist = artist_of(p)
        display = ARTIST_DISPLAY.get(artist, artist)
        art_root = REPO_ROOT / "images" / artist
        key = _r2.key_for_local(p, artist_root=art_root,
                                bandleader_folder_name=bandleader_folder(display))
        try:
            _r2.upload_file(client, bucket=cfg.bucket, key=key, path=p)
            print(f"  uploaded: {key}")
            ok += 1
        except Exception as e:  # keep going; report at the end
            print(f"  fail: {p} -> {key}: {e}", file=sys.stderr)
            failed += 1
    print(f"uploaded={ok} failed={failed}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="local image paths under images/")
    ap.add_argument("--from-crop-report", metavar="ARTIST",
                    help="upload every file _crop_report.csv marks as cropped")
    args = ap.parse_args()

    paths = [Path(p) for p in args.paths]
    if args.from_crop_report:
        report = REPO_ROOT / "images" / args.from_crop_report / "_crop_report.csv"
        if not report.exists():
            print(f"error: no crop report at {report}", file=sys.stderr)
            return 2
        with report.open(encoding="utf-8-sig", newline="") as f:
            paths += [Path(r["path"]) for r in csv.DictReader(f)
                      if r.get("action") == "cropped"]
    if not paths:
        print("nothing to upload", file=sys.stderr)
        return 2
    return upload(paths)


if __name__ == "__main__":
    sys.exit(main())
