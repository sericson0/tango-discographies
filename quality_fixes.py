#!/usr/bin/env python3
"""Apply targeted data-quality fixes to csv_files/*.csv after normalize_csvs.py.

Each fix is an explicit, auditable transformation. Re-runnable: idempotent
once the corpus is in canonical form.

Run from project root:

    python normalize_csvs.py    # canonical schema + Spanish casing
    python quality_fixes.py     # corrections from the multi-agent audit
    python build.py             # rebuild discographies.csv
"""

from __future__ import annotations

import csv
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path


CANONICAL_HEADERS = [
    "Bandleader", "Orchestra", "Date", "Title", "AltTitle", "Genre",
    "Singer", "Label", "Master", "Matrix", "Disc", "Composer", "Author",
    "Arranger", "Grouping", "Pianist", "Bassist", "Bandoneons", "Strings",
    "Lineup",
]


# ----------------------------------------------------------------------------
# Item 1 — Genre cleanup and row filter
# ----------------------------------------------------------------------------

GENRE_RENAMES = {
    "Milonga Clásica": "Milonga",
    "Milonga clásica": "Milonga",
    "Milonga clasica": "Milonga",
    "Candombe": "Milonga",
    "Valsa": "Vals",
    "Vals Canción": "Vals",
    "Vals canción": "Vals",
    "Vals Cancion": "Vals",
    "Tango Canción": "Tango",
    "Tango canción": "Tango",
    "Tango Cancion": "Tango",
}

GENRE_KEEP_TOKENS = ("tango", "milonga", "vals")


def fix_genres(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int, int]:
    """Apply Genre renames and drop rows whose Genre isn't Tango/Milonga/Vals.

    Empty-Genre rows are kept (no genre claimed)."""
    renames = 0
    out = []
    for row in rows:
        g = row.get("Genre", "").strip()
        if g in GENRE_RENAMES:
            row["Genre"] = GENRE_RENAMES[g]
            renames += 1
            g = row["Genre"]
        if not g:
            out.append(row)
            continue
        gl = g.lower()
        if any(tok in gl for tok in GENRE_KEEP_TOKENS):
            out.append(row)
    return out, renames, len(rows) - len(out)


# ----------------------------------------------------------------------------
# Item 2 — Person-name typo fixes (one-to-one substitutions on whole-cell
# matches OR on individual list elements in comma-separated fields).
# ----------------------------------------------------------------------------

PERSON_TYPO_MAP = {
    # First-pass typo map
    "Erique Lomuto": "Enrique Lomuto",
    "Teofilo Ibañez": "Teófilo Ibáñez",
    "Teófilo Ibanez": "Teófilo Ibáñez",
    "Ascañio Donato": "Ascanio Donato",
    "Ascañio E. Donato": "Ascanio E. Donato",
    "Francisco Pracáníco": "Francisco Pracánico",
    "Pedro Láurenz": "Pedro Laurenz",
    "Floreal Ruíz": "Floreal Ruiz",
    "Rafael Rossí": "Rafael Rossi",
    "Victor Soliño": "Víctor Soliño",
    "Victor Braña": "Víctor Braña",
    "Horacion Salgán": "Horacio Salgán",
    "Vincente Greco": "Vicente Greco",
    "Agusun Bardi": "Agustín Bardi",
    "Agustin Bardi": "Agustín Bardi",
    "Edgardo Donao": "Edgardo Donato",
    "Osavldo Donato": "Osvaldo Donato",
    "Gerardo Malos Rodríguez": "Gerardo Matos Rodríguez",
    "Héctor Varella": "Héctor Varela",
    "Pedro Vergez": "Pedro Vérgez",
    "Próspero Cimaglia": "Próspero Cimaglia",
    "Prospero Cimaglia": "Próspero Cimaglia",
    "José Pecora": "José Pécora",
    "Francisco Bohigas": "Francisco Bohígas",
    "Pablo Hechim": "Pablo Hechím",
    "Eugenio Majul": "Eugenio Majúl",
    "Felix Gutierrez": "Félix Gutiérrez",
    "Santiago Paris": "Santiago París",
    "Domingo Mattio": "Domingo Mattío",
    "Héctor Grane": "Héctor Grané",
    "Miguel Fama": "Miguel Famá",
    "Jerónimo Sureda": "Gerónimo Sureda",
    "Juan Miguel Rodriguez": "Juan Miguel Rodríguez",
    "Andres R. Domenech": "Andrés R. Domenech",
    "Manuel Andres Meaños": "Manuel Andrés Meaños",
    "Luis D'andrea": "Luis D'Andrea",
    "Andrés Domenech": "Andrés Doménech",
    "Andres Domenech": "Andrés Doménech",
    "Andres R. Doménech": "Andrés R. Doménech",

    # Second-pass typos
    "Héctor Varelia": "Héctor Varela",
    "Guillermo Barbieri.": "Guillermo Barbieri",
    "Francisco Jímnez": "Francisco Jiménez",
    "Francisco Gimenez": "Francisco Jiménez",
    "Carmelo Muttarelli": "Carmelo Mutarelli",
    "Vicente De Campo": "Vicente Del Campo",
    "Luisdiaz": "Luis Díaz",

    # Second-pass accent overrides (majority was wrong)
    "Americo Bianchi": "Américo Bianchi",
    "Daniel Alvarez": "Daniel Álvarez",
    "Mauricio Mise": "Mauricio Misé",
    "Hipólito Caron": "Hipólito Carón",
    "Carlos Lazzari": "Carlos Lázzari",
    "Claudio Gonzalez": "Claudio González",
    "Francisco Orefice": "Francisco Oréfice",
    "Fernando Suarez Paz": "Fernando Suárez Paz",
    "Alfredo Attadia": "Alfredo Attadía",
    "Domingo Sanchez": "Domingo Sánchez",
    "Victor Lavallén": "Víctor Lavallén",
    "Carmelo Aguila": "Carmelo Águila",
}

