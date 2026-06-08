"""Build the image manifest by walking images/<Artist>/{LPs,EPs}/<Folder>/*.webp."""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

# Recognized cover-art types. A type is accepted if it matches one of these patterns,
# optionally followed by an intentional-alternate suffix (e.g. 'V2', 'Alt', 'Alt 2').
# Anything else (stray scanner names like 'Image 3', random words) is warned + skipped.
_BASE_TYPE_RE = re.compile(
    r"""^(
            Front | Back |
            (Disk|Disc)\ \d+ |
            Side\ \d+ |
            Gatefold | Inner | Insert | Label | Sleeve | Booklet | Spine
        )$""",
    re.IGNORECASE | re.VERBOSE,
)
# Intentional alternate suffix appended to a base type: 'Disk 1 V2', 'Front Alt', 'Back Alt 2'.
_ALT_SUFFIX_RE = re.compile(r"^(V\d+|Alt(\s+\d+)?)$", re.IGNORECASE)


def is_valid_type(t: str) -> bool:
    """True if t is a recognized cover type, optionally with an intentional alternate suffix."""
    if _BASE_TYPE_RE.match(t):
        return True
    # Allow a trailing alternate marker on an otherwise-valid base type, e.g. 'Disk 1 V2'.
    for marker in ("V", "Alt", "v", "alt"):
        idx = t.rfind(" " + marker)
        if idx > 0:
            base, suffix = t[:idx], t[idx + 1:]
            if _BASE_TYPE_RE.match(base) and _ALT_SUFFIX_RE.match(suffix):
                return True
    return False


def type_from_filename(folder: str, filename: str) -> str:
    """'Armenonville Front.webp' -> 'Front' given folder='Armenonville'.

    Returns "" (skip) only for files that don't match the "<folder> " prefix, since
    those can't yield a clean type. Files with a valid prefix but an unrecognized type
    (e.g. 'Image 3', 'Center A') are KEPT but warned about: such scans are legitimate
    extra gallery images and can never be chosen as the cover (build.py forces the cover
    to front/disk/side), so dropping them would silently lose art. The warning surfaces
    naming drift without deleting content.
    """
    stem = Path(filename).stem  # strips .webp
    prefix = folder + " "
    if not stem.startswith(prefix):
        print(f"warn: file {filename!r} does not start with folder prefix {folder!r}", file=sys.stderr)
        return ""
    t = stem[len(prefix):]
    if not is_valid_type(t):
        print(f"warn: non-standard cover type {t!r} in {filename!r} (kept as extra gallery image)", file=sys.stderr)
    return t


def walk_collection(artist_root: Path) -> list[dict]:
    """Walk artist_root/{LPs,EPs}/<Folder>/*.webp. Returns one row per webp file."""
    rows: list[dict] = []
    for kind, subdir in (("LP", "LPs"), ("EP", "EPs")):
        base = artist_root / subdir
        if not base.is_dir():
            continue
        for folder_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            folder = folder_dir.name
            for webp in sorted(folder_dir.glob("*.webp")):
                t = type_from_filename(folder, webp.name)
                if not t:
                    continue
                rows.append({"Folder": folder, "Type": t, "Kind": kind})
    return rows


def write_manifest(path: Path, rows: list[dict]) -> None:
    """Write the image manifest CSV. Header: LP_Folder,Type,Kind (column kept as LP_Folder for build.py compat)."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["LP_Folder", "Type", "Kind"])
        for r in rows:
            w.writerow([r["Folder"], r["Type"], r["Kind"]])
