#!/usr/bin/env python3
"""Normalize per-artist CSV files in csv_files/ to the canonical schema.

Rewrites every csv_files/*.csv in place with:
- canonical 20-column header order
- header aliases resolved (Disk/Catalog/Record number/Matrix_Number/Alt_Title)
- dropped columns removed (Side, Source, blank-name columns)
- whitespace trimmed, BOM/replacement chars stripped from cell values
- unknown placeholders cleared in person fields
- Spanish title case applied to Title, AltTitle, Orchestra, and person fields
- accent split-variants resolved across the corpus (accented form wins)

Idempotent: a second run on the normalized output produces no diff.

Usage:
    python normalize_csvs.py
"""

from __future__ import annotations

import csv
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path


CANONICAL_HEADERS = [
    "Bandleader", "Orchestra", "Date", "Title", "AltTitle", "Genre",
    "Singer", "Label", "Master", "Matrix", "Disc", "Composer", "Author",
    "Arranger", "Grouping", "Pianist", "Bassist", "Bandoneons", "Strings",
    "Lineup",
]

HEADER_ALIASES = {
    "Disk": "Disc",
    "Disk_Number": "Disc",
    "DiskNumber": "Disc",
    "Catalog": "Disc",
    "Record number": "Disc",
    "Matrix_Number": "Matrix",
    "Alt_Title": "AltTitle",
}

DROPPED_COLUMNS = {"Side", "Source"}

UNKNOWN_TOKENS = {
    "unknown", "unknwon", "unnamed", "unnamed student",
    "n/a", "na", "[unknown]", "?",
}

UNKNOWN_FIELDS = {"Singer", "Composer", "Author", "Arranger"}

TITLE_CASE_FIELDS = {"Title", "AltTitle", "Orchestra"}
PERSON_NAME_FIELDS = {"Singer", "Composer", "Author", "Arranger"}
CASE_FIELDS = TITLE_CASE_FIELDS | PERSON_NAME_FIELDS

LIST_FIELDS = {"Composer", "Author", "Arranger", "Bandoneons", "Strings"}

VARIANT_FIELDS = ("Singer", "Composer", "Author", "Arranger")

CONNECTIVES = {
    "de", "del", "la", "las", "los", "el", "un", "una",
    "y", "e", "a", "en", "con", "su",
}

WEIRD_CHARS = ("﻿", "�")
WHITESPACE_RE = re.compile(r"\s+")


def trim(value: str) -> str:
    for ch in WEIRD_CHARS:
        if ch in value:
            value = value.replace(ch, "")
    value = unicodedata.normalize("NFC", value)
    value = WHITESPACE_RE.sub(" ", value)
    return value.strip()


def is_unknown(value: str) -> bool:
    return value.strip().lower() in UNKNOWN_TOKENS


OPENER_CHARS = "([{¡¿\"'"


def _capitalize_first_alpha(word: str) -> str:
    """Capitalize the first alphabetic character in a word.

    Handles tokens like `(jiron`, `[gardel`, `"nuevo`, `¿quien` — the opener
    character stays, but the next letter gets capitalized.
    """
    out = []
    capitalized = False
    for ch in word:
        if not capitalized and ch.isalpha():
            out.append(ch.upper())
            capitalized = True
        elif capitalized:
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def title_word(word: str) -> str:
    if not word:
        return word
    if len(word) >= 3 and word[0].lower() == "d" and word[1] == "'":
        rest = word[2:]
        return "D'" + rest[0].upper() + rest[1:].lower()
    if word[0] in OPENER_CHARS:
        return _capitalize_first_alpha(word)
    return word[0].upper() + word[1:].lower()


def title_word_hyphenated(word: str) -> str:
    """Title-case a word, handling hyphenated segments like 'Wayne-Rogelio'."""
    if "-" in word and not word.startswith("-") and not word.endswith("-"):
        return "-".join(title_word(part) if part else part for part in word.split("-"))
    return title_word(word)


def _starts_after_opener(word: str) -> bool:
    """True if word starts with an opener char like '(' or '[' — these reset
    word-position so the next letter should be capitalized regardless of the
    connectives rule."""
    return bool(word) and word[0] in OPENER_CHARS


def title_case_spanish(text: str, person_name: bool = False) -> str:
    """Apply Spanish title case to a string.

    If person_name=True, every word is capitalized — particles like 'de', 'del'
    inside surnames stay capitalized (e.g. 'Julio De Caro', 'Alfredo De Angelis').
    Otherwise interior connectives are lowercased per Spanish title-case
    conventions (e.g. 'Milonga de Mis Amores').
    """
    if not text:
        return text
    words = text.split()
    n = len(words)
    out = []
    for i, word in enumerate(words):
        is_interior = (i != 0 and i != n - 1)
        bare = word.lower().strip(".,;:!?\"()[]{}¡¿")
        if (
            not person_name
            and is_interior
            and bare in CONNECTIVES
            and not word.endswith(".")
            and "'" not in word
            and "-" not in word
            and not _starts_after_opener(word)
        ):
            out.append(word.lower())
        else:
            out.append(title_word_hyphenated(word))
    return " ".join(out)


