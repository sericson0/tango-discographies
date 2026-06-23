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

# Image-association columns, round-tripped verbatim. Excluded from
# CANONICAL_HEADERS (and thus from every per-field fix function above) because
# folder/album names must stay literal to match the R2 bucket paths — see the
# same exclusion in normalize_csvs.py's VERBATIM_FIELDS.
IMG_HEADERS = ["Img_Type", "Img_Folder", "Img_Side", "Img_Album"]
ALL_HEADERS = CANONICAL_HEADERS + IMG_HEADERS


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
    # Fourth-pass: sub-variants collapsed to base
    "Vals Americano": "Vals",
    "Vals Criollo": "Vals",
    "Vals criollo": "Vals",
    "Milonga Criolla": "Milonga",
    "Milonga Tangueada": "Milonga",
    "Tango Canyengue": "Tango",
    "Tango Campero": "Tango",
    "Tango campero": "Tango",
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

    # Third-pass: Singer accent fixes
    "Ernesto Fama": "Ernesto Famá",
    "Agustin Irusta": "Agustín Irusta",
    "Hector De Rosas": "Héctor De Rosas",
    "Jose Bohr": "José Bohr",
    "Carlos Roldan": "Carlos Roldán",
    "Aldolfo Rivas": "Adolfo Rivas",
    "Ignacio Diáz": "Ignacio Díaz",
    "Angel Ramos": "Ángel Ramos",

    # Third-pass: Composer typos (edit-distance 1)
    "Roberto Fipo": "Roberto Firpo",
    "Anselmo Aleta": "Anselmo Aieta",
    "Pedro Mafia": "Pedro Maffía",
    "Rodolfo Sciamarella": "Rodolfo Sciammarella",
    "Rafael Rossa": "Rafael Rossi",
    "Enrique Franchini": "Enrique Francini",
    "Horacio Pettorosi": "Horacio Pettorossi",
    "Horacio Pettoross": "Horacio Pettorossi",
    "Luis Berstein": "Luis Bernstein",
    "Armando Balioti": "Armando Baliotti",
    "Luis Ricardi": "Luis Riccardi",
    "Carlos Vivián": "Carlos Viván",
    "Juan Bahuer": "Juan Bauer",
    "Eduardo Arman": "Eduardo Armani",
    "Ángel Vanesi": "Ángel Danesi",
    "Carlos Percuocco": "Carlos Percuoco",
    "Orestes Cóffaro": "Orestes Cófaro",
    "Guillermo Cavarza": "Guillermo Cavazza",
    "Teófilo Yespés": "Teófilo Lespés",
    "Alejandro Junissi": "Alejandro Junnissi",
    "Domingo Riverol.": "Domingo Riverol",
    "Ángel Riverol.": "Ángel Riverol",

    # Third-pass: Author typos
    "José Suñe": "José Suñé",
    "José Riu": "José Riú",
    "Víctorino Velázquez": "Victorino Velázquez",
    "Haydee Cardón": "Haydeé Cardón",
    "Víctor Spindola": "Víctor Spíndola",
    "Juan Carabillo": "Juan Carabilló",
    "Nestor Vargas": "Néstor Vargas",
    "Jose Cousani": "José Cousani",
    "Andres Giachino": "Andrés Giachino",
    "Jose Gil": "José Gil",
    "Victor Lambertucci": "Víctor Lambertucci",

    # Third-pass: initials -> full (high-confidence single-occurrence cases)
    "F. Canaro": "Francisco Canaro",
    "O. Fresedo": "Osvaldo Fresedo",
    "A. Aieta": "Anselmo Aieta",
    "A. Bardi": "Agustín Bardi",
    "P. Maffia": "Pedro Maffía",
    "F. Pracanico": "Francisco Pracánico",
    "J. Servidio": "José Servidio",
    "C. Posadas": "Carlos Posadas",
    "J. Pollero": "Julio Pollero",
    "S. París": "Santiago París",
    "R. Blanco": "Roberto Blanco",
    "A.j. Tagini": "Armando Tagini",
    "C. Flores": "Celedonio Flores",
    "A. Tagini": "Armando Tagini",
    "J. Staffolani": "José Staffolani",
    "P. Cordoba": "Pedro Córdoba",
    "L. Herrera": "Luis Herrera",
    "L. De Biase": "Luis De Biase",
    "A. Bustamante": "Arturo Bustamante",
    "E. Fresedo": "Emilio Fresedo",
    "A. Novion": "Alberto Novión",
    "E.cadicamo": "Enrique Cadícamo",
    "Rogelio Cadicamo": "Rogelio Cadícamo",
    "P. Etchegoyen": "Pedro Etchegoyen",
    "A. Chimenti": "Armando Chimenti",
    "A. Canto": "Alberto Canto",
    "J. Martinez": "José Martínez",
    "J. Ruffet": "José Ruffet",

    # Third-pass: compound-surname recovery (information lost in First+Last reduction)
    "Julio Caro": "Julio De Caro",
    "Carlos Sarli": "Carlos Di Sarli",
    "Raúl Hoyos": "Raúl De Los Hoyos",
    "Francisco Caro": "Francisco De Caro",
    "Arturo Bassi": "Arturo De Bassi",
    "Graciano Leone": "Graciano De Leone",
    "Alfredo Pera": "Alfredo Le Pera",
    "Ernesto Cruz": "Ernesto De La Cruz",
    "Alfredo Franco": "Alfredo De Franco",
    "Eduardo Piano": "Eduardo Del Piano",
    "José Grandis": "José De Grandis",
    "Carlos Riestra": "Carlos De La Riestra",
    "Oscar Fuente": "Oscar De La Fuente",
    "Pascual Gullo": "Pascual De Gullo",
    "Guillermo Ciancio": "Guillermo Del Ciancio",
    "Herminia Rossano": "Herminia De Rossano",
    "Alejandro Barrio": "Alejandro Del Barrio",
    "Eduardo Labar": "Eduardo De Labar",
    "Rafael Bagno": "Rafael Del Bagno",
    "José Pilato": "José Di Pilato",
    "Jaime Hoyos": "Jaime De Los Hoyos",
    "Gerardo Rodríguez (Cumparsita)": "Gerardo Matos Rodríguez",

    # Third-pass: corrupted multi-token (item 11)
    "Adolfo De Adolfo Alejandro Pérez": "Alejandro Pérez",

    # Fourth-pass: supporting-role typos (Bandoneons/Strings/Bassist)
    "Ernesto De Cicco": "Ernesto Di Cicco",
    "Felipe Ricciardi": "Felipe Riccardo",
    "Felipe Riccardi": "Felipe Riccardo",
    "Federico Scorticatti": "Federico Scorticati",
    "Eduardo Cortti": "Eduardo Corti",
    "Bernard Weber": "Bernardo Weber",
    "Andriano Fanelli": "Adriano Fanelli",
    "Antinio Agri": "Antonio Agri",
    "Bernardo Germinio": "Bernardo Germino",
    "Pedro Belluatim": "Pedro Belluati",
    "Atilio Carral": "Atilio Corral",
    "Alfredo Sciarreta": "Alfredo Sciarretta",
    "Vicente Sicarretta": "Vicente Sciarretta",
    "Enrique Marcheto": "Enrique Marchetto",
    "Quicho Diaz": "Kicho Diaz",
    "Nerón Ferrazano": "Nerón Ferrazzano",
    "Francisco Sammartino": "Francisco Sanmartino",
    "Angel Scichetti": "Ángel Cichetti",

    # Fourth-pass: accent overrides for supporting roles
    "Angel Genta": "Ángel Genta",
    "Andres Rivas": "Andrés Rivas",
    "Jose Bragato": "José Bragato",
    "Jose Nieso": "José Nieso",
    "Victor Felice": "Víctor Felice",
    "Simon Bajour": "Simón Bajour",
    "Hector Del Curto": "Héctor Del Curto",
    "Cayetano Camara": "Cayetano Cámara",
    "Nestor Panik": "Néstor Panik",
    "Claudio Gonzales": "Claudio González",
    "Fernando Rodriguez": "Fernando Rodríguez",

    # Fourth-pass: data-leak fixes
    "Ma+O158:O177rio Monteleone": "Mario Monteleone",

    # Fourth-pass: Cyrillic letter contamination
    "Rodolfo Alchourrоn": "Rodolfo Alchourrón",
    "Rodolfo Alchourrон": "Rodolfo Alchourrón",
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
# Singer joiner normalization + last-name expansion
# ----------------------------------------------------------------------------

