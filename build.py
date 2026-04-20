#!/usr/bin/env python3
"""Consolidate per-artist CSVs in csv_files/ into discographies.csv.

This is the single source of compiled data for the web viewer. Run locally
after editing any file in csv_files/ to preview changes in the browser.
CI also runs this on every push to main before deploying to GitHub Pages.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

OUTPUT_HEADERS = [
    "Bandleader", "Orchestra", "Date", "Title", "AltTitle", "Genre",
    "Singer", "Label", "Master", "Composer", "Author", "Arranger",
    "Grouping", "Pianist", "Bassist", "Bandoneons", "Strings", "Lineup",
]

DEDUPE_KEY_FIELDS = ("Orchestra", "Title", "Date", "Singer")


def build(csv_dir: Path, output_path: Path) -> int:
    """Concatenate all CSVs in csv_dir into output_path, deduping across files.

    Returns the number of rows written.
    """
    all_rows: list[dict[str, str]] = []
    source_files = sorted(csv_dir.glob("*.csv"))
    if not source_files:
        print(f"error: no CSV files found in {csv_dir}", file=sys.stderr)
        sys.exit(1)

    for source in source_files:
        with source.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                all_rows.append(row)

    consolidated: list[dict[str, str]] = []
    seen: dict[tuple, dict[str, str]] = {}
    cross_file_dups = 0
    for row in all_rows:
        key = tuple(row.get(f, "").strip().lower() for f in DEDUPE_KEY_FIELDS)
        if all(key) and key in seen:
            if row == seen[key]:
                cross_file_dups += 1
                continue
        if all(key):
            seen[key] = row
        consolidated.append(row)

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS)
        writer.writeheader()
        writer.writerows(consolidated)

    return len(consolidated)


def main() -> None:
    root = Path(__file__).parent
    csv_dir = root / "csv_files"
    output_path = root / "discographies.csv"

    if not csv_dir.is_dir():
        print(f"error: {csv_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    n = build(csv_dir, output_path)
    print(f"wrote {n} rows to {output_path.name}")


if __name__ == "__main__":
    main()
