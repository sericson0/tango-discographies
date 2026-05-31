"""Build the image manifest by walking images/<Artist>/{LPs,EPs}/<Folder>/*.webp."""
from __future__ import annotations

import csv
import sys
from pathlib import Path


def type_from_filename(folder: str, filename: str) -> str:
    """'Armenonville Front.webp' -> 'Front' given folder='Armenonville'."""
    stem = Path(filename).stem  # strips .webp
    prefix = folder + " "
    if not stem.startswith(prefix):
        print(f"warn: file {filename!r} does not start with folder prefix {folder!r}", file=sys.stderr)
        return ""
    return stem[len(prefix):]


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
