#!/usr/bin/env python3
"""Thorough quality/normalization audit for tango discography CSVs."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


TARGET_HEADERS = [
    "Bandleader",
    "Orchestra",
    "Date",
    "Title",
    "AltTitle",
    "Genre",
    "Singer",
    "Label",
    "Master",
    "Composer",
    "Author",
    "Arranger",
    "Grouping",
    "Pianist",
    "Bassist",
    "Bandoneons",
    "Strings",
]

UNKNOWN_TOKENS = {
    "unknown",
    "unnamed",
    "unnamed student",
    "n/a",
    "na",
    "[unknown]",
    "?",
}

DATE_YEAR = re.compile(r"^\d{4}$")
DATE_MDY = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
DATE_YYYY_MM = re.compile(r"^\d{4}-\d{2}$")
DATE_YYYY_MM_DD = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def canonical_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def has_weird_chars(value: str) -> bool:
    if not value:
        return False
    return ("\ufeff" in value) or ("�" in value)


def split_people(cell: str) -> list[str]:
    if not cell:
        return []
    return [x.strip() for x in cell.split(",") if x.strip()]


def extract_year(date_value: str) -> str:
    date_value = (date_value or "").strip()
    if DATE_YEAR.fullmatch(date_value):
        return date_value
    if DATE_MDY.fullmatch(date_value):
        return date_value.split("/")[-1]
    if DATE_YYYY_MM.fullmatch(date_value) or DATE_YYYY_MM_DD.fullmatch(date_value):
        return date_value[:4]
    return ""


def audit(csv_dir: Path) -> dict:
    files = sorted(csv_dir.glob("*.csv"))

    header_mismatches = []
    rows_total = 0

    unknown_cells = Counter()
    weird_char_cells = Counter()
    empty_rates = Counter()

    date_formats = Counter()
    bad_date_examples = []

    # Variants by normalized key.
    variants = {
        "Genre": defaultdict(Counter),
        "Singer": defaultdict(Counter),
        "Label": defaultdict(Counter),
        "Master": defaultdict(Counter),
        "Composer_token": defaultdict(Counter),
        "Author_token": defaultdict(Counter),
    }

    # Duplicate heuristics
    strict_dup = []
    title_orchestra_year_dup = []

    for path in files:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            if headers != TARGET_HEADERS:
                header_mismatches.append({"file": path.name, "headers": headers})

            strict_groups = defaultdict(list)
            fuzzy_groups = defaultdict(list)

            for i, row in enumerate(reader, start=2):
                rows_total += 1

                for h in TARGET_HEADERS:
                    val = (row.get(h, "") or "").strip()
                    if not val:
                        empty_rates[h] += 1
                    if canonical_text(val) in UNKNOWN_TOKENS:
                        unknown_cells[h] += 1
                    if has_weird_chars(val):
                        weird_char_cells[h] += 1

                d = (row.get("Date", "") or "").strip()
                if DATE_YEAR.fullmatch(d):
                    date_formats["YYYY"] += 1
                elif DATE_MDY.fullmatch(d):
                    date_formats["M/D/YYYY"] += 1
                elif DATE_YYYY_MM.fullmatch(d):
                    date_formats["YYYY-MM"] += 1
                    if len(bad_date_examples) < 50:
                        bad_date_examples.append({"file": path.name, "row": i, "date": d})
                elif DATE_YYYY_MM_DD.fullmatch(d):
                    date_formats["YYYY-MM-DD"] += 1
                    if len(bad_date_examples) < 50:
                        bad_date_examples.append({"file": path.name, "row": i, "date": d})
                elif d:
                    date_formats["OTHER"] += 1
                    if len(bad_date_examples) < 50:
                        bad_date_examples.append({"file": path.name, "row": i, "date": d})
                else:
                    date_formats["EMPTY"] += 1

                for field in ("Genre", "Singer", "Label", "Master"):
                    raw = (row.get(field, "") or "").strip()
                    if raw:
                        variants[field][canonical_text(raw)][raw] += 1

                for token in split_people((row.get("Composer", "") or "").strip()):
                    variants["Composer_token"][canonical_text(token)][token] += 1
                for token in split_people((row.get("Author", "") or "").strip()):
                    variants["Author_token"][canonical_text(token)][token] += 1

                strict_key = (
                    canonical_text(row.get("Orchestra", "")),
                    canonical_text(row.get("Title", "")),
                    canonical_text(row.get("Date", "")),
                    canonical_text(row.get("Singer", "")),
                )
                if all(strict_key):
                    strict_groups[strict_key].append((i, row))

                year = extract_year(d)
                fuzzy_key = (
                    canonical_text(row.get("Orchestra", "")),
                    canonical_text(row.get("Title", "")),
                    year,
                )
                if all(fuzzy_key):
                    fuzzy_groups[fuzzy_key].append((i, row))

            for key, items in strict_groups.items():
                if len(items) > 1:
                    # "true" if all fields match exactly.
                    records = [x[1] for x in items]
                    true_dup = all(r == records[0] for r in records[1:])
                    if true_dup:
                        strict_dup.append(
                            {
                                "file": path.name,
                                "row_numbers": [x[0] for x in items],
                                "orchestra": items[0][1]["Orchestra"],
                                "title": items[0][1]["Title"],
                                "date": items[0][1]["Date"],
                                "singer": items[0][1]["Singer"],
                            }
                        )

            for key, items in fuzzy_groups.items():
                if len(items) > 1:
                    # Track likely "same title/year" duplicates when singer/date differ.
                    singers = sorted({(x[1].get("Singer", "") or "").strip() for x in items})
                    dates = sorted({(x[1].get("Date", "") or "").strip() for x in items})
                    if len(singers) > 1 or len(dates) > 1:
                        title_orchestra_year_dup.append(
                            {
                                "file": path.name,
                                "orchestra": items[0][1]["Orchestra"],
                                "title": items[0][1]["Title"],
                                "year": key[2],
                                "count": len(items),
                                "singers": singers[:10],
                                "dates": dates[:10],
                            }
                        )

    def summarize_variant_map(vmap: dict, limit: int = 80) -> list[dict]:
        out = []
        for norm, raw_counter in vmap.items():
            if len(raw_counter) > 1:
                out.append(
                    {
                        "canonical_key": norm,
                        "variants": raw_counter.most_common(),
                        "total": sum(raw_counter.values()),
                    }
                )
        out.sort(key=lambda x: x["total"], reverse=True)
        return out[:limit]

    result = {
        "files_scanned": len(files),
        "rows_scanned": rows_total,
        "header_mismatches": header_mismatches,
        "date_formats": dict(date_formats),
        "bad_date_examples": bad_date_examples,
        "empty_cells_by_column": dict(empty_rates),
        "unknown_cells_by_column": dict(unknown_cells),
        "weird_char_cells_by_column": dict(weird_char_cells),
        "variant_groups": {
            "Genre": summarize_variant_map(variants["Genre"]),
            "Singer": summarize_variant_map(variants["Singer"]),
            "Label": summarize_variant_map(variants["Label"]),
            "Master": summarize_variant_map(variants["Master"]),
            "Composer_token": summarize_variant_map(variants["Composer_token"]),
            "Author_token": summarize_variant_map(variants["Author_token"]),
        },
        "true_duplicate_groups": strict_dup,
        "fuzzy_title_orchestra_year_groups": sorted(
            title_orchestra_year_dup, key=lambda x: x["count"], reverse=True
        )[:200],
    }
    return result


def main() -> int:
    csv_dir = Path("cleaned_v2")
    if not csv_dir.exists():
        raise SystemExit("cleaned_v2 not found")

    result = audit(csv_dir)
    Path("thorough_audit_report.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"files_scanned {result['files_scanned']}")
    print(f"rows_scanned {result['rows_scanned']}")
    print(f"header_mismatches {len(result['header_mismatches'])}")
    print("date_formats", result["date_formats"])
    print(f"true_duplicate_groups {len(result['true_duplicate_groups'])}")
    print(
        f"fuzzy_title_orchestra_year_groups {len(result['fuzzy_title_orchestra_year_groups'])}"
    )
    print("variant_group_counts", {k: len(v) for k, v in result["variant_groups"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