PERSON_FIELDS = ("Singer", "Composer", "Author", "Arranger", "Bandleader",
                 "Pianist", "Bassist", "Bandoneons", "Strings")


def fix_person_typos(rows: list[dict[str, str]]) -> int:
    """Rewrite known person-name typos across all person-name fields."""
    changes = 0
    for row in rows:
        for field in PERSON_FIELDS:
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [p.strip() for p in cell.split(",")]
            new_parts = []
            cell_changed = False
            for p in parts:
                if p in PERSON_TYPO_MAP:
                    new_parts.append(PERSON_TYPO_MAP[p])
                    cell_changed = True
                else:
                    new_parts.append(p)
            if cell_changed:
                changes += 1
                row[field] = ", ".join(new_parts)
    return changes


# ----------------------------------------------------------------------------
# Item 2 (cont.) — Capitalize surname particles like "de Caro" -> "De Caro"
# in ALL fields (not just person-name fields, because Orchestra column also
# contains surnames).
# ----------------------------------------------------------------------------

PARTICLE_SURNAMES = (
    ("de Angelis", "De Angelis"),
    ("de Caro", "De Caro"),
    ("del Bagno", "Del Bagno"),
    ("de Franco", "De Franco"),
    ("de Lío", "De Lío"),
    ("di Sarli", "Di Sarli"),
    ("d'Arienzo", "D'Arienzo"),
    ("d'Agostino", "D'Agostino"),
    ("de Grandis", "De Grandis"),
    ("de los Hoyos", "De Los Hoyos"),
    ("de las", "De Las"),
    # Second-pass additions (multi-word particles)
    ("de la Fuente", "De La Fuente"),
    ("de la Plaza", "De La Plaza"),
    ("del Curto", "Del Curto"),
    ("de Vivo", "De Vivo"),
)