# Last-name shorthand seen in slash-joined Singer cells (e.g. Anibal Troilo
# file lists pairs of his singers as `Fiorentino / Mandarino`). Expand to
# full names; uncertain ones (Cárdenas, Rufino, Calderón) are left unexpanded
# for manual review.
SINGER_LAST_NAME_EXPANSIONS = {
    "Fiorentino": "Francisco Fiorentino",
    "Mandarino": "Roberto Mandarino",
    "Marino": "Alberto Marino",
    "Ruiz": "Floreal Ruiz",
    "Rivero": "Edmundo Rivero",
    "Casal": "Jorge Casal",
    "Berón": "Raúl Berón",
    "Goyeneche": "Roberto Goyeneche",
}


def normalize_singer_joiners(rows: list[dict[str, str]]) -> int:
    """Normalize Singer joiners to lowercase ' y '. Replaces capital ' Y ',
    ' & ', and ' / '. After joiner normalization, also expand last-name
    shorthand using SINGER_LAST_NAME_EXPANSIONS where possible."""
    changes = 0
    for row in rows:
        s = row.get("Singer", "")
        if not s:
            continue
        new = s
        # Capital Y between words → lowercase y
        new = re.sub(r"\s+Y\s+", " y ", new)
        # Ampersand → y
        new = re.sub(r"\s*&\s*", " y ", new)
        # Slash → y
        new = re.sub(r"\s*/\s*", " y ", new)
        # Collapse double spaces
        new = re.sub(r"\s+", " ", new).strip()

        # Expand single-word names to full names if recognized
        # Split on " y " and ", " to get individual singer tokens
        tokens = re.split(r"\s+y\s+|,\s+", new)
        expanded = []
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            if tok in SINGER_LAST_NAME_EXPANSIONS:
                expanded.append(SINGER_LAST_NAME_EXPANSIONS[tok])
            else:
                expanded.append(tok)
        # Rejoin with ' y ' (drop any commas — assume all separators are 'y')
        if len(expanded) > 1:
            new = " y ".join(expanded)
        else:
            new = expanded[0] if expanded else new

        if new != s:
            row["Singer"] = new
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Non-singer leaks in Singer column → blank
# ----------------------------------------------------------------------------