def normalize_list(value: str, apply_case: bool, person_name: bool) -> str:
    parts = [trim(p) for p in value.split(",")]
    parts = [p for p in parts if p]
    if apply_case:
        parts = [title_case_spanish(p, person_name=person_name) for p in parts]
    return ", ".join(parts)


def normalize_cell(field: str, value: str) -> str:
    value = trim(value)
    if not value:
        return ""
    if field in UNKNOWN_FIELDS and is_unknown(value):
        return ""
    person_name = field in PERSON_NAME_FIELDS
    if field in LIST_FIELDS:
        return normalize_list(
            value,
            apply_case=(field in CASE_FIELDS),
            person_name=person_name,
        )
    if field in CASE_FIELDS:
        return title_case_spanish(value, person_name=person_name)
    return value


def canonical_key(value: str) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


def has_accent(value: str) -> bool:
    decomposed = unicodedata.normalize("NFKD", value)
    return any(unicodedata.combining(c) for c in decomposed)


def collect_variant_map(rows: list[dict[str, str]]) -> dict[tuple[str, str], str]:
    """For each (field, accent-stripped key) where both accented and unaccented
    variants appear in the corpus, pick the majority form. Ties favor the
    accented form. NFC-normalize so e.g. composed-vs-decomposed encodings of
    the same string collapse."""
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        for field in VARIANT_FIELDS:
            cell = row.get(field, "")
            if not cell:
                continue
            for name in (n.strip() for n in cell.split(",")):
                if not name:
                    continue
                name = unicodedata.normalize("NFC", name)
                key = canonical_key(name)
                if not key:
                    continue
                counts[(field, key)][name] += 1
    chosen: dict[tuple[str, str], str] = {}
    for key, forms in counts.items():
        if len(forms) < 2:
            continue
        accented = [name for name in forms if has_accent(name)]
        unaccented = [name for name in forms if not has_accent(name)]
        if accented and unaccented:
            top_accented_count = max(forms[n] for n in accented)
            top_unaccented_count = max(forms[n] for n in unaccented)
            if top_accented_count >= top_unaccented_count:
                chosen[key] = max(accented, key=lambda n: forms[n])
            else:
                chosen[key] = max(unaccented, key=lambda n: forms[n])
    return chosen


def apply_variant_map(
    rows: list[dict[str, str]],
    vmap: dict[tuple[str, str], str],
) -> int:
    fixes = 0
    for row in rows:
        for field in VARIANT_FIELDS:
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [n.strip() for n in cell.split(",")]
            new_parts = []
            changed = False
            for name in parts:
                if not name:
                    continue
                preferred = vmap.get((field, canonical_key(name)))
                if preferred and preferred != name:
                    new_parts.append(preferred)
                    changed = True
                else:
                    new_parts.append(name)
            if changed:
                fixes += 1
                row[field] = ", ".join(new_parts)
    return fixes


def load_file(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    raw_headers = rows[0]
    column_map: list[str | None] = []
    for header in raw_headers:
        clean = (header or "").strip()
        if not clean or clean in DROPPED_COLUMNS:
            column_map.append(None)
            continue
        canonical = HEADER_ALIASES.get(clean, clean)
        if canonical not in CANONICAL_HEADERS:
            column_map.append(None)
            continue
        column_map.append(canonical)

    out = []
    for raw_row in rows[1:]:
        row: dict[str, str] = {h: "" for h in CANONICAL_HEADERS}
        for i, value in enumerate(raw_row):
            if i >= len(column_map):
                break
            target = column_map[i]
            if target is None:
                continue
            row[target] = value or ""
        for field in CANONICAL_HEADERS:
            row[field] = normalize_cell(field, row[field])
        out.append(row)
    return out


def write_file(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in CANONICAL_HEADERS})


def main() -> int:
    root = Path(__file__).parent
    csv_dir = root / "csv_files"
    if not csv_dir.is_dir():
        print(f"error: {csv_dir} not found", file=sys.stderr)
        return 1

    by_file: dict[Path, list[dict[str, str]]] = {}
    all_rows: list[dict[str, str]] = []
    for path in sorted(csv_dir.glob("*.csv")):
        rows = load_file(path)
        by_file[path] = rows
        all_rows.extend(rows)

    vmap = collect_variant_map(all_rows)
    total_variant_fixes = 0
    for rows in by_file.values():
        total_variant_fixes += apply_variant_map(rows, vmap)

    for path, rows in by_file.items():
        write_file(path, rows)

    total_rows = sum(len(r) for r in by_file.values())
    print(f"Normalized {len(by_file)} files, {total_rows} rows.")
    print(f"Accent variant keys merged: {len(vmap)} (applied to {total_variant_fixes} cells).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
