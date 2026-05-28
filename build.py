#!/usr/bin/env python3
"""Consolidate per-artist CSVs in csv_files/ into discographies.csv.

This is the single source of compiled data for the web viewer. Run locally
after editing any file in csv_files/ to preview changes in the browser.
CI also runs this on every push to main before deploying to GitHub Pages.

Alongside discographies.csv, this also writes duplicates_report.csv listing
fuzzy-duplicate groups (same Orchestra+Title+Year but differing on other
fields) for manual review.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

OUTPUT_HEADERS = [
    "Bandleader", "Orchestra", "Date", "Title", "AltTitle", "Genre",
    "Singer", "Label", "Master", "Matrix", "Disc", "Composer", "Author",
    "Arranger", "Grouping", "Pianist", "Bassist", "Bandoneons", "Strings",
    "Lineup",
]

DEDUPE_KEY_FIELDS = ("Orchestra", "Title", "Date", "Singer")

YEAR_RE = re.compile(r"(\d{4})")

IMAGE_BASE = "https://pub-df59ead2b87f40468ed4dcba1d274efa.r2.dev"


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text or "")
        if unicodedata.category(c) != "Mn"
    )


def bandleader_folder(name: str) -> str:
    """Last+First, accents and apostrophes/punctuation removed, no spaces."""
    cleaned = re.sub(r"[^\w\s]", "", strip_accents(name or ""))
    parts = cleaned.split()
    if not parts:
        return ""
    return parts[-1] + "".join(parts[:-1])


def artist_match_key(name: str) -> str:
    """Normalized key to pair lp_matches filenames with Bandleader values."""
    cleaned = re.sub(r"[^\w\s]", "", strip_accents(name or ""))
    return re.sub(r"\s+", " ", cleaned.strip().lower())


def normalize_date(value: str) -> str:
    """M/D/YYYY or YYYY-M-D -> ISO YYYY-MM-DD; anything else passes through."""
    value = (value or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", value)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", value)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return value


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(value or "").strip().lower())


def catalog_sort_value(catalog: str) -> int:
    """Sortable rank from a catalog string; lowest = original pressing."""
    nums = re.findall(r"\d+", catalog or "")
    return int(max(nums, key=len)) if nums else 10**9


def lp_image_url(folder: str, lp_folder: str, image_type: str) -> str:
    filename = f"{lp_folder} {image_type}.webp"
    return f"{IMAGE_BASE}/{quote(folder)}/LPs/{quote(lp_folder)}/{quote(filename)}"


def join_key(date: str, title: str, singer: str) -> tuple:
    return (normalize_date(date), normalize_title(title), (singer or "").strip())


def load_lp_data(lp_dir: Path) -> dict:
    """Read lp_matches/ into {artist_key: {"matches": {join_key: [cand]}, "manifest": {LP_Folder: [type]}}}.

    Track-match files are named "{Artist}.csv"; image manifests "{Artist} images.csv".
    Candidate and manifest lists preserve CSV row order (used for tiebreaks/carousel).
    """
    data: dict = {}
    if not lp_dir.is_dir():
        return data
    for path in sorted(lp_dir.glob("*.csv")):
        stem = path.stem
        is_manifest = stem.endswith(" images")
        artist_stem = stem[: -len(" images")] if is_manifest else stem
        key = artist_match_key(artist_stem)
        entry = data.setdefault(key, {"matches": {}, "manifest": {}})
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if is_manifest:
                    folder = (row.get("LP_Folder") or "").strip()
                    ttype = (row.get("Type") or "").strip()
                    if folder and ttype:
                        entry["manifest"].setdefault(folder, []).append(ttype)
                else:
                    jk = join_key(
                        row.get("Disc_Date", ""),
                        row.get("Disc_Title", ""),
                        row.get("Singer", ""),
                    )
                    entry["matches"].setdefault(jk, []).append({
                        "LP_Folder": (row.get("LP_Folder") or "").strip(),
                        "LP_Catalog": (row.get("LP_Catalog") or "").strip(),
                        "LP_Title": (row.get("LP_Title") or "").strip(),
                    })
    return data


def extract_year(date_value: str) -> str:
    match = YEAR_RE.search(date_value or "")
    return match.group(1) if match else ""


def build(csv_dir: Path, output_path: Path) -> tuple[int, list[dict]]:
    """Concatenate all CSVs in csv_dir into output_path, deduping across files.

    Returns the number of rows written and a list of fuzzy-duplicate group
    summaries.
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
                row["_source_file"] = source.name
                all_rows.append(row)

    consolidated: list[dict[str, str]] = []
    seen: dict[tuple, dict[str, str]] = {}
    for row in all_rows:
        key = tuple(row.get(f, "").strip().lower() for f in DEDUPE_KEY_FIELDS)
        if all(key) and key in seen:
            cleaned_existing = {k: v for k, v in seen[key].items() if k != "_source_file"}
            cleaned_new = {k: v for k, v in row.items() if k != "_source_file"}
            if cleaned_existing == cleaned_new:
                continue
        if all(key):
            seen[key] = row
        consolidated.append(row)

    fuzzy_groups = collect_fuzzy_duplicates(consolidated)

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS)
        writer.writeheader()
        for row in consolidated:
            writer.writerow({h: row.get(h, "") for h in OUTPUT_HEADERS})

    return len(consolidated), fuzzy_groups


def collect_fuzzy_duplicates(rows: list[dict[str, str]]) -> list[dict]:
    """Group rows that share Orchestra+Title+Year but differ on Singer or Date.

    Returns one report row per fuzzy group, with the source files and the
    set of differing field values.
    """
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        orch = row.get("Orchestra", "").strip().lower()
        title = row.get("Title", "").strip().lower()
        year = extract_year(row.get("Date", ""))
        if orch and title and year:
            groups[(orch, title, year)].append(row)

    report = []
    for (orch_key, title_key, year), group in groups.items():
        if len(group) < 2:
            continue
        singers = sorted({r.get("Singer", "").strip() for r in group})
        dates = sorted({r.get("Date", "").strip() for r in group})
        if len(singers) <= 1 and len(dates) <= 1:
            continue
        report.append({
            "Orchestra": group[0].get("Orchestra", ""),
            "Title": group[0].get("Title", ""),
            "Year": year,
            "RowCount": len(group),
            "Singers": " | ".join(singers),
            "Dates": " | ".join(dates),
            "SourceFiles": " | ".join(sorted({r.get("_source_file", "") for r in group})),
        })
    report.sort(key=lambda r: (-r["RowCount"], r["Orchestra"], r["Title"]))
    return report


def write_duplicates_report(report_path: Path, report: list[dict]) -> None:
    headers = ["Orchestra", "Title", "Year", "RowCount", "Singers", "Dates", "SourceFiles"]
    with report_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(report)


def main() -> None:
    root = Path(__file__).parent
    csv_dir = root / "csv_files"
    output_path = root / "discographies.csv"
    report_path = root / "duplicates_report.csv"

    if not csv_dir.is_dir():
        print(f"error: {csv_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    n, fuzzy = build(csv_dir, output_path)
    write_duplicates_report(report_path, fuzzy)
    print(f"wrote {n} rows to {output_path.name}")
    print(f"wrote {len(fuzzy)} fuzzy-duplicate groups to {report_path.name}")


if __name__ == "__main__":
    main()