def fix_particle_surnames(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in CANONICAL_HEADERS:
            cell = row.get(field, "")
            if not cell:
                continue
            new = cell
            for lower, upper in PARTICLE_SURNAMES:
                new = new.replace(lower, upper)
            if new != cell:
                changes += 1
                row[field] = new
    return changes


# ----------------------------------------------------------------------------
# Item 2 (cont.) — Categorical vocabulary fixes (Genre/Master typos)
# ----------------------------------------------------------------------------

CATEGORY_FIXES = {
    "Master": {
        "Accoustic": "Acoustic",
    },
    "Genre": {
        "Foxtrot": "Fox Trot",
        "Fox trot": "Fox Trot",
        "Paso doble": "Pasodoble",
        "Paso Doble": "Pasodoble",
        "Cancion": "Canción",
        "Rabchera": "Ranchera",
        "Cifra Gauc": "Cifra Gaucha",
        "Polca Humoristica": "Polca Humorística",
        "Tango campero": "Tango Campero",
        "Vals criollo": "Vals Criollo",
    },
    "Label": {
        "Odeon": "Odeón",
        "Victor": "Víctor",
        "TK": "T.K.",
        "Disco TK": "T.K.",
    },
}


def fix_category_typos(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field, mapping in CATEGORY_FIXES.items():
            cell = row.get(field, "").strip()
            if cell in mapping:
                row[field] = mapping[cell]
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Item 2 (cont.) — Master column misfiled with catalog numbers in Pedro Maffia
# ----------------------------------------------------------------------------

def fix_maffia_master_field(rows: list[dict[str, str]], filename: str) -> int:
    """In Pedro Maffia.csv, the Master column has catalog values like
    'Brunswick 41336', 'Record 6018-A', etc. — should be '78rpm'.
    Detect any Master value that isn't a known medium and rewrite to '78rpm'."""
    if filename != "Pedro Maffia.csv":
        return 0
    valid_masters = {
        "78rpm", "45rpm", "33rpm", "LP", "CD", "Tape", "Tape-S",
        "Acoustic", "Micro", "Microfón", "Mastered",
    }
    changes = 0
    for row in rows:
        m = row.get("Master", "").strip()
        if not m:
            continue
        if m in valid_masters:
            continue
        # Any other value in Master in this file is a misfiled catalog number
        row["Master"] = "78rpm"
        changes += 1
    return changes


# ----------------------------------------------------------------------------
# Item 3 — Date normalization
# ----------------------------------------------------------------------------

DATE_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
DATE_DMY_DASH = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
DATE_YEAR = re.compile(r"^(\d{4})$")
DATE_YYYY_MM = re.compile(r"^(\d{4})-(\d{2})$")
DATE_YYYY_MM_DD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATE_M_YYYY = re.compile(r"^(\d{1,2})/(\d{4})$")
DATE_YYYY_SEASON = re.compile(r"^(\d{4})\s+(Spring|Summer|Fall|Autumn|Winter)$", re.I)
DATE_MULTI_DAY = re.compile(r"^(\d{1,2})-(\d{1,2})/(\d{1,2})/(\d{4})$")  # 22-23/11/1974
DATE_SPANISH_MONTH = re.compile(r"^(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\s+(\d{4})$", re.I)

SPANISH_MONTH_NUM = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def normalize_date(value: str, day_first: bool = False) -> str:
    """Convert a date value to one of: '', YYYY, YYYY-MM, YYYY-MM-DD.

    If day_first is True (Carlos Gardel file), interpret slash dates as D/M/YYYY.
    Otherwise interpret as M/D/YYYY (US convention used in the rest of the corpus)
    unless the first component is >12, in which case swap.
    Free-text dates that can't be cleanly parsed are returned unchanged.
    """
    if not value:
        return ""
    value = value.strip()
    if not value:
        return ""

    m = DATE_YYYY_MM_DD.match(value)
    if m:
        return value

    m = DATE_YYYY_MM.match(value)
    if m:
        return value

    m = DATE_YEAR.match(value)
    if m:
        return value

    m = DATE_MDY.match(value)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if day_first:
            day, month = a, b
        else:
            if a > 12 and b <= 12:
                day, month = a, b
            else:
                month, day = a, b
        try:
            return _format_ymd(y, month, day)
        except ValueError:
            return value

    m = DATE_DMY_DASH.match(value)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return _format_ymd(y, mo, d)
        except ValueError:
            return value

    m = DATE_M_YYYY.match(value)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}"

    m = DATE_YYYY_SEASON.match(value)
    if m:
        return m.group(1)

    m = DATE_MULTI_DAY.match(value)
    if m:
        d, _, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            return _format_ymd(y, mo, d)
        except ValueError:
            return value

    m = DATE_SPANISH_MONTH.match(value)
    if m:
        month_num = SPANISH_MONTH_NUM[m.group(1).lower()]
        return f"{int(m.group(2)):04d}-{month_num:02d}"

    # Heuristic fallback for free-text dates like "¿1937?", "before 1/2/1941",
    # "between 5/10/1939 and 7/12/1939", "14/4/1929 ó 19/4/1929".
    # Extract the first complete M/D/YYYY or D/M/YYYY substring if present;
    # otherwise extract the first 4-digit year.
    embedded_mdy = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if embedded_mdy:
        a = int(embedded_mdy.group(1))
        b = int(embedded_mdy.group(2))
        y = int(embedded_mdy.group(3))
        if day_first:
            day, month = a, b
        elif a > 12 and b <= 12:
            day, month = a, b
        else:
            month, day = a, b
        try:
            return _format_ymd(y, month, day)
        except ValueError:
            pass

    embedded_year = re.search(r"(\d{4})", value)
    if embedded_year:
        y = int(embedded_year.group(1))
        if 1850 <= y <= 2030:
            return embedded_year.group(1)

    return value


def _format_ymd(y: int, month: int, day: int) -> str:
    if not (1 <= month <= 12):
        raise ValueError(f"month {month} out of range")
    if not (1 <= day <= 31):
        raise ValueError(f"day {day} out of range")
    if not (1850 <= y <= 2030):
        raise ValueError(f"year {y} out of range")
    return f"{y:04d}-{month:02d}-{day:02d}"


def fix_dates(rows: list[dict[str, str]], filename: str) -> tuple[int, list[tuple[int, str, str]]]:
    """Normalize dates. Returns (count of changes, unparseable list)."""
    day_first = (filename == "Carlos Gardel.csv")
    changes = 0
    unparseable: list[tuple[int, str, str]] = []
    for i, row in enumerate(rows, start=2):
        old = row.get("Date", "")
        new = normalize_date(old, day_first=day_first)
        if new != old:
            row["Date"] = new
            changes += 1
        if old and not (DATE_YEAR.match(new) or DATE_YYYY_MM.match(new) or DATE_YYYY_MM_DD.match(new)):
            unparseable.append((i, old, new))
    return changes, unparseable


# ----------------------------------------------------------------------------
# Item 5 — Composer/Author to First + Last
# ----------------------------------------------------------------------------

# Compound surnames to preserve intact when stripping middle names.
COMPOUND_SURNAMES = {
    "matos rodríguez", "matos rodriguez",
    "coria peñaloza", "coria penaloza",
    "geroni flores",
    "marambio catán", "marambio catan",
    "scarpino caldarella",  # if appears as one
}

# Particles that, when they appear interior, mean the surname continues
# (so we should keep everything from the particle onward).
NAME_PARTICLES = {"de", "del", "di", "la", "las", "los", "della", "dal", "da", "van", "von", "le"}


def reduce_to_first_last(name: str) -> str:
    """Reduce a single name to first + last (or first + particle-surname).

    Examples:
        'Pedro Mario Maffia' -> 'Pedro Maffia'
        'Juan Andrés Caruso' -> 'Juan Caruso'
        'Alfredo De Angelis' -> 'Alfredo De Angelis' (particle interior, keep all)
        'Gerardo Hernán Matos Rodríguez' -> 'Gerardo Matos Rodríguez' (compound surname preserved)
        'R. Leopoldo Thompson' -> 'R. Thompson' (initial counts as first)
    """
    words = name.split()
    n = len(words)
    if n < 3:
        return name

    # If any interior word is a particle, keep first + (from first particle through last)
    for i in range(1, n - 1):
        if words[i].lower() in NAME_PARTICLES:
            return " ".join([words[0]] + words[i:])

    # If the last two words form a known compound surname, keep first + last two
    last_two = " ".join(words[-2:]).lower()
    if last_two in COMPOUND_SURNAMES:
        return " ".join([words[0]] + words[-2:])

    # Default: first + last
    return f"{words[0]} {words[-1]}"


def fix_first_last_in_credits(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in ("Composer", "Author"):
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [p.strip() for p in cell.split(",")]
            new_parts = [reduce_to_first_last(p) for p in parts]
            new = ", ".join(p for p in new_parts if p)
            if new != cell:
                changes += 1
                row[field] = new
    return changes


# ----------------------------------------------------------------------------
# Item 6 — Strip Piazzolla footnote markers (* runs in Title)
# ----------------------------------------------------------------------------

FOOTNOTE_TRAIL_RE = re.compile(r"\*+\s*$")


def strip_footnote_markers(rows: list[dict[str, str]], filename: str) -> int:
    if filename != "Astor Piazzolla.csv":
        return 0
    changes = 0
    for row in rows:
        for field in ("Title", "AltTitle"):
            v = row.get(field, "")
            new = FOOTNOTE_TRAIL_RE.sub("", v).rstrip()
            if new != v:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Item 7 — Drop trailing ?/! from Title and AltTitle
# ----------------------------------------------------------------------------

QUESTION_EXCLAM_TAIL_RE = re.compile(r"[¿¡]?\s*[?!]+\s*$")


def drop_question_exclam(rows: list[dict[str, str]]) -> int:
    """Strip trailing ? and ! (and leading ¿/¡ when used as Spanish opening
    punctuation matched with a trailing close mark)."""
    changes = 0
    for row in rows:
        for field in ("Title", "AltTitle"):
            v = row.get(field, "")
            if not v:
                continue
            new = QUESTION_EXCLAM_TAIL_RE.sub("", v).rstrip()
            # Also drop opening ¿/¡ if title started with one (Spanish convention)
            if new.startswith("¿") or new.startswith("¡"):
                new = new[1:].lstrip()
                # Re-capitalize the first letter
                if new and new[0].isalpha():
                    new = new[0].upper() + new[1:]
            if new != v:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Item 8 — Delete web-scrape junk rows from Lomuto
# ----------------------------------------------------------------------------

LOMUTO_JUNK_TITLES = {"Suscribite Al 667%", "Suscribite Al ,"}


def delete_lomuto_junk(rows: list[dict[str, str]], filename: str) -> tuple[list[dict[str, str]], int]:
    if filename != "Francisco Lomuto.csv":
        return rows, 0
    kept = []
    dropped = 0
    for row in rows:
        if row.get("Title", "").strip() in LOMUTO_JUNK_TITLES:
            dropped += 1
            continue
        kept.append(row)
    return kept, dropped


# ----------------------------------------------------------------------------
# Item 9 — Normalize De Angelis Grouping prefix to YY-YY format
# ----------------------------------------------------------------------------

DE_ANGELIS_GROUPING_FIXES = {
    "Dante + Martel Duo (1944-50)": "44-50 Dante + Martel Duo",
    "Before Dante (1943-44)": "43-44 Before Dante",
    "Solo Piano": "Solo Piano",  # no date available — keep
    "Rizzo 1st bando, Cinosi 1st violin (1968-77)": "68-77 Rizzo 1st bando, Cinosi 1st violin",
}


def normalize_de_angelis_grouping(rows: list[dict[str, str]], filename: str) -> int:
    if filename != "Alfredo De Angelis.csv":
        return 0
    changes = 0
    for row in rows:
        g = row.get("Grouping", "").strip()
        if g in DE_ANGELIS_GROUPING_FIXES:
            new = DE_ANGELIS_GROUPING_FIXES[g]
            if new != g:
                row["Grouping"] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Item 10 — Flip "Last, First" -> "First Last" in Lomuto and OTV
# ----------------------------------------------------------------------------

# Single-element values that match "Last, First" inside a single cell are
# tricky because comma is also the list separator. We detect them by checking
# whether a cell has exactly two comma-separated parts AND the second part
# looks like a first name (starts capital, no spaces beyond initials).
# We restrict to a known list found in the audit to avoid false positives.

LASTFIRST_FLIPS = {
    "Boutelje, Phil": "Phil Boutelje",
    "Clemente, Virgilio San": "Virgilio San Clemente",
    "Daugherty, Doc": "Doc Daugherty",
    "Donaldson, Walter": "Walter Donaldson",
    "Hargreaves, Robert": "Robert Hargreaves",
    "Kalmar, Bert": "Bert Kalmar",
    "Mcdermott, Louis William": "Louis William McDermott",
    "Mchugh, Jimmy": "Jimmy McHugh",
    "Maizani, Azucena": "Azucena Maizani",
    "Pidemunt, Alberto": "Alberto Pidemunt",
    "Young, Victor": "Víctor Young",
}


def fix_lastfirst_order(rows: list[dict[str, str]], filename: str) -> int:
    if filename not in {"Francisco Lomuto.csv", "Orquesta Típica Victor.csv"}:
        return 0
    changes = 0
    for row in rows:
        for field in ("Composer", "Author", "Singer", "Arranger"):
            cell = row.get(field, "")
            if not cell:
                continue
            for old, new in LASTFIRST_FLIPS.items():
                if cell == old:
                    row[field] = new
                    changes += 1
                    break
    return changes


# ----------------------------------------------------------------------------
# Item 11 — Standardize joint-credit separators to commas
# ----------------------------------------------------------------------------

JOINT_CREDIT_FIELDS = ("Composer", "Author", "Arranger")


def standardize_credit_separators(rows: list[dict[str, str]]) -> int:
    """Replace `;` separator with `,` in composer/author/arranger cells.
    Also flatten `Firstname Lastname-Firstname Lastname` to comma-separated when
    BOTH halves look like distinct people (two capitalized words on each side).
    """
    changes = 0
    pair_pattern = re.compile(
        r"^([A-ZÁÉÍÓÚÑÜ][\w'.]*(?:\s+[A-ZÁÉÍÓÚÑÜ][\w'.]*)+)-([A-ZÁÉÍÓÚÑÜ][\w'.]*(?:\s+[A-ZÁÉÍÓÚÑÜ][\w'.]*)+)$"
    )
    for row in rows:
        for field in JOINT_CREDIT_FIELDS:
            cell = row.get(field, "")
            if not cell:
                continue
            new = cell

            # Replace ; with , (with optional surrounding whitespace)
            if ";" in new:
                new = re.sub(r"\s*;\s*", ", ", new)

            # Detect single-cell hyphen-joined pairs of full names
            if "," not in new and ";" not in new:
                m = pair_pattern.match(new.strip())
                if m:
                    new = f"{m.group(1)}, {m.group(2)}"

            # Collapse multi-space introduced by replacements
            new = re.sub(r"\s+", " ", new).strip()
            # Re-tighten ", " spacing
            new = re.sub(r",\s*", ", ", new)
            if new != cell:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Title-level typo cleanup
# ----------------------------------------------------------------------------

TITLE_TYPO_MAP = {
    "Un Cambio Te Viene Bién": "Un Cambio Te Viene Bien",
    "Mama Iévame P'al Pueblo": "Mamá Llévame Pa'l Pueblo",
}


def fix_title_typos(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        t = row.get("Title", "")
        if t in TITLE_TYPO_MAP:
            row["Title"] = TITLE_TYPO_MAP[t]
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Title accent restorations — words almost always accented in Spanish
# ----------------------------------------------------------------------------

# Pairs of (unaccented, accented) — applied as word-boundary replacements
# in Title and AltTitle. Cell-level exclusions handle English contexts like
# `April In Paris`.
TITLE_ACCENT_FIXES = [
    ("Corazon", "Corazón"), ("corazon", "corazón"),
    ("Bandoneon", "Bandoneón"), ("bandoneon", "bandoneón"),
    ("Cancion", "Canción"), ("cancion", "canción"),
    ("Adios", "Adiós"), ("adios", "adiós"),
    ("Maria", "María"),
    ("Otono", "Otoño"), ("otono", "otoño"),
    ("Porteno", "Porteño"), ("porteno", "porteño"),
    ("Quien", "Quién"), ("quien", "quién"),
    ("Mama", "Mamá"),
    ("Tambien", "También"), ("tambien", "también"),
    ("Pasion", "Pasión"), ("pasion", "pasión"),
    ("Ilusion", "Ilusión"), ("ilusion", "ilusión"),
    ("Traicion", "Traición"), ("traicion", "traición"),
    ("Evocacion", "Evocación"), ("evocacion", "evocación"),
    # Single-character "Dia"/"dia" needs more care — match only word-boundary
    ("Dia", "Día"), ("dia", "día"),
]

TITLE_ACCENT_EXCLUDE_CELLS = {
    "April In Paris",  # English title — paris stays unaccented
    # Spurious-accent reversal:
}

# Reverse: where an over-accented form snuck in
TITLE_REVERSE_ACCENTS = [
    ("Víctoria", "Victoria"),  # Victoria takes no accent
]


def fix_title_accents(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in ("Title", "AltTitle"):
            v = row.get(field, "")
            if not v:
                continue
            if v in TITLE_ACCENT_EXCLUDE_CELLS:
                continue
            new = v
            for old, repl in TITLE_ACCENT_FIXES:
                new = re.sub(rf"\b{re.escape(old)}\b", repl, new)
            # `paris` -> `parís` (proper noun, both cases)
            new = re.sub(r"\bParis\b", "París", new)
            new = re.sub(r"\bparis\b", "parís", new)
            # Reverse spurious accents
            for old, repl in TITLE_REVERSE_ACCENTS:
                new = re.sub(rf"\b{re.escape(old)}\b", repl, new)
            if new != v:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Second-pass row bugs
# ----------------------------------------------------------------------------

def fix_pugliese_date_typo(rows: list[dict[str, str]], filename: str) -> int:
    """Pugliese rows with date 1905-06-09 are a 1985 typo (he was born Dec 1905,
    and the surrounding rows are mid-1980s recordings)."""
    if filename != "Osvaldo Pugliese.csv":
        return 0
    changes = 0
    for row in rows:
        if row.get("Date", "") == "1905-06-09":
            row["Date"] = "1985-06-09"
            changes += 1
    return changes


def fix_pugliese_bandleader_leak(rows: list[dict[str, str]], filename: str) -> int:
    if filename != "Osvaldo Pugliese.csv":
        return 0
    changes = 0
    for row in rows:
        b = row.get("Bandleader", "")
        if "con cuerdas arreglos" in b:
            row["Bandleader"] = "Osvaldo Pugliese"
            changes += 1
    return changes


def fix_piazzolla_penal_split(rows: list[dict[str, str]], filename: str) -> int:
    """Repair one row where 'Tango Fever (Penalty)' was split across Title and
    AltTitle: Title='Penal)', AltTitle='Tango Fever (Penalty'."""
    if filename != "Astor Piazzolla.csv":
        return 0
    changes = 0
    for row in rows:
        if row.get("Title") == "Penal)" and row.get("AltTitle") == "Tango Fever (Penalty":
            row["Title"] = "Tango Fever (Penalty)"
            row["AltTitle"] = ""
            changes += 1
    return changes


def fix_darienzo_1928_cluster(rows: list[dict[str, str]], filename: str) -> int:
    """34 rows are dated 1928-01-01 — an artifact of year-only data being
    normalized to Jan 1. Promote back to year-only."""
    if filename != "Juan D'Arienzo.csv":
        return 0
    changes = 0
    for row in rows:
        if row.get("Date") == "1928-01-01":
            row["Date"] = "1928"
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Piazzolla orchestra consolidation
# ----------------------------------------------------------------------------

PIAZZOLLA_ORCHESTRA_MERGES = {
    'Astor Piazzolla y su Quinteto "Nuevo Tango"': "Astor Piazzolla y su Quinteto Tango Nuevo",
    "Astor Piazzolla And The New Tango Quintet (Y su Quinteto Tango Nuevo)": "Astor Piazzolla y su Quinteto Tango Nuevo",
    "Astor Piazzolla & His Orquesta": "Astor Piazzolla y su Orquesta",
    "Astor Piazzolla con Orquesta": "Astor Piazzolla y su Orquesta",
}


def consolidate_piazzolla_orchestra(rows: list[dict[str, str]], filename: str) -> int:
    if filename != "Astor Piazzolla.csv":
        return 0
    changes = 0
    for row in rows:
        o = row.get("Orchestra", "")
        if o in PIAZZOLLA_ORCHESTRA_MERGES:
            row["Orchestra"] = PIAZZOLLA_ORCHESTRA_MERGES[o]
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Instrument-name capitalization inside parens, e.g. (cello) -> (Cello)
# ----------------------------------------------------------------------------

INSTRUMENT_NAMES = [
    "cello", "guitar", "guitarra", "viola", "bass", "piano", "bandoneon",
    "bandoneón", "violin", "violín", "vibrafono", "vibráfono", "recitado",
    "drums", "percussion", "percusion", "percusión", "flute", "flauta",
    "voz", "voice", "arpa", "harp", "arreglos", "cantor", "narrador",
    "saxofon", "saxofón", "trompeta", "trombon", "trombón", "armonica",
    "armónica", "acordeon", "acordeón", "clarinete", "oboe",
]

_INSTRUMENT_PATTERN = re.compile(
    r"\((" + "|".join(re.escape(n) for n in INSTRUMENT_NAMES) + r")\b",
    re.IGNORECASE,
)


def _capitalize_instrument(match: "re.Match[str]") -> str:
    name = match.group(1)
    return "(" + name[0].upper() + name[1:].lower()


def fix_instrument_casing(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in CANONICAL_HEADERS:
            v = row.get(field, "")
            if not v or "(" not in v:
                continue
            new = _INSTRUMENT_PATTERN.sub(_capitalize_instrument, v)
            if new != v:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Close unmatched open-parens in person-name fields
# ----------------------------------------------------------------------------

def fix_unmatched_parens(rows: list[dict[str, str]]) -> int:
    """Close any open-paren-without-close in a single name element of a
    person-name list (e.g. 'Emilio Paiva (Cello' -> 'Emilio Paiva (Cello)')."""
    changes = 0
    person_list_fields = ("Composer", "Author", "Arranger", "Bandoneons", "Strings")
    for row in rows:
        for field in person_list_fields:
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [p.strip() for p in cell.split(",")]
            new_parts = []
            cell_changed = False
            for p in parts:
                opens = p.count("(")
                closes = p.count(")")
                if opens > closes:
                    p = p + ")" * (opens - closes)
                    cell_changed = True
                elif closes > opens:
                    # Strip stray closing parens
                    extra = closes - opens
                    while extra > 0 and p.endswith(")"):
                        p = p[:-1].rstrip()
                        extra -= 1
                    cell_changed = True
                new_parts.append(p)
            if cell_changed:
                changes += 1
                row[field] = ", ".join(new_parts)
    return changes


# ----------------------------------------------------------------------------
# Stray punctuation suffixes in person names — '?', '[?]'
# ----------------------------------------------------------------------------

STRAY_PUNCT_RE = re.compile(r"(\s*\[\??\]|\s*\?+)$")


def fix_person_stray_punct(rows: list[dict[str, str]]) -> int:
    changes = 0
    person_list_fields = ("Composer", "Author", "Arranger", "Bandoneons", "Strings",
                          "Singer", "Pianist", "Bassist", "Bandleader")
    for row in rows:
        for field in person_list_fields:
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [p.strip() for p in cell.split(",")]
            new_parts = []
            cell_changed = False
            for p in parts:
                new_p = STRAY_PUNCT_RE.sub("", p).rstrip()
                if new_p != p:
                    cell_changed = True
                new_parts.append(new_p)
            if cell_changed:
                changes += 1
                row[field] = ", ".join(new_parts)
    return changes


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
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

    summary = Counter()
    unparseable_dates: list[tuple[str, int, str, str]] = []

    for path in sorted(csv_dir.glob("*.csv")):
        rows = load_csv(path)
        filename = path.name

        rows, g_renames, g_dropped = fix_genres(rows)
        summary["genre_renames"] += g_renames
        summary["rows_dropped_non_tango"] += g_dropped

        rows, l_dropped = delete_lomuto_junk(rows, filename)
        summary["lomuto_junk_dropped"] += l_dropped

        summary["person_typos"] += fix_person_typos(rows)
        summary["particle_surnames"] += fix_particle_surnames(rows)
        summary["category_typos"] += fix_category_typos(rows)
        summary["maffia_master"] += fix_maffia_master_field(rows, filename)
        summary["title_typos"] += fix_title_typos(rows)
        summary["title_accents"] += fix_title_accents(rows)

        d_changes, file_unparseable = fix_dates(rows, filename)
        summary["date_normalizations"] += d_changes
        for row_num, old, new in file_unparseable:
            unparseable_dates.append((filename, row_num, old, new))

        # Second-pass row bugs
        summary["pugliese_date_typo"] += fix_pugliese_date_typo(rows, filename)
        summary["pugliese_bandleader_leak"] += fix_pugliese_bandleader_leak(rows, filename)
        summary["piazzolla_penal_split"] += fix_piazzolla_penal_split(rows, filename)
        summary["darienzo_1928_cluster"] += fix_darienzo_1928_cluster(rows, filename)
        summary["piazzolla_orchestra_merged"] += consolidate_piazzolla_orchestra(rows, filename)

        summary["first_last_credits"] += fix_first_last_in_credits(rows)
        summary["piazzolla_footnotes"] += strip_footnote_markers(rows, filename)
        summary["question_exclam_stripped"] += drop_question_exclam(rows)
        summary["de_angelis_grouping"] += normalize_de_angelis_grouping(rows, filename)
        summary["lastfirst_flipped"] += fix_lastfirst_order(rows, filename)
        summary["credit_separators"] += standardize_credit_separators(rows)

        summary["unmatched_parens_fixed"] += fix_unmatched_parens(rows)
        summary["stray_punct_stripped"] += fix_person_stray_punct(rows)
        summary["instrument_capitalized"] += fix_instrument_casing(rows)

        write_csv(path, rows)

    print("Quality fixes applied:")
    for key in [
        "rows_dropped_non_tango",
        "lomuto_junk_dropped",
        "genre_renames",
        "person_typos",
        "particle_surnames",
        "category_typos",
        "maffia_master",
        "title_typos",
        "title_accents",
        "date_normalizations",
        "pugliese_date_typo",
        "pugliese_bandleader_leak",
        "piazzolla_penal_split",
        "darienzo_1928_cluster",
        "piazzolla_orchestra_merged",
        "first_last_credits",
        "piazzolla_footnotes",
        "question_exclam_stripped",
        "de_angelis_grouping",
        "lastfirst_flipped",
        "credit_separators",
        "unmatched_parens_fixed",
        "stray_punct_stripped",
        "instrument_capitalized",
    ]:
        print(f"  {summary[key]:6d}  {key}")

    if unparseable_dates:
        report_path = root / "unparseable_dates.csv"
        with report_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["File", "Row", "OldValue", "OutputValue"])
            for filename, row_num, old, new in unparseable_dates:
                w.writerow([filename, row_num, old, new])
        print(f"\n{len(unparseable_dates)} unparseable date(s) written to unparseable_dates.csv")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
