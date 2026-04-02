#!/usr/bin/env python3
"""Consolidated cleaning pipeline for tango discography CSV files.

Reads raw CSVs from csv_files/, applies all normalization, deduplication,
and enrichment, then writes cleaned output to cleaned/.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────

TARGET_HEADERS = [
    "Bandleader", "Orchestra", "Date", "Title", "AltTitle", "Genre",
    "Singer", "Label", "Master", "Composer", "Author", "Arranger",
    "Grouping", "Pianist", "Bassist", "Bandoneons", "Strings",
]

OUTPUT_HEADERS = TARGET_HEADERS + ["Lineup"]

NAME_FIELDS = ("Composer", "Author", "Singer")
TEXT_FIELDS = [h for h in TARGET_HEADERS if h != "Date"]

HEADER_ALIASES = {
    "Alternative_Title": "AltTitle",
    "Medium": "Master",
    "Piano": "Pianist",
    "Bandoneon": "Bandoneons",
    "OrchestraSub": "Orchestra",
}

MANUAL_CANONICAL_BY_KEY = {
    "various": "Various",
    "juan pacho": "Juan Pacho",
    "luis d abraccio": "D'Abraccio",
    "luis d andrea": "Luis D'Andrea",
    "benjamin tagle lara": "Benjamín Tagle Lara",
    "a timarni": "Antonio Timarni",
    "pancho laguna": "Francisco Lomuto",
    "julian porteno": "Ernesto Temes",
    # Merge middle-name variants
    "juan andres caruso": "Juan Caruso",
    "juan a caruso": "Juan Caruso",
    "celedonio esteban flores": "Celedonio Flores",
    "angel gregorio villoldo": "Ángel Villoldo",
    "julio placido navarrine": "Julio Navarrine",
    "antonio martinez viergol": "Antonio Viérgol",
    "francisco isidro martino": "Francisco Martino",
    "francisco isidro y emilio martino": "Francisco Martino",
    # Initials → full names (high frequency, 5+ occurrences)
    "e delfino": "Enrique Delfino",
    "m romero": "Manuel Romero",
    "v greco": "Vicente Greco",
    "m joves": "Manuel Jovés",
    "j betinotti": "José Betinotti",
    "s castriota": "Samuel Castriota",
    "j maglio": "Juan Maglio",
    "j gonzalez castillo": "José González Castillo",
    "f canaro": "Francisco Canaro",
    "j filiberto": "Juan Filiberto",
    "f lomuto": "Francisco Lomuto",
    "f garcia jimenez": "Francisco García Jiménez",
    "j cobian": "Juan Carlos Cobián",
    "a greco": "Ángel Greco",
    "f schreier": "Francisco Schreier",
    "j caruso": "Juan Caruso",
    "a caruso": "Juan Caruso",
    "a cadicamo": "Antonio Cadícamo",
    "p contursi": "Pascual Contursi",
    "a navarrine": "Alfredo Navarrine",
    "r tuegols": "Rafael Tuegols",
    "p numa cordoba": "Pedro Numa Córdoba",
    "a viergol": "Antonio Viérgol",
    "f martino": "Francisco Martino",
    "a vaccarezza": "Alberto Vaccarezza",
    "r goyeneche": "Roberto Goyeneche",
    "v servetto": "Verminio Servetto",
    "c tapia": "Cristino Tapia",
    "l teisseire": "Luis Teisseire",
    "a vacarezza": "Alberto Vaccarezza",
    "a piancino": "Adolfo Piancino",
    "a ruiz": "Antonio Ruiz",
    "a lopez": "Audín López",
    "a castellanos": "Alberto Castellanos",
    "s gomez": "Sandalio Gómez",
    "p valdez": "Pedro Valdez",
    "j zas": "José Zas",
    "e gonzalez tunon": "Enrique González Tuñón",
    "b musci": "Benjamin Musci",
    "e d angelo": "Ernesto D'Angelo",
    "a villoldo": "Ángel Villoldo",
    "a flores": "Alejandro Flores",
    "a pereyra": "Alejandro Pereyra",
    "a penaloza": "A. Peñaloza",
    # Initials → full names (1-2 occurrences, single candidate)
    "a herschel": "Adolfo Herschel",
    "s linning": "Samuel Linning",
    "e arolas": "Eduardo Arolas",
    "a buglione": "Antonio Buglione",
    "m pardo": "Mario Pardo",
    "j rial": "José Rial",
    "r firpo": "Roberto Firpo",
    "a rosquellas": "Adolfo Rosquellas",
    "a albert": "Arturo Albert",
    "c marcucci": "Carlos Marcucci",
    "e dizeo": "Enrique Dizeo",
    "c camba": "Carlos Camba",
    "d fortunato": "Domingo Fortunato",
    "e bonessi": "Eduardo Bonessi",
    "s piana": "Sebastián Piana",
    "g gatto": "Genaro Gatto",
    "a alguero": "Augusto Algueró",
    "a canale": "Amadeo Canale",
    "a munilla": "Alberto Munilla",
    "a supparo": "Atilio Supparo",
    "a de la torre": "Arturo De La Torre",
    "a calvillo": "Andrés Calvillo",
    "h cristante": "Humberto Cristante",
    "j la via": "José La Vía",
    "l scatasso": "Luis Scatasso",
    "l bernstein": "Luis Bernstein",
    "f garcia": "Francisco García",
    "a gatti": "Afner Gatti",
    "c d amico": "Carmelo D'Amico",
    "c navas": "Cipriano Navas",
    "j marco": "José Marco",
    "f ottaviano": "Francisco Ottaviano",
    "j puentes": "Juan Puentes",
    "j vanni": "Julio Vanni",
    "j de la calle": "Juan De La Calle",
    "m massini": "Miguel Massini",
    "b ochoa": "Benigno Ochoa",
    "e romano": "Eugenio Romano",
    "a molina": "Antonio Molina",
    "o novarro": "Osvaldo Novarro",
    "j martin": "Julio Martín",
    "a seghini": "Alfredo Seghini",
    "d moranese": "Domingo Moranese",
    "a simone": "Amado Simone",
    "c de pardo": "César De Pardo",
    "r alonso": "Restituto Alonso",
    "r de rosa": "Rafael De Rosa",
    "r giovinazzi": "Rafael Giovinazzi",
    "c alvarez pintos": "Carlos Álvarez Pintos",
    "l coraggio": "Luis Coraggio",
    "r duran": "Roberto Durán",
    "l roldan": "Luis Pedro Roldan",
    "p gregorio": "Pascual Gregorio",
    "r aieta": "Ricardo Aieta",
    "m parish": "Mitchel Parish",
    "d atorra": "Diógenes Atorra",
    "m ossi": "Miguel Ossi",
    "e maurno": "Ernesto Maurno",
    "m villanova": "Martín Villanova",
    "m valerga": "Manuel Valerga",
    "a mansilla": "Alberico Mansilla",
    "m mujica": "Milon Mujica",
    "r iturralde": "Ramón Iturralde",
    "a garcia": "Alfonso García",
}

UNKNOWN_TOKENS = {"unknown", "unknwon", "unnamed", "n/a", "?", "#ref!", "[unknown]"}

APOSTROPHE_VARIANTS = {
    "\u2018": "'", "\u2019": "'", "\u0060": "'",
    "\u00b4": "'", "\u0091": "'", "\u0092": "'",
}

MOJIBAKE_FIXES = {
    "V\ufffdctor": "Víctor", "Ode\ufffdn": "Odeón", "Microf\ufffdn": "Microfón",
    "Clar\ufffdn": "Clarín", "M\ufffds": "Más", "Ma\ufffdanitas": "Mañanitas",
    "Jos\ufffd": "José", "\ufffdngel": "Ángel", "Te\ufffdfilo": "Teófilo",
}

TITLE_PUNCT = re.compile(r"[,\.!?¿¡…]")

PARTICLES = {"de", "del", "di", "los", "las", "la"}


# ── Helpers ────────────────────────────────────────────────────────────────

def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def canonical_key(text: str) -> str:
    text = strip_accents((text or "").lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def accent_score(text: str) -> int:
    return sum(1 for c in unicodedata.normalize("NFC", text) if c in "áéíóúÁÉÍÓÚñÑüÜ")


# ── Text Sanitization ─────────────────────────────────────────────────────

def sanitize_text(value: str) -> str:
    if not value:
        return ""
    # Strip BOM and control characters.
    value = value.replace("\ufeff", "").replace("\u00ad", "")
    value = value.replace("\x93", '"').replace("\x94", '"').replace("\x96", "-")
    # Normalize apostrophes and quotes.
    for src, dst in APOSTROPHE_VARIANTS.items():
        value = value.replace(src, dst)
    value = value.replace("\u201c", '"').replace("\u201d", '"')
    # Repair mojibake.
    for bad, good in MOJIBAKE_FIXES.items():
        value = value.replace(bad, good)
    value = re.sub(r"(?<=\w)\ufffd(?=\w)", "'", value)
    value = value.replace("\ufffd", "")
    # Attempt latin-1/cp1252 mojibake reversal.
    if any(ch in value for ch in ("\u00c3", "\u00c2", "\u00d0", "\u00f0")):
        for enc in ("latin-1", "cp1252"):
            try:
                candidate = value.encode(enc).decode("utf-8")
                if candidate:
                    value = candidate
                    break
            except UnicodeError:
                pass
    # Fix common misspellings.
    value = re.sub(r"\bUNKNWON\b", "UNKNOWN", value, flags=re.IGNORECASE)
    # Semicolons to commas, normalize whitespace.
    value = value.replace(";", ",")
    return " ".join(value.split()).strip()


# ── Field-Specific Cleaning ───────────────────────────────────────────────

def strip_parenthetical(token: str) -> str:
    """Strip parenthetical content from names (aliases, nicknames, etc.)."""
    result = re.sub(r"\s*\([^)]*\)", "", token).strip()
    return result if result else token


def capitalize_particles(name: str) -> str:
    """Capitalize particles (de, del, di, etc.) in Argentine convention."""
    words = name.split()
    for i, word in enumerate(words):
        if i > 0 and word.lower() in PARTICLES:
            words[i] = word.capitalize()
    return " ".join(words)


def strip_orphan_parens(token: str) -> str:
    """Remove unmatched parentheses from a name token."""
    opens = token.count("(")
    closes = token.count(")")
    if opens == closes:
        return token
    if opens == 0 and closes > 0:
        return token.replace(")", "")
    if closes == 0 and opens > 0:
        return token.replace("(", "")
    return token


def clean_name_cell(value: str) -> str:
    if not value:
        return ""
    # Normalize slash and dash separators to commas before splitting
    value = value.replace(" / ", ", ").replace("/", ", ")
    value = value.replace(" - ", ", ")
    tokens = [t.strip() for t in value.split(",")]
    cleaned = []
    for token in tokens:
        if not token:
            continue
        token = token.replace('"', "")
        # Fix colon used as period in initials (e.g., "A: Caruso" → "A. Caruso")
        token = re.sub(r"^([A-Z]): ", r"\1. ", token)
        # Skip unknown/placeholder tokens
        if token.strip().lower() in UNKNOWN_TOKENS:
            continue
        # Strip parenthetical content (aliases, nicknames, etc.)
        token = strip_parenthetical(token)
        # Strip orphan parentheses
        token = strip_orphan_parens(token)
        token = re.sub(r"(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])", " ", token)
        token = " ".join(token.split())
        # Capitalize particles in Argentine style
        token = capitalize_particles(token)
        if token:
            cleaned.append(token)
    return ", ".join(cleaned)


def normalize_date(value: str) -> str:
    value = (value or "").strip()
    m = re.fullmatch(r"(\d{4})-00-00", value)
    return m.group(1) if m else value


SKIP_GENRES = {"ranchera", "estilo"}

GENRE_OVERRIDES = {
    "t d fango": "Tango",
    "t c fango": "Tango",
    "un nuevo ritmo para toda orquesta": "Tango",
    "candombe": "Milonga",
    "marcha candombe": "Milonga",
    "marcha candombe motivo popular": "Milonga",
}


def normalize_genre(value: str) -> str:
    key = canonical_key(value)
    # Single-char corruption (lone quote, etc.)
    if len(value.strip()) <= 1 and not value.strip().isalpha():
        return ""
    override = GENRE_OVERRIDES.get(key)
    if override:
        return override
    if "vals" in key:
        return "Vals"
    if "milonga" in key:
        return "Milonga"
    if "tango" in key:
        return "Tango"
    if "candombe" in key:
        return "Milonga"
    return value


def normalize_label(value: str) -> str:
    key = canonical_key(value)
    if key == "music hall":
        return "Music Hall"
    if key == "microfon":
        return "Microfón"
    if key == "bmg rca":
        return "BMG RCA"
    if key == "art fono":
        return "Art Fono"
    if key == "victor":
        return "Víctor"
    if key == "bmg victor japan":
        return "BMG Víctor Japan"
    if key.startswith("odeon"):
        return value.replace("Odeon", "Odeón")
    return value


def clean_title(value: str) -> str:
    if not value:
        return ""
    # Strip three-dot sequences first (handles mid-word like "C...ara" -> "Cara")
    value = value.replace("...", "")
    # Strip remaining single punctuation characters
    value = TITLE_PUNCT.sub("", value)
    return " ".join(value.split())


# ── Header Mapping ─────────────────────────────────────────────────────────

def map_row(raw_row: dict[str, str], fieldnames: list[str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for raw_key in fieldnames:
        clean_key = raw_key.strip()
        canonical = HEADER_ALIASES.get(clean_key, clean_key)
        if canonical in TARGET_HEADERS:
            mapped[canonical] = (raw_row.get(raw_key, "") or "").strip()
    return mapped


# ── Variant Canonicalization ───────────────────────────────────────────────

def choose_canonical(variants: set[str], counts: Counter) -> str:
    def sort_key(v: str) -> tuple[int, int, int, int, str]:
        a = accent_score(v)
        u = sum(1 for c in v if c.isalpha() and c.isupper())
        return (a, u, counts[v], len(v), v)
    candidate = sorted(variants, key=sort_key, reverse=True)[0]
    manual = MANUAL_CANONICAL_BY_KEY.get(canonical_key(candidate))
    return manual or candidate


# ── Lineup Builder ─────────────────────────────────────────────────────────

def parse_musicians(cell_value: str) -> list[tuple[str, str | None]]:
    if not cell_value or cell_value.strip().lower() in ("", "nan"):
        return []
    musicians = []
    for entry in cell_value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        match = re.search(r"\(([^)]+)\)\s*$", entry)
        if match:
            musicians.append((entry, match.group(1).strip().title()))
        else:
            musicians.append((entry, None))
    return musicians


def build_lineup(row: dict[str, str]) -> str:
    parts: list[str] = []
    others: dict[str, int] = {}

    def add_other(instrument: str) -> None:
        others[instrument] = others.get(instrument, 0) + 1

    # Piano
    if (row.get("Pianist") or "").strip():
        parts.append("Piano")

    # Bass
    bass_count = 0
    for _, instrument in parse_musicians(row.get("Bassist", "")):
        if instrument is None:
            bass_count += 1
        else:
            add_other(instrument)
    if bass_count == 1:
        parts.append("Bass")
    elif bass_count > 1:
        parts.append(f"{bass_count} Bass")

    # Bandoneons
    bandoneon_count = 0
    for _, instrument in parse_musicians(row.get("Bandoneons", "")):
        if instrument is None:
            bandoneon_count += 1
        else:
            add_other(instrument)
    if bandoneon_count == 1:
        parts.append("Bandoneon")
    elif bandoneon_count > 1:
        parts.append(f"{bandoneon_count} Bandoneons")

    # Violins
    violin_count = 0
    for _, instrument in parse_musicians(row.get("Strings", "")):
        if instrument is None:
            violin_count += 1
        else:
            add_other(instrument)
    if violin_count == 1:
        parts.append("Violin")
    elif violin_count > 1:
        parts.append(f"{violin_count} Violins")

    # Other instruments
    for instrument, count in others.items():
        parts.append(instrument if count == 1 else f"{count} {instrument}")

    return ", ".join(parts)


# ── Main Pipeline ──────────────────────────────────────────────────────────

def main() -> int:
    root = Path(".")
    src_dir = root / "csv_files"
    out_dir = root / "cleaned"
    out_dir.mkdir(exist_ok=True)

    csv_files = sorted(src_dir.glob("*.csv"))
    if not csv_files:
        raise SystemExit("No CSV files found in csv_files/")

    # ── Phase 1: Read all files, sanitize cells, collect name-token variants ──

    file_rows: dict[str, list[tuple[int, dict[str, str]]]] = {}
    name_token_counts: dict[str, Counter] = {f: Counter() for f in NAME_FIELDS}
    name_token_variants: dict[str, dict[str, set[str]]] = {
        f: defaultdict(set) for f in NAME_FIELDS
    }

    for path in csv_files:
        rows: list[tuple[int, dict[str, str]]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []

            for row_num, raw_row in enumerate(reader, start=2):
                mapped = map_row(raw_row, fieldnames)
                normalized: dict[str, str] = {}

                for h in TARGET_HEADERS:
                    val = sanitize_text(mapped.get(h, ""))
                    if h == "Date":
                        normalized[h] = normalize_date(mapped.get(h, ""))
                    elif h == "Genre":
                        normalized[h] = normalize_genre(val)
                    elif h == "Label":
                        normalized[h] = normalize_label(val)
                    elif h in ("Title", "AltTitle"):
                        normalized[h] = clean_title(val)
                    elif h in NAME_FIELDS:
                        normalized[h] = clean_name_cell(val)
                    else:
                        normalized[h] = val

                    if h != "Date" and normalized[h].strip().lower() in UNKNOWN_TOKENS:
                        normalized[h] = "UNKNOWN"

                # Skip rows with excluded genres.
                if canonical_key(normalized.get("Genre", "")) in SKIP_GENRES:
                    continue

                # Collect name-field token variants.
                for field in NAME_FIELDS:
                    if normalized[field]:
                        for token in (
                            t.strip()
                            for t in normalized[field].split(",")
                            if t.strip()
                        ):
                            name_token_counts[field][token] += 1
                            name_token_variants[field][canonical_key(token)].add(token)

                rows.append((row_num, normalized))
        file_rows[path.name] = rows

    # ── Phase 2: Apply name-token canonicalization, collect whole-value variants ──

    name_token_mapping: dict[str, dict[str, str]] = {f: {} for f in NAME_FIELDS}
    name_variant_groups: dict[str, list] = {f: [] for f in NAME_FIELDS}

    for field in NAME_FIELDS:
        for key, variants in name_token_variants[field].items():
            if len(variants) <= 1:
                continue
            canonical = choose_canonical(variants, name_token_counts[field])
            for v in variants:
                name_token_mapping[field][v] = canonical
            name_variant_groups[field].append({
                "canonical_key": key,
                "canonical": canonical,
                "variants": sorted(variants),
                "counts": {
                    v: name_token_counts[field][v] for v in sorted(variants)
                },
            })

    # Apply name-token mapping in-place and collect whole-value variants.
    field_variant_counters: dict[str, dict[str, Counter]] = {
        f: defaultdict(Counter) for f in TEXT_FIELDS
    }

    for indexed_rows in file_rows.values():
        for _, row in indexed_rows:
            for field in NAME_FIELDS:
                if not row[field]:
                    continue
                tokens = [t.strip() for t in row[field].split(",") if t.strip()]
                mapped = []
                for token in tokens:
                    value = name_token_mapping[field].get(token, token)
                    value = MANUAL_CANONICAL_BY_KEY.get(
                        canonical_key(value), value
                    )
                    mapped.append(value)
                row[field] = ", ".join(mapped)

            for h in TEXT_FIELDS:
                v = row[h]
                if v:
                    field_variant_counters[h][canonical_key(v)][v] += 1

    # Build whole-value mappings for all text fields.
    field_mappings: dict[str, dict[str, str]] = {f: {} for f in TEXT_FIELDS}
    for field in TEXT_FIELDS:
        for key, counter in field_variant_counters[field].items():
            if len(counter) > 1:
                canonical = choose_canonical(set(counter.keys()), counter)
                for v in counter:
                    field_mappings[field][v] = canonical

    # ── Phase 3: Apply whole-value mappings, deduplicate, add lineup, write ──

    duplicate_review: list[dict] = []
    true_duplicates_removed = 0

    for filename, indexed_rows in file_rows.items():
        # Apply whole-value mappings.
        for _, row in indexed_rows:
            for h in TEXT_FIELDS:
                row[h] = field_mappings[h].get(row[h], row[h])
                if row[h].strip().lower() in UNKNOWN_TOKENS:
                    row[h] = "UNKNOWN"

        # Deduplicate.
        grouped: dict[tuple, list] = defaultdict(list)
        for row_num, row in indexed_rows:
            key = (
                row["Orchestra"].strip().lower(),
                row["Title"].strip().lower(),
                row["Date"].strip().lower(),
                row["Singer"].strip().lower(),
            )
            if all(key):
                grouped[key].append((row_num, row))

        drop_row_numbers: set[int] = set()
        for _, items in grouped.items():
            if len(items) <= 1:
                continue
            full_rows = [item[1] for item in items]
            first = full_rows[0]
            is_true_dup = all(r == first for r in full_rows[1:])
            differing: list[dict] = []
            if not is_true_dup:
                for h in TARGET_HEADERS:
                    vals = sorted({r[h] for r in full_rows})
                    if len(vals) > 1:
                        differing.append({"field": h, "values": vals})
            else:
                for idx, _ in items[1:]:
                    drop_row_numbers.add(idx)
                true_duplicates_removed += len(items) - 1

            duplicate_review.append({
                "file": filename,
                "key": {
                    "Orchestra": items[0][1]["Orchestra"],
                    "Title": items[0][1]["Title"],
                    "Date": items[0][1]["Date"],
                    "Singer": items[0][1]["Singer"],
                },
                "row_numbers": [idx for idx, _ in items],
                "count": len(items),
                "true_duplicate": is_true_dup,
                "removed_rows": sorted(
                    drop_row_numbers.intersection({x[0] for x in items})
                ),
                "differing_fields": differing,
            })

        # Build output with Lineup column.
        out_rows: list[dict[str, str]] = []
        for row_num, row in indexed_rows:
            if row_num not in drop_row_numbers:
                row["Lineup"] = build_lineup(row)
                out_rows.append(row)

        with (out_dir / filename).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS)
            writer.writeheader()
            writer.writerows(out_rows)

    # ── Phase 4: Consolidate all cleaned files into discographies.csv ──

    all_rows: list[dict[str, str]] = []
    for cleaned_csv in sorted(out_dir.glob("*.csv")):
        with cleaned_csv.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                all_rows.append(row)

    # Cross-file deduplication: skip rows that are exact duplicates of an
    # already-seen row with the same (Orchestra, Title, Date, Singer) key.
    consolidated: list[dict[str, str]] = []
    seen: dict[tuple, dict[str, str]] = {}
    cross_file_dups = 0
    for row in all_rows:
        key = (
            row.get("Orchestra", "").strip().lower(),
            row.get("Title", "").strip().lower(),
            row.get("Date", "").strip().lower(),
            row.get("Singer", "").strip().lower(),
        )
        if all(key) and key in seen:
            if row == seen[key]:
                cross_file_dups += 1
                continue
        if all(key):
            seen[key] = row
        consolidated.append(row)

    master_path = root / "discographies.csv"
    with master_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS)
        writer.writeheader()
        writer.writerows(consolidated)

    # ── Report ──
    report = {
        "files_processed": len(csv_files),
        "name_variant_groups": name_variant_groups,
        "duplicate_review": duplicate_review,
        "true_duplicates_removed": true_duplicates_removed,
        "cross_file_duplicates_removed": cross_file_dups,
        "consolidated_records": len(consolidated),
    }
    (root / "cleaning_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Processed {len(csv_files)} files into {out_dir}")
    print(f"Composer variant groups: {len(name_variant_groups['Composer'])}")
    print(f"Author variant groups: {len(name_variant_groups['Author'])}")
    print(f"Singer variant groups: {len(name_variant_groups['Singer'])}")
    print(f"Duplicate groups reviewed: {len(duplicate_review)}")
    print(f"True duplicate rows removed: {true_duplicates_removed}")
    print(f"Cross-file duplicates removed: {cross_file_dups}")
    print(f"Consolidated records: {len(consolidated)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
