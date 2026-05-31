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
import sys
from pathlib import Path


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("artist", nargs="?", help="Local folder name under images/")
    p.add_argument("--all", action="store_true", help="Process every folder under images/")
    p.add_argument("--match-only", action="store_true")
    p.add_argument("--convert-only", action="store_true")
    p.add_argument("--upload-only", action="store_true")
    p.add_argument("--force", action="store_true", help="Re-upload even if R2 has the file")
    p.add_argument("--dry-run", action="store_true", help="Log actions; touch nothing")
    p.add_argument("--quality", type=int, default=85, help="WebP encoder quality")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if not args.artist and not args.all:
        print("error: must pass an artist name or --all", file=sys.stderr)
        return 2
    print("(scaffold) would process:", args.artist or "all artists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