# Values applied AFTER joiner normalization (so capital Y is already lowercase y).
SINGER_NON_SINGER_LEAK_VALUES = {
    "Armando Pontier y Su Orquesta Típica",
    "Héctor Varela y Su Orquesta",
    "Orquesta",
    "Orquesta Típica Criolla",
    "Coro",
    "Con Coro",
    "Estribillo",
    "Con Estribillo",
    "Estribillo Coreado",
    "Estribillo y Coro",
    "Dirección Orquestal De Armando Pontier",
    "Cantor y Cantora Sin Identificar y Coro Del Teatro Nacional",
}


def blank_singer_non_singer_leaks(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        s = row.get("Singer", "").strip()
        if s in SINGER_NON_SINGER_LEAK_VALUES:
            row["Singer"] = ""
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Singer: strip glosa annotations and standardize Instrumental
# ----------------------------------------------------------------------------

GLOSA_SUFFIX_RE = re.compile(
    r"\s+(?:y\s+)?(?:Glosas?|Coplas|Pregón|Estribillo Recitado|Con Palabras De|Recitado)\s+(?:De|Por)\s+.+$",
    re.IGNORECASE,
)
GLOSA_ONLY_RE = re.compile(
    r"^(?:Glosas?|Coplas|Pregón|Estribillo Recitado|Recitado)\s+(?:De|Por)\s+.+$",
    re.IGNORECASE,
)
RECITADO_VARIANT_RE = re.compile(r"^Instrumental\s*\(\s*Con\s*Recitado\s*\)\s*$", re.IGNORECASE)


def fix_glosa_and_instrumental(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        s = row.get("Singer", "")
        if not s:
            continue
        new = s.strip()
        # If the whole cell is just a glosa annotation, blank it
        if GLOSA_ONLY_RE.match(new):
            new = ""
        else:
            # Otherwise strip trailing glosa annotation
            new = GLOSA_SUFFIX_RE.sub("", new).rstrip()
        # Normalize Instrumental (Con Recitado) → Instrumental
        if RECITADO_VARIANT_RE.match(new):
            new = "Instrumental"
        # Also strip trailing " y Coro" suffix from valid singer names
        new = re.sub(r"\s+y\s+Coro\s*$", "", new, flags=re.IGNORECASE).rstrip()
        if new != s:
            row["Singer"] = new
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Recover Instrumentals where empty Singer + era clearly implies instrumental
# ----------------------------------------------------------------------------

def recover_instrumentals(rows: list[dict[str, str]], filename: str) -> int:
    """For known files+eras where empty Singer means instrumental, fill in.

    - Juan Carlos Cobain.csv: all 1922 rows are pre-singer era (Cobián was
      leading instrumental orchestras then). 48 empty cells recoverable.
    - Horacio Salgan.csv: 1963-64 empty Singer rows are instrumentals.
    """
    if filename not in {"Juan Carlos Cobain.csv", "Horacio Salgan.csv"}:
        return 0
    changes = 0
    for row in rows:
        if row.get("Singer", "").strip():
            continue
        d = row.get("Date", "")
        if filename == "Juan Carlos Cobain.csv" and d.startswith("1922"):
            row["Singer"] = "Instrumental"
            changes += 1
        elif filename == "Horacio Salgan.csv" and (d.startswith("1963") or d.startswith("1964")):
            row["Singer"] = "Instrumental"
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Orchestra canonical merges (cross-file)
# ----------------------------------------------------------------------------

ORCHESTRA_CANONICAL_MAP = {
    # Pedro Maffia
    "Orquesta Tipica Maffia": "Orquesta Pedro Maffia",
    # Juan Maglio
    "Orquesta Típica Maglio": "Orquesta Juan Maglio",
    # José Basso
    "Jose Basso y su Orquesta": "Orquesta José Basso",
    # Roberto Firpo
    "Orquesta de Roberto Firpo": "Orquesta Roberto Firpo",
    "Orchestra Típica Argentina Roberto Firpo": "Orquesta Argentina Roberto Firpo",
    # Astor Piazzolla — drop quotes around "Nuevo Octeto" (consistent with
    # already-applied Quinteto consolidation)
    'Astor Piazzolla y su "Nuevo Octeto"': "Astor Piazzolla y su Nuevo Octeto",
}


def apply_orchestra_canonical(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        o = row.get("Orchestra", "")
        if o in ORCHESTRA_CANONICAL_MAP:
            row["Orchestra"] = ORCHESTRA_CANONICAL_MAP[o]
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Singer-name leaks in Orchestra column → blank
# ----------------------------------------------------------------------------

SINGER_LEAKS_IN_ORCHESTRA = {
    "Armando Pontier.csv": {
        "Roberto Goyeneche", "Rubén Juárez", "Hugo del Carril",
        "Hugo Del Carril",
    },
    "Astor Piazzolla.csv": {
        "Amelita Baltar", "Jairo", "Mina", "Georges Moustaki",
        "Ney Matogrosso", "Marie-Paule Belle",
    },
    "Francisco Lomuto.csv": {
        "Tito Schipa", "Carmencita del Moral", "Carmencita Del Moral",
    },
    "Orquesta Típica Victor.csv": {
        "Mercedes Simone", "Alberto Gómez",
    },
}


def blank_orchestra_singer_leaks(rows: list[dict[str, str]], filename: str) -> int:
    leaks = SINGER_LEAKS_IN_ORCHESTRA.get(filename)
    if not leaks:
        return 0
    changes = 0
    for row in rows:
        if row.get("Orchestra", "") in leaks:
            row["Orchestra"] = ""
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Composer/Author junk cleanup
# ----------------------------------------------------------------------------

JUNK_VALUES_IN_CREDITS = {
    "Varios", "Various", "A:", "Letra", "F", "D.r.",
    "Eduardo Discos, Casetes] Sadaic]",
    "Enrique [Otra]",
}

CREDIT_PREFIX_STRIPS = [
    re.compile(r"^Vers\.:\s*", re.IGNORECASE),
    re.compile(r"^Música:\s*", re.IGNORECASE),
    re.compile(r"^Letras?:\s*", re.IGNORECASE),
    re.compile(r"^Música\s+y\s+Letra\s+de\s+", re.IGNORECASE),
]


def clean_credit_junk(rows: list[dict[str, str]]) -> int:
    """Blank junk values in Composer/Author; strip role-prefixes from real names."""
    changes = 0
    for row in rows:
        for field in ("Composer", "Author"):
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [p.strip() for p in cell.split(",")]
            new_parts = []
            cell_changed = False
            for p in parts:
                # Strip role-prefixes ("Vers.: X", "Música: Y")
                for pat in CREDIT_PREFIX_STRIPS:
                    new_p = pat.sub("", p)
                    if new_p != p:
                        p = new_p.strip()
                        cell_changed = True
                if p in JUNK_VALUES_IN_CREDITS:
                    cell_changed = True
                    continue  # drop
                new_parts.append(p)
            new = ", ".join(new_parts)
            if cell_changed or new != cell:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Hyphen-joined credit pairs missed by first-pass split
# ----------------------------------------------------------------------------

HYPHEN_PAIR_FIXES = {
    "Ferrazzano-Pollero": "Ferrazzano, Pollero",
}


def fix_hyphen_credit_pairs(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in ("Composer", "Author"):
            cell = row.get(field, "")
            if not cell:
                continue
            new = cell
            for old, repl in HYPHEN_PAIR_FIXES.items():
                if old in new:
                    new = new.replace(old, repl)
            if new != cell:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Initialism normalization: `J.a. Saavedra` -> `J. A. Saavedra`,
# `T.b.c.` -> `T. B. C.`
# ----------------------------------------------------------------------------

INITIALISM_CHAIN_RE = re.compile(r"\b(?:[A-Za-z]\.){2,}")


def _normalize_initialism(match: "re.Match[str]") -> str:
    s = match.group(0)
    letters = re.findall(r"([A-Za-z])\.", s)
    return ". ".join(letter.upper() for letter in letters) + "."


def fix_dotted_initials(rows: list[dict[str, str]]) -> int:
    """Apply to Composer, Author, Title, AltTitle. Handles arbitrary-length
    initial chains (`T.b.c.` -> `T. B. C.`)."""
    changes = 0
    for row in rows:
        for field in ("Composer", "Author", "Title", "AltTitle"):
            cell = row.get(field, "")
            if not cell or "." not in cell:
                continue
            new = INITIALISM_CHAIN_RE.sub(_normalize_initialism, cell)
            if new != cell:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Title-level cross-file unifications
# ----------------------------------------------------------------------------

TITLE_UNIFICATIONS = {
    "Inspiracion": "Inspiración",
    "Adiós Pampa Mia": "Adiós Pampa Mía",
    "Cuando Volverás": "Cuándo Volverás",
    "El Huracan": "El Huracán",
    "Lagrimas": "Lágrimas",
    "Intimas": "Íntimas",
    "Margo": "Margó",
}


def fix_title_unifications(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in ("Title", "AltTitle"):
            v = row.get(field, "")
            if v in TITLE_UNIFICATIONS:
                row[field] = TITLE_UNIFICATIONS[v]
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Composer/Author column swap for famous tangos (when columns were swapped)
# ----------------------------------------------------------------------------

# Per-Title: which values in Composer column actually belong in Author column,
# and vice versa.
FAMOUS_TITLE_ATTRIBUTIONS = [
    {
        "title": "La Cumparsita",
        "should_be_author": {
            "Pascual Contursi", "Enrique Maroni",
            "Pascual Contursi, Enrique Maroni",
        },
        "should_be_composer": {"Gerardo Matos Rodríguez", "Gerardo Rodríguez"},
    },
    {
        "title": "El Choclo",
        "should_be_author": {"Enrique Santos Discépolo", "Enrique Discépolo"},
        "should_be_composer": {"Ángel Villoldo"},
    },
    {
        "title": "Chiqué",
        "should_be_author": set(),
        "should_be_composer": {"Ricardo Brignolo", "Ricardo Luis Brignolo"},
    },
    {
        "title": "A Media Luz",
        "should_be_author": {"Carlos Lenzi", "Carlos César Lenzi"},
        "should_be_composer": {"Edgardo Donato"},
    },
    {
        "title": "Adiós Muchachos",
        "should_be_author": set(),
        "should_be_composer": {"Julio Sanders"},
    },
]


def fix_famous_title_swaps(rows: list[dict[str, str]]) -> int:
    """For famous tangos, swap Composer/Author when both columns appear to be
    misfiled (composer column has the lyricist, author column has the composer).
    Conservative — only swaps when at least one side is clearly misfiled and the
    swap won't produce empty wrong-column data."""
    changes = 0
    by_title = {a["title"]: a for a in FAMOUS_TITLE_ATTRIBUTIONS}
    for row in rows:
        title = row.get("Title", "")
        attrib = by_title.get(title)
        if not attrib:
            continue
        c = row.get("Composer", "").strip()
        a = row.get("Author", "").strip()

        c_is_misfiled_author = c and c in attrib["should_be_author"]
        a_is_misfiled_composer = a and a in attrib["should_be_composer"]

        if c_is_misfiled_author and a_is_misfiled_composer:
            row["Composer"], row["Author"] = a, c
            changes += 1
        elif c_is_misfiled_author and not a:
            row["Author"] = c
            row["Composer"] = ""
            changes += 1
        elif a_is_misfiled_composer and not c:
            row["Composer"] = a
            row["Author"] = ""
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Carabelli/OTV cross-file duplicate removal
# ----------------------------------------------------------------------------

# The same Victor sessions are filed under both `Adolfo Carabelli.csv` and
# `Orquesta Típica Victor.csv` for these 7 (Date, Title) keys. Carabelli was
# OTV's director in this period — these belong in OTV. Delete from Carabelli.
CARABELLI_OTV_DUPS = {
    ("1930-01-07", "Engrupido"),
    ("1930-01-07", "Esponjita"),
    ("1930-02-05", "Recóndita"),
    ("1930-06-12", "El Neura"),
    ("1930-06-19", "El Cuatrero"),
    ("1930-06-19", "El Flete"),
    ("1930-11-05", "Hacé Memoria"),
}


def delete_carabelli_otv_dups(rows: list[dict[str, str]], filename: str) -> tuple[list[dict[str, str]], int]:
    if filename != "Adolfo Carabelli.csv":
        return rows, 0
    kept = []
    dropped = 0
    for row in rows:
        key = (row.get("Date", "").strip(), row.get("Title", "").strip())
        if (key in CARABELLI_OTV_DUPS
            and row.get("Orchestra", "").strip() == "Orquesta Típica Porteña"):
            dropped += 1
            continue
        kept.append(row)
    return kept, dropped


# ----------------------------------------------------------------------------
# Composer/Author swap for Pedro Maffia accent (column-restricted typo)
# ----------------------------------------------------------------------------

# Apply Pedro Maffia -> Pedro Maffía ONLY in Composer/Author columns. Leaving
# Bandleader and Bandoneons columns alone (they use the un-accented form).
COMPOSER_AUTHOR_ONLY_TYPOS = {
    "Pedro Maffia": "Pedro Maffía",
}


def fix_composer_author_only_typos(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in ("Composer", "Author"):
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [p.strip() for p in cell.split(",")]
            new_parts = [COMPOSER_AUTHOR_ONLY_TYPOS.get(p, p) for p in parts]
            new = ", ".join(new_parts)
            if new != cell:
                row[field] = new
                changes += 1
    return changes


# ----------------------------------------------------------------------------
# Targeted misc cleanups
# ----------------------------------------------------------------------------

# Strip `[?]` and trailing `?` uncertainty markers wherever they appear.
UNCERTAIN_MARKER_INLINE_RE = re.compile(r"\s*\[\??\]\s*")
TRAILING_Q_RE = re.compile(r"\?+$")


def fix_uncertain_markers(rows: list[dict[str, str]]) -> int:
    changes = 0
    person_list_fields = ("Composer", "Author", "Arranger", "Bandoneons",
                          "Strings", "Singer", "Pianist", "Bassist")
    for row in rows:
        for field in person_list_fields:
            cell = row.get(field, "")
            if not cell:
                continue
            # Strip [?] markers anywhere in the cell; collapse resulting whitespace.
            new_cell = UNCERTAIN_MARKER_INLINE_RE.sub(" ", cell)
            new_cell = re.sub(r"\s+", " ", new_cell).strip()
            new_cell = re.sub(r"\s+,", ",", new_cell)
            new_cell = re.sub(r",\s+\)", ")", new_cell)
            # Also strip trailing `?` per name element
            parts = [p.strip() for p in new_cell.split(",")]
            new_parts = []
            for p in parts:
                new_p = TRAILING_Q_RE.sub("", p).rstrip()
                new_parts.append(new_p)
            new_cell = ", ".join(new_parts)
            if new_cell != cell:
                row[field] = new_cell
                changes += 1
    return changes


# Cyrillic-letter lookalikes — homoglyph replacement (Cyrillic chars that look
# identical to Latin chars; sometimes leak in via copy-paste from mixed-script
# sources). Replace with Latin equivalents.
CYRILLIC_TO_LATIN = {
    "а": "a",  # Cyrillic а
    "А": "A",  # Cyrillic А
    "е": "e",  # Cyrillic е
    "Е": "E",  # Cyrillic Е
    "о": "o",  # Cyrillic о
    "О": "O",  # Cyrillic О
    "р": "p",  # Cyrillic р
    "Р": "P",  # Cyrillic Р
    "с": "c",  # Cyrillic с
    "С": "C",  # Cyrillic С
    "х": "x",  # Cyrillic х
    "Х": "X",  # Cyrillic Х
    "н": "n",  # Cyrillic н (NOT a perfect lookalike but used as 'n' in data)
    "Н": "N",  # Cyrillic Н
}


# Substring repairs that should happen after Cyrillic→Latin replacement
# (the name then needs the proper Spanish accent restored).
POST_CYRILLIC_ACCENT_FIXES = {
    "Alchourron": "Alchourrón",
}


def fix_cyrillic_lookalikes(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in CANONICAL_HEADERS:
            v = row.get(field, "")
            if not v:
                continue
            new = v
            for cyr, lat in CYRILLIC_TO_LATIN.items():
                if cyr in new:
                    new = new.replace(cyr, lat)
            for old, repl in POST_CYRILLIC_ACCENT_FIXES.items():
                if old in new and repl not in new:
                    new = new.replace(old, repl)
            if new != v:
                row[field] = new
                changes += 1
    return changes


# Replace `?` in middle of names where it stands in for an accented letter
QUESTION_FOR_ACCENT = {
    "L. Ri?os": "L. Ríos",
    "Rezs? Seress": "Rezsö Seress",
}


def fix_question_for_accent(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in ("Composer", "Author"):
            cell = row.get(field, "")
            if not cell:
                continue
            new = cell
            for old, repl in QUESTION_FOR_ACCENT.items():
                if old in new:
                    new = new.replace(old, repl)
            if new != cell:
                row[field] = new
                changes += 1
    return changes


# Replace curly apostrophes in Title/AltTitle (Lomuto contains 4 cells)
CURLY_APOSTROPHES = ("’", "‘", "‛")


def fix_curly_quotes_in_titles(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in ("Title", "AltTitle"):
            v = row.get(field, "")
            if not v:
                continue
            new = v
            for ch in CURLY_APOSTROPHES:
                new = new.replace(ch, "'")
            if new != v:
                row[field] = new
                changes += 1
    return changes


# Fix `Sadaic`/`Discos`/`Casetes`-containing tokens in Composer/Author
# These are registry / format metadata leaked into person fields.
SADAIC_JUNK_TOKENS = (
    re.compile(r"\bSadaic\b", re.IGNORECASE),
    re.compile(r"\bDiscos\b", re.IGNORECASE),
    re.compile(r"\bCasetes\b", re.IGNORECASE),
)


def fix_sadaic_junk(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        for field in ("Composer", "Author"):
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [p.strip() for p in cell.split(",")]
            new_parts = []
            cell_changed = False
            for p in parts:
                if any(pat.search(p) for pat in SADAIC_JUNK_TOKENS):
                    cell_changed = True
                    continue
                new_parts.append(p)
            new = ", ".join(new_parts)
            if cell_changed or new != cell:
                row[field] = new
                changes += 1
    return changes


# Normalize UNKNOWN-style placeholders to a single form
UNKNOWN_PLACEHOLDER_MAP = {
    "UKNOWN": "UNKNOWN",
    "**Unknown": "UNKNOWN",
    "**Unknown (wind)": "UNKNOWN",
    "**Unknown (Guitar)": "UNKNOWN",
}


def normalize_unknown_placeholders(rows: list[dict[str, str]]) -> int:
    changes = 0
    person_list_fields = ("Bandoneons", "Strings", "Bassist", "Pianist")
    for row in rows:
        for field in person_list_fields:
            cell = row.get(field, "")
            if not cell:
                continue
            parts = [p.strip() for p in cell.split(",")]
            new_parts = [UNKNOWN_PLACEHOLDER_MAP.get(p, p) for p in parts]
            new = ", ".join(new_parts)
            if new != cell:
                row[field] = new
                changes += 1
    return changes


# Blank the stray `Sinphonic` in Pianist column (it's an orchestration
# descriptor, not a pianist)
def fix_sinphonic_leak(rows: list[dict[str, str]]) -> int:
    changes = 0
    for row in rows:
        if row.get("Pianist", "").strip() == "Sinphonic":
            row["Pianist"] = ""
            changes += 1
    return changes


# Fix the OTV row with a stray low-quote in Title
def fix_otv_low_quote(rows: list[dict[str, str]], filename: str) -> int:
    if filename != "Orquesta Típica Victor.csv":
        return 0
    changes = 0
    for row in rows:
        v = row.get("Title", "")
        if "„" in v:
            new = v.replace("„", '"')
            # Capitalize letter immediately after the (re-straight-quoted) open
            new = re.sub(r'"([a-z])', lambda m: '"' + m.group(1).upper(), new)
            row["Title"] = new
            changes += 1
    return changes


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in ALL_HEADERS})


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

        # Fix Cyrillic letter contamination BEFORE typo map (so the corrected
        # ASCII forms match the typo entries)
        summary["cyrillic_fixed"] += fix_cyrillic_lookalikes(rows)

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

        # Third-pass column-consistency fixes
        summary["singer_joiners"] += normalize_singer_joiners(rows)
        summary["glosa_stripped"] += fix_glosa_and_instrumental(rows)
        summary["singer_leaks_blanked"] += blank_singer_non_singer_leaks(rows)
        summary["instrumentals_recovered"] += recover_instrumentals(rows, filename)
        summary["orchestra_canonical"] += apply_orchestra_canonical(rows)
        summary["orchestra_singer_leaks_blanked"] += blank_orchestra_singer_leaks(rows, filename)
        summary["credit_junk_cleaned"] += clean_credit_junk(rows)
        summary["hyphen_pairs_split"] += fix_hyphen_credit_pairs(rows)
        summary["dotted_initials_fixed"] += fix_dotted_initials(rows)

        # Fourth-pass: cross-file unifications and remaining cleanups
        summary["title_unifications"] += fix_title_unifications(rows)
        summary["famous_title_swaps"] += fix_famous_title_swaps(rows)
        rows, c_dups = delete_carabelli_otv_dups(rows, filename)
        summary["carabelli_otv_dups_dropped"] += c_dups
        summary["composer_author_only_typos"] += fix_composer_author_only_typos(rows)
        summary["uncertain_markers_stripped"] += fix_uncertain_markers(rows)
        summary["question_for_accent_fixed"] += fix_question_for_accent(rows)
        summary["curly_quotes_in_titles"] += fix_curly_quotes_in_titles(rows)
        summary["sadaic_junk_blanked"] += fix_sadaic_junk(rows)
        summary["unknown_placeholders_normalized"] += normalize_unknown_placeholders(rows)
        summary["sinphonic_blanked"] += fix_sinphonic_leak(rows)
        summary["otv_low_quote_fixed"] += fix_otv_low_quote(rows, filename)

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
        "singer_joiners",
        "glosa_stripped",
        "singer_leaks_blanked",
        "instrumentals_recovered",
        "orchestra_canonical",
        "orchestra_singer_leaks_blanked",
        "credit_junk_cleaned",
        "hyphen_pairs_split",
        "dotted_initials_fixed",
        "cyrillic_fixed",
        "title_unifications",
        "famous_title_swaps",
        "carabelli_otv_dups_dropped",
        "composer_author_only_typos",
        "uncertain_markers_stripped",
        "question_for_accent_fixed",
        "curly_quotes_in_titles",
        "sadaic_junk_blanked",
        "unknown_placeholders_normalized",
        "sinphonic_blanked",
        "otv_low_quote_fixed",
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
