#!/usr/bin/env python3
"""Retroactively crop full-disc record photos down to just the label.

Walks ``images/<ArtistFolder>/Singles/**/*.webp`` (or an arbitrary ``--dir``),
classifies each file with ``_crop_label.classify_and_detect`` and, with
``--apply``, crops the ``full_disc`` photos in place (re-encoding WEBP at
quality 90). ``label_only`` and ``unknown`` files are never modified. Dry-run by
default; always writes a report CSV listing every file's verdict.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from _crop_label import classify_and_detect, crop_to_label

REPO = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _detail(bbox) -> str:
    """Human-readable circle center/radius summary for the report."""
    if not bbox:
        return ""
    left, top, right, bottom = bbox
    cx, cy = (left + right) // 2, (top + bottom) // 2
    r = (right - left) // 2
    return f"center=({cx},{cy}) r={r}"


def process(files: list[Path], apply: bool, quality: int) -> tuple[Counter, list[list[str]]]:
    """Classify (and optionally crop) each file; return (verdict counts, report rows)."""
    counts: Counter = Counter()
    rows: list[list[str]] = []
    for p in files:
        try:
            with Image.open(p) as im:
                im.load()
                verdict, bbox = classify_and_detect(im)
                action = "none"
                if verdict == "full_disc" and bbox is not None:
                    if apply:
                        crop_to_label(im.convert("RGB"), bbox).save(p, "WEBP", quality=quality)
                        action = "cropped"
                    else:
                        action = "would-crop"
        except (UnidentifiedImageError, OSError) as e:
            verdict, bbox, action = "error", None, str(e)
        counts[verdict] += 1
        rows.append([str(p), verdict, action, _detail(bbox)])
    return counts, rows


def collect(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.webp") if p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("artist", nargs="?", help="images/<ArtistFolder> name, e.g. DiSarli")
    ap.add_argument("--dir", help="crop an arbitrary directory instead of an artist folder")
    ap.add_argument("--apply", action="store_true", help="crop full_disc files in place")
    ap.add_argument("--quality", type=int, default=90, help="WEBP quality for re-encode")
    args = ap.parse_args()

    if args.dir:
        root = Path(args.dir)
        report = root / "_crop_report.csv"
    elif args.artist:
        root = REPO / "images" / args.artist / "Singles"
        report = REPO / "images" / args.artist / "_crop_report.csv"
    else:
        ap.error("give an artist folder or --dir")

    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    files = collect(root)
    print(f"scanning {len(files)} webp under {root}")
    counts, rows = process(files, args.apply, args.quality)

    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "verdict", "action", "detail"])
        w.writerows(rows)

    print("verdicts: " + ", ".join(f"{k}={counts[k]}" for k in sorted(counts)))
    cropped = sum(1 for r in rows if r[2] == "cropped")
    would = sum(1 for r in rows if r[2] == "would-crop")
    if args.apply:
        print(f"cropped {cropped} full_disc files in place (quality {args.quality})")
    else:
        print(f"dry-run: {would} full_disc files would be cropped (pass --apply)")
    print(f"report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
