#!/usr/bin/env python3
"""Run data-quality checks on tango discography CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple


CANONICAL_COLUMNS = [
    "Bandleader",
    "Orchestra",
    "Title",
    "Alternative_Title",
    "Genre",
    "Date",
    "Singer",
    "Composer",
    "Author",
    "Label",
    "Medium",
    "Arranger",
    "Grouping",
    "Piano",
    "Bassist",
    "Bandoneons",
    "Strings",
    "Other",
]

REQUIRED_COLUMNS = ["Bandleader", "Orchestra", "Title", "Genre", "Date"]

HEADER_ALIASES = {
    "AltTitle": "Alternative_Title",
    "OrchestraSub": "Orchestra",
    "Master": "Medium",
    "Pianist": "Piano",
    "Bandoneon": "Bandoneons",
}

DATE_PATTERNS = (
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
    re.compile(r"^\d{4}$"),
)

UNKNOWN_TOKENS = {"unknown", "unknwon", "unnamed", "unnamed student"}


@dataclass
class FileIssueSummary:
    row_count: int = 0
    original_headers: List[str] = field(default_factory=list)
    normalized_headers: List[str] = field(default_factory=list)
    blank_header_count: int = 0
    duplicate_header_count: int = 0
    extra_columns: List[str] = field(default_factory=list)
    missing_required_columns: List[str] = field(default_factory=list)
    missing_optional_columns: List[str] = field(default_factory=list)
    required_value_gaps: Counter = field(default_factory=Counter)
    bad_dates: List[Tuple[int, str]] = field(default_factory=list)
    padded_values: int = 0
    possible_duplicates: List[Tuple[int, str, str, str, str]] = field(default_factory=list)
    unknown_token_cells: int = 0
    genre_variants: Counter = field(default_factory=Counter)
    singer_variants: Counter = field(default_factory=Counter)


def normalize_header(header: str) -> str:
    stripped = (header or "").strip()
    return HEADER_ALIASES.get(stripped, stripped)


def looks_like_valid_date(value: str) -> bool:
    value = value.strip()
    return any(pattern.match(value) for pattern in DATE_PATTERNS)


def norm_key(value: str) -> str:
    return (value or "").strip().lower()


def scan_file(path: Path) -> FileIssueSummary:
    summary = FileIssueSummary()

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        original_headers = list(reader.fieldnames or [])
        normalized_headers = [normalize_header(h) for h in original_headers]
        summary.original_headers = original_headers
        summary.normalized_headers = normalized_headers

        summary.blank_header_count = sum(1 for h in normalized_headers if not h)
        header_counts = Counter(normalized_headers)
        summary.duplicate_header_count = sum(1 for _, c in header_counts.items() if c > 1)

        normalized_header_set = {h for h in normalized_headers if h}
        summary.extra_columns = sorted(
            [h for h in normalized_header_set if h not in CANONICAL_COLUMNS]
        )
        summary.missing_required_columns = sorted(
            [c for c in REQUIRED_COLUMNS if c not in normalized_header_set]
        )
        summary.missing_optional_columns = sorted(
            [c for c in CANONICAL_COLUMNS if c not in normalized_header_set and c not in REQUIRED_COLUMNS]
        )

        seen_keys = set()

        for row_index, row in enumerate(reader, start=2):
            summary.row_count += 1

            normalized_row: Dict[str, str] = {}
            for raw_key, raw_value in row.items():
                if raw_key is None:
                    continue
                key = normalize_header(raw_key)
                value = raw_value or ""
                normalized_row[key] = value
                if value != value.strip():
                    summary.padded_values += 1
                token = value.strip().lower()
                if token in UNKNOWN_TOKENS:
                    summary.unknown_token_cells += 1

            for required in REQUIRED_COLUMNS:
                if not normalized_row.get(required, "").strip():
                    summary.required_value_gaps[required] += 1

            date_value = normalized_row.get("Date", "").strip()
            if date_value and not looks_like_valid_date(date_value):
                summary.bad_dates.append((row_index, date_value))

            genre = normalized_row.get("Genre", "").strip()
            if genre:
                summary.genre_variants[genre] += 1

            singer = normalized_row.get("Singer", "").strip()
            if singer:
                summary.singer_variants[singer] += 1

            dedupe_key = (
                norm_key(normalized_row.get("Orchestra", "")),
                norm_key(normalized_row.get("Title", "")),
                norm_key(date_value),
                norm_key(singer),
            )
            if all(dedupe_key):
                if dedupe_key in seen_keys:
                    summary.possible_duplicates.append(
                        (
                            row_index,
                            normalized_row.get("Title", "").strip(),
                            date_value,
                            normalized_row.get("Orchestra", "").strip(),
                            singer,
                        )
                    )
                else:
                    seen_keys.add(dedupe_key)

    return summary


def grouped_case_variants(counter: Counter) -> Dict[str, List[str]]:
    # Report lowercased keys that have mixed-casing/source variants across files.
    groups: Dict[str, List[str]] = defaultdict(list)
    for key in counter:
        groups[key.lower()].append(key)
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


def build_report(results: Dict[str, FileIssueSummary]) -> Dict[str, object]:
    schema_patterns = Counter(tuple(v.normalized_headers) for v in results.values())

    totals = {
        "files": len(results),
        "rows": sum(v.row_count for v in results.values()),
        "schema_variants": len(schema_patterns),
        "files_with_schema_mismatch": sum(
            1
            for v in results.values()
            if v.blank_header_count
            or v.duplicate_header_count
            or v.extra_columns
            or v.missing_required_columns
        ),
        "total_padded_values": sum(v.padded_values for v in results.values()),
        "total_bad_dates": sum(len(v.bad_dates) for v in results.values()),
        "total_possible_duplicates": sum(len(v.possible_duplicates) for v in results.values()),
        "total_unknown_tokens": sum(v.unknown_token_cells for v in results.values()),
    }

    files = {}
    for filename, summary in results.items():
        files[filename] = {
            "row_count": summary.row_count,
            "blank_header_count": summary.blank_header_count,
            "duplicate_header_count": summary.duplicate_header_count,
            "extra_columns": summary.extra_columns,
            "missing_required_columns": summary.missing_required_columns,
            "missing_optional_columns": summary.missing_optional_columns,
            "required_value_gaps": dict(summary.required_value_gaps),
            "padded_values": summary.padded_values,
            "bad_dates_sample": summary.bad_dates[:10],
            "possible_duplicates_sample": summary.possible_duplicates[:10],
            "unknown_token_cells": summary.unknown_token_cells,
        }

    genre_counter = Counter()
    singer_counter = Counter()
    for summary in results.values():
        genre_counter.update(summary.genre_variants)
        singer_counter.update(summary.singer_variants)

    recommendations = []
    if totals["schema_variants"] > 1:
        recommendations.append(
            "Standardize all files to one canonical header order and names; map aliases "
            "(AltTitle->Alternative_Title, OrchestraSub->Orchestra, Master->Medium, "
            "Pianist->Piano, Bandoneon->Bandoneons) and remove blank trailing columns."
        )
    if totals["total_bad_dates"]:
        recommendations.append(
            "Normalize Date to either YYYY or M/D/YYYY. Convert partial/placeholder values "
            "(for example YYYY-MM, YYYY-00-00) to a consistent unknown-date policy."
        )
    if totals["total_padded_values"]:
        recommendations.append(
            "Trim whitespace in all cells to avoid false duplicate mismatches and dirty output."
        )
    if totals["total_possible_duplicates"]:
        recommendations.append(
            "Review duplicate recordings identified by (Orchestra, Title, Date, Singer) "
            "and mark intentional duplicates explicitly if they should be preserved."
        )
    if totals["total_unknown_tokens"]:
        recommendations.append(
            "Replace placeholder names such as UNKNOWN/UNKNWON/UNNAMED with a single standard token."
        )

    return {
        "totals": totals,
        "schema_patterns": [
            {"count": count, "header": list(header)}
            for header, count in schema_patterns.most_common()
        ],
        "files": files,
        "genre_case_variants": grouped_case_variants(genre_counter),
        "singer_case_variants": grouped_case_variants(singer_counter),
        "recommendations": recommendations,
    }


def print_report(report: Dict[str, object]) -> None:
    totals = report["totals"]
    print("Data Quality Report")
    print("===================")
    print(f"Files scanned: {totals['files']}")
    print(f"Rows scanned: {totals['rows']}")
    print(f"Schema variants: {totals['schema_variants']}")
    print(f"Files with schema mismatch: {totals['files_with_schema_mismatch']}")
    print(f"Padded values: {totals['total_padded_values']}")
    print(f"Unexpected date formats: {totals['total_bad_dates']}")
    print(f"Possible duplicate rows: {totals['total_possible_duplicates']}")
    print(f"Unknown placeholder cells: {totals['total_unknown_tokens']}")

    print("\nTop schema patterns:")
    for pattern in report["schema_patterns"][:5]:
        print(f"- {pattern['count']} file(s): {', '.join(pattern['header'])}")

    files_with_major_issues = []
    for filename, info in report["files"].items():
        if (
            info["blank_header_count"]
            or info["duplicate_header_count"]
            or info["extra_columns"]
            or info["missing_required_columns"]
            or info["bad_dates_sample"]
            or info["possible_duplicates_sample"]
        ):
            files_with_major_issues.append((filename, info))

    if files_with_major_issues:
        print("\nFiles needing attention:")
        for filename, info in sorted(files_with_major_issues):
            chunks = []
            if info["blank_header_count"]:
                chunks.append(f"blank headers={info['blank_header_count']}")
            if info["duplicate_header_count"]:
                chunks.append(f"duplicate headers={info['duplicate_header_count']}")
            if info["extra_columns"]:
                chunks.append(f"extra columns={info['extra_columns']}")
            if info["missing_required_columns"]:
                chunks.append(f"missing required={info['missing_required_columns']}")
            if info["bad_dates_sample"]:
                chunks.append(f"bad dates={len(info['bad_dates_sample'])}+")
            if info["possible_duplicates_sample"]:
                chunks.append(f"possible duplicates={len(info['possible_duplicates_sample'])}+")
            print(f"- {filename}: " + "; ".join(chunks))

    if report["recommendations"]:
        print("\nRecommended updates:")
        for item in report["recommendations"]:
            print(f"- {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check data quality of CSV metadata files.")
    parser.add_argument(
        "--csv-dir",
        default="csv_files",
        help="Directory containing CSV files (default: csv_files).",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional output path to write machine-readable JSON report.",
    )
    parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="Exit with code 1 when major issues are found.",
    )
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    if not csv_dir.exists() or not csv_dir.is_dir():
        raise SystemExit(f"CSV directory not found: {csv_dir}")

    files = sorted(csv_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"No CSV files found in: {csv_dir}")

    results = {path.name: scan_file(path) for path in files}
    report = build_report(results)

    print_report(report)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON report written to: {out_path}")

    if args.fail_on_issues:
        totals = report["totals"]
        major_issue_count = (
            totals["files_with_schema_mismatch"]
            + totals["total_bad_dates"]
            + totals["total_possible_duplicates"]
        )
        if major_issue_count > 0:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
