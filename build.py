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
    "Lineup", "LP_Title", "LP_Images",
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


def lp_image_url(folder: str, lp_folder: str, image_type: str, kind: str = "LP") -> str:
    subdir = "EPs" if (kind or "LP").upper() == "EP" else "LPs"
    filename = f"{lp_folder} {image_type}.webp"
    return f"{IMAGE_BASE}/{quote(folder)}/{subdir}/{quote(lp_folder)}/{quote(filename)}"


def join_key(date: str, title: str, singer: str) -> tuple:
    return (normalize_date(date), normalize_title(title), (singer or "").strip())


def load_manifests(lp_dir: Path) -> dict:
    """Read lp_matches/<Artist> images.csv files -> {artist_key: {folder: [(type, kind)]}}."""
    data: dict = {}
    if not lp_dir.is_dir():
        return data
    for path in sorted(lp_dir.glob("*images.csv")):
        stem = path.stem
        if not stem.endswith(" images"):
            continue
        key = artist_match_key(stem[: -len(" images")])
        entry = data.setdefault(key, {})
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                folder = (row.get("LP_Folder") or "").strip()
                ttype = (row.get("Type") or "").strip()
                kind = (row.get("Kind") or "LP").strip() or "LP"
                if folder and ttype:
                    entry.setdefault(folder, []).append((ttype, kind))
    return data


def apply_lp_images(rows: list[dict], manifests: dict) -> dict:
    """Set LP_Title/LP_Images from each row's Img_* columns + the image manifest.

    Img_Type EP/LP with an Img_Folder gets that release's art attached. The main
    (cover) image is chosen by kind: LPs always lead with the Front cover; EPs
    lead with the disk label for the track's side (Img_Side 'A' -> Disk 1,
    'B' -> Disk 2) when that scan exists, else fall back to the Front. Manifest
    entries are filtered to the matching kind (a folder name can exist as both an
    EP and LP). Img_Type Single / blank -> no LP_Images (built client-side).

    Returns a {(img_type, folder_name): count} dict of EP/LP rows whose
    Img_Folder produced an EMPTY LP_Images (no manifest entry matched), so the
    caller can surface this drift.
    """
    empty: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        img_type = (row.get("Img_Type") or "").strip().upper()
        folder_name = (row.get("Img_Folder") or "").strip()
        if img_type not in ("EP", "LP") or not folder_name:
            continue
        entry = manifests.get(artist_match_key(row.get("Bandleader", "")))
        if not entry:
            empty[(img_type, folder_name)] += 1
            continue
        types = [tk for tk in entry.get(folder_name, [])
                 if (tk[1] or "LP").strip().upper() == img_type]
        if not types:
            empty[(img_type, folder_name)] += 1
            continue
        side = (row.get("Img_Side") or "").strip().upper()
        if img_type == "EP":
            # EP cover = the disk label for this track's side, when that scan exists.
            avail = {t.strip().lower() for t, _ in types}
            want = {"A": "disk 1", "B": "disk 2"}.get(side)
            main_type = want if (want and want in avail) else "front"
        else:  # LP: always lead with the front cover.
            main_type = "front"
        # Cover-fallback chain: never silently leave a 'Back' scan as the cover.
        # Prefer the desired main_type, then front, then disk 1, then side 1.
        avail = {t.strip().lower() for t, _ in types}
        cover_type = next(
            (c for c in (main_type, "front", "disk 1", "side 1") if c in avail),
            None,
        )
        if cover_type is None:
            cover_type = main_type  # keep current order; warn below
            print(
                f"warning: no preferred cover (front/disk 1/side 1) for "
                f"Img_Folder {folder_name!r} ({img_type}); using "
                f"{types[0][0]!r} as cover",
                file=sys.stderr,
            )
        ordered = sorted(types, key=lambda tk: 0 if tk[0].strip().lower() == cover_type else 1)
        folder = bandleader_folder(row.get("Bandleader", ""))
        images = [{"type": t, "url": lp_image_url(folder, folder_name, t, kind=k)}
                  for t, k in ordered]
        row["LP_Title"] = (row.get("Img_Album") or "").strip()
        row["LP_Images"] = json.dumps(images, ensure_ascii=False)
    return dict(empty)


def extract_year(date_value: str) -> str:
    match = YEAR_RE.search(date_value or "")
    return match.group(1) if match else ""


def build(csv_dir: Path, output_path: Path, lp_dir: Path) -> tuple[int, list[dict]]:
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

    empty_folders = apply_lp_images(consolidated, load_manifests(lp_dir))
    if empty_folders:
        print(
            f"warning: {len(empty_folders)} EP/LP Img_Folder(s) produced empty "
            f"LP_Images (no manifest entry matched):",
            file=sys.stderr,
        )
        for (img_type, folder_name), count in sorted(empty_folders.items()):
            print(f"  {img_type} {folder_name!r}: {count} row(s)", file=sys.stderr)
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
    lp_dir = root / "lp_matches"
    report_path = root / "duplicates_report.csv"

    if not csv_dir.is_dir():
        print(f"error: {csv_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    n, fuzzy = build(csv_dir, output_path, lp_dir)
    write_duplicates_report(report_path, fuzzy)
    print(f"wrote {n} rows to {output_path.name}")
    print(f"wrote {len(fuzzy)} fuzzy-duplicate groups to {report_path.name}")


if __name__ == "__main__":
    main()
