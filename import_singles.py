#!/usr/bin/env python3
"""Import additional 78rpm single images from the parse-tango-discographies repo.

Source images (DAHR, tango_info, Discogs) are collected from the sibling
``parse-tango-discographies`` repo, matched to this repo's discography rows by
(date, title), and written into ``images/<Artist>/Singles/<year-bucket>/`` under
the *exact* canonical filename the web client expects — recomputed from the
matched discography row, never trusted from the source filename (source
casing/suffix drift otherwise breaks the R2 key the viewer builds).

The ``<year-bucket>`` folder is a deterministic 5-year bucket derived from the
recording's year (e.g. 1928 -> ``1925-1929``, 1938 -> ``1935-1939``), NOT the
CSV Grouping column.

Canonical single key (mirrors imageUrl() in index.html and bandleader_folder()
in build.py):

    {LastFirst}/Singles/{YearBucket}/{iso}_{Title-Cased}_{Suffix}.webp

Dry-run by default. ``--apply`` writes webp; ``--clean`` also removes stray files
in the artist's Singles tree that aren't a computed target; ``--upload`` runs
sync_artist_images.py afterwards.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

from PIL import Image, UnidentifiedImageError

REPO = Path(__file__).resolve().parent
PARSE = REPO.parent / "parse-tango-discographies"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

LAST_NAME_PARTICLES = {"de", "di", "del", "la", "las", "los"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
# Source image filename shape: {date}_{Title-Slug}_{Suffix}[_dupN].ext
FNAME_RE = re.compile(
    r"^(?P<date>\d{4}(?:-\d{2}-\d{2})?)_(?P<title>.+?)_(?P<suffix>[A-Za-z][A-Za-z-]*?)(?:_\d+)?\.(?P<ext>jpg|jpeg|png|webp)$",
    re.IGNORECASE,
)


# ---------- client-parity helpers ----------
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def bandleader_folder(name: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", strip_accents(name or ""))
    parts = cleaned.split()
    return parts[-1] + "".join(parts[:-1]) if parts else ""


def artist_suffix(name: str) -> str:
    parts = strip_accents(name or "").split()
    if not parts:
        return ""
    start = len(parts) - 1
    for i in range(1, len(parts)):
        if parts[i].lower() in LAST_NAME_PARTICLES:
            start = i
            break
    return re.sub(r"^-+|-+$", "", re.sub(r"[^A-Za-z0-9]+", "-", "-".join(parts[start:])))


def year_bucket(s: str) -> str | None:
    """5-year bucket for a date/year string: 1928 -> '1925-1929'. None if no year."""
    m = re.match(r"(\d{4})", s or "")
    if not m:
        return None
    b = (int(m.group(1)) // 5) * 5
    return f"{b}-{b+4}"


def title_segment(title: str) -> str:
    slug = re.sub(r"^-+|-+$", "", re.sub(r"[^A-Za-z0-9]+", "-", strip_accents(title or "")))
    return "-".join(w[:1].upper() + w[1:] if w else w for w in slug.split("-"))


def iso_date(date: str) -> str | None:
    d = (date or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", d)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return d
    if re.match(r"^\d{4}$", d):
        return d
    return None


# ---------- match-key normalization ----------
def norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", strip_accents(t or "").lower())).strip()


def year_of(date: str) -> str:
    m = re.search(r"(\d{4})", date or "")
    return m.group(1) if m else ""


def date_key(date: str) -> str | None:
    return iso_date(date)


# ---------- discography index ----------
class Target:
    __slots__ = ("bucket", "stem")

    def __init__(self, bucket: str, stem: str):
        self.bucket = bucket
        self.stem = stem

    def key(self):
        return (self.bucket, self.stem)


def build_disco_index(disco_csv: Path, bandleader: str):
    suffix = artist_suffix(bandleader)
    exact: dict[tuple, list[Target]] = defaultdict(list)
    byyear: dict[tuple, list[Target]] = defaultdict(list)
    n_rows = n_indexed = 0
    with disco_csv.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            n_rows += 1
            iso = iso_date(row.get("Date", ""))
            title = (row.get("Title") or "").strip()
            if not (iso and title):
                continue
            bucket = year_bucket(iso)
            if bucket is None:
                continue
            stem = f"{iso}_{title_segment(title)}_{suffix}"
            tgt = Target(bucket, stem)
            n_indexed += 1
            yr = year_of(iso)
            for t in {title, (row.get("AltTitle") or "").strip()}:
                if not t:
                    continue
                nt = norm_title(t)
                exact[(iso, nt)].append(tgt)
                byyear[(yr, nt)].append(tgt)
    return exact, byyear, n_rows, n_indexed


# ---------- source collection ----------
def parse_source_name(path: Path):
    """(date, title) parsed from a canonical-ish source filename, or None."""
    m = FNAME_RE.match(path.name)
    if not m:
        return None
    return m.group("date"), m.group("title").replace("-", " ")


def collect_sources(dirs: list[Path]) -> list[tuple[str, str, Path]]:
    out: list[tuple[str, str, Path]] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() not in IMG_EXTS:
                continue
            parsed = parse_source_name(p)
            if not parsed:
                continue
            date, title = parsed
            out.append((date, title, p))
    return out


# ---------- matching ----------
def match_targets(date: str, title: str, exact, byyear) -> list[Target]:
    nt = norm_title(title)
    dk = date_key(date)
    yr = year_of(date)
    hits = []
    if dk and (dk, nt) in exact:
        hits = exact[(dk, nt)]
    elif (yr, nt) in byyear:
        hits = byyear[(yr, nt)]
    # de-dup by (bucket, stem)
    uniq: dict[tuple, Target] = {}
    for t in hits:
        uniq[t.key()] = t
    return list(uniq.values())


def actual_names(d: Path) -> set[str]:
    """Case-sensitive set of filenames in d (Windows FS is case-insensitive, but
    R2/S3 keys are case-sensitive, so we must compare names exactly)."""
    return set(os.listdir(d)) if d.is_dir() else set()


def remove_case_collision(dest: Path) -> None:
    """Drop any sibling that collides case-insensitively with dest but differs in
    case, so the freshly written file lands with dest's exact casing on Windows."""
    for name in actual_names(dest.parent):
        if name != dest.name and name.lower() == dest.name.lower():
            (dest.parent / name).unlink()


# Cap the long edge so 78rpm label scans (some sources ship ~30MB, >16383px files
# that stall or exceed WebP's 16383px limit) convert fast and stay small. The site
# shows ~200px thumbs / ~800px detail, so 1600px keeps full quality headroom.
MAX_DIM = 1600

# Auto-crop full-disc record photos down to their label before downscaling; set
# False via --no-crop. Label-only scans are left untouched (see _crop_label.py).
CROP_LABELS = True


def _downscaled(img: Image.Image) -> Image.Image:
    w, h = img.size
    if max(w, h) <= MAX_DIM:
        return img
    scale = MAX_DIM / max(w, h)
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def _maybe_crop_label(img: Image.Image) -> Image.Image:
    """Crop a full-disc photo to its label; return label-only/unknown images as-is."""
    if not CROP_LABELS:
        return img
    try:
        from _crop_label import classify_and_detect
        verdict, bbox = classify_and_detect(img)
        if verdict == "full_disc" and bbox is not None:
            return img.crop(bbox)
    except Exception as e:  # detection must never break the import
        print(f"  warn: label crop skipped: {e}", file=sys.stderr)
    return img


def write_webp(src: Path, dest: Path, quality: int) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    remove_case_collision(dest)
    try:
        with Image.open(src) as img:
            # draft() lets the JPEG decoder emit at ~target size (1/2,1/4,1/8), turning
            # a 34MP decode into a fraction of the work. Aim at 2*MAX_DIM so a cropped
            # label region still resolves to >=MAX_DIM when the source is large enough.
            img.draft("RGB", (2 * MAX_DIM, 2 * MAX_DIM))
            img = _maybe_crop_label(img.convert("RGB"))
            img = _downscaled(img)
            img.save(dest, "WEBP", quality=quality)
        return True
    except (UnidentifiedImageError, OSError) as e:
        print(f"  warn: convert failed {src.name}: {e}", file=sys.stderr)
        return False


# ---------- per-artist source config ----------
def source_dirs(cfg: dict) -> list[Path]:
    dirs = []
    if cfg.get("tangoinfo"):
        dirs.append(PARSE / "TangoInfo data" / "ScrapeShellac" / "out" / cfg["tangoinfo"] / "images")
    if cfg.get("dahr"):
        dirs.append(PARSE / "DAHR Parsing" / "output" / cfg["dahr"] / "images")
    if cfg.get("discogs"):
        dirs.append(PARSE / "Tangos78" / "matched_discography" / cfg["discogs"])
    return dirs


# local folder -> {display, csv, tangoinfo, dahr, discogs}
# tangoinfo: TangoInfo data/ScrapeShellac/out/<name>/images
# dahr:      DAHR Parsing/output/<name>/images   (omit if the artist isn't in DAHR)
# discogs:   Tangos78/matched_discography/<slug>
ARTISTS = {
    "Gardel":    {"display": "Carlos Gardel",     "csv": "Carlos Gardel.csv",
                  "tangoinfo": "Carlos_Gardel",    "dahr": "Gardel_Carlos",     "discogs": "carlos-gardel"},
    "Canaro":    {"display": "Francisco Canaro",   "csv": "Francisco Canaro.csv",
                  "tangoinfo": "Francisco_Canaro", "dahr": "Canaro_Francisco",  "discogs": "francisco-canaro"},
    "Firpo":     {"display": "Roberto Firpo",      "csv": "Roberto Firpo.csv",
                  "tangoinfo": "Roberto_Firpo",    "dahr": "Firpo_Roberto",     "discogs": "roberto-firpo"},
    "Fresedo":   {"display": "Osvaldo Fresedo",    "csv": "Osvaldo Fresedo.csv",
                  "tangoinfo": "Osvaldo_Fresedo",  "dahr": "Fresedo_Osvaldo",   "discogs": "osvaldo-fresedo"},
    "DeCaro":    {"display": "Julio De Caro",      "csv": "Julio De Caro.csv",
                  "tangoinfo": "Julio_De_Caro",    "dahr": "Caro_Julio_de",     "discogs": "julio-de-caro"},
    "Lomuto":    {"display": "Francisco Lomuto",   "csv": "Francisco Lomuto.csv",
                  "tangoinfo": "Francisco_Lomuto", "dahr": "Lomuto_Francisco_J", "discogs": "francisco-lomuto"},
    "Demare":    {"display": "Lucio Demare",       "csv": "Lucio Demare.csv",
                  "tangoinfo": "Lucio_Demare",     "dahr": "Demare_Lucio",      "discogs": "lucio-demare"},
    "DeAngelis": {"display": "Alfredo De Angelis", "csv": "Alfredo De Angelis.csv",
                  "tangoinfo": "Alfredo_De_Angelis", "discogs": "alfredo-de-angelis"},
    "Piazzolla": {"display": "Astor Piazzolla",    "csv": "Astor Piazzolla.csv",
                  "tangoinfo": "Astor_Piazzolla",  "discogs": "astor-piazzolla"},
    "Basso":     {"display": "José Basso",         "csv": "José Basso.csv",
                  "tangoinfo": "Jose_Basso",       "discogs": "jose-basso"},
    "Francini":  {"display": "Enrique Francini",   "csv": "Enrique Francini.csv",
                  "tangoinfo": "Enrique_Francini", "discogs": "enrique-francini"},
    # --- expanded coverage (folder keys reuse existing images/<key> where present) ---
    "DArienzo":  {"display": "Juan D'Arienzo",     "csv": "Juan D'Arienzo.csv",
                  "tangoinfo": "Juan_DArienzo",    "dahr": "DArienzo_Juan",     "discogs": "juan-d-arienzo"},
    "DiSarli":   {"display": "Carlos Di Sarli",    "csv": "Carlos Di Sarli.csv",
                  "tangoinfo": "Carlos_Di_Sarli",  "dahr": "Di_Sarli_Carlos",   "discogs": "carlos-di-sarli"},
    "Troilo":    {"display": "Anibal Troilo",      "csv": "Anibal Troilo.csv",
                  "tangoinfo": "Anibal_Troilo",    "dahr": "Orquesta_Tpica_Anibal_Troilo", "discogs": "anibal-troilo"},
    "Tanturi":   {"display": "Ricardo Tanturi",    "csv": "Ricardo Tanturi.csv",
                  "tangoinfo": "Ricardo_Tanturi",  "dahr": "Tanturi_Ricardo",   "discogs": "ricardo-tanturi"},
    "Laurenz":   {"display": "Pedro Laurenz",      "csv": "Pedro Laurenz.csv",
                  "dahr": "Laurenz_Pedro",         "discogs": "pedro-laurenz"},
    "Pugliese":  {"display": "Osvaldo Pugliese",   "csv": "Osvaldo Pugliese.csv",
                  "tangoinfo": "Osvaldo_Pugliese", "discogs": "osvaldo-pugliese"},
    "Biagi":     {"display": "Rodolfo Biagi",      "csv": "Rodolfo Biagi.csv",
                  "tangoinfo": "Rodolfo_Biagi",    "discogs": "rodolfo-biagi"},
    "Calo":      {"display": "Miguel Calo",        "csv": "Miguel Calo.csv",
                  "tangoinfo": "Miguel_Calo",      "discogs": "miguel-calo"},
    "Castillo":  {"display": "Alberto Castillo",   "csv": "Alberto Castillo.csv",
                  "tangoinfo": "Alberto_Castillo", "discogs": "alberto-castillo"},
    "DAgostino": {"display": "Angel D'Agostino",   "csv": "Angel D'Agostino.csv",
                  "dahr": "Agostino_Angel_d",      "discogs": "angel-d-agostino"},
    "Vargas":    {"display": "Angel Vargas",       "csv": "Angel Vargas.csv",
                  "dahr": "Vargas_Angel",          "discogs": "angel-vargas"},
    "Rodriguez": {"display": "Enrique Rodriguez",  "csv": "Enrique Rodriguez.csv",
                  "dahr": "Rodrguez_Enrique",      "discogs": "enrique-rodriguez"},
    "Donato":    {"display": "Edgardo Donato",     "csv": "Edgardo Donato.csv",
                  "dahr": "Donato_Edgardo",        "discogs": "edgardo-donato"},
    "Maglio":    {"display": "Juan Maglio",        "csv": "Juan Maglio.csv",
                  "dahr": "Maglio_Juan",           "discogs": "juan-maglio"},
    "Maffia":    {"display": "Pedro Maffia",       "csv": "Pedro Maffia.csv",
                  "dahr": "Maffia_Pedro",          "discogs": "pedro-maffia"},
    "Carabelli": {"display": "Adolfo Carabelli",   "csv": "Adolfo Carabelli.csv",
                  "dahr": "Carabelli_Adolfo",      "discogs": "adolfo-carabelli"},
    "Aieta":     {"display": "Anselmo Aieta",      "csv": "Anselmo Aieta.csv",
                  "dahr": "Aieta_Anselmo",         "discogs": "anselmo-aieta"},
    "Cobian":    {"display": "Juan Carlos Cobian", "csv": "Juan Carlos Cobain.csv",  # csv filename misspells Cobián as "Cobain"
                  "tangoinfo": "Juan_Carlos_Cobian", "dahr": "Cobin_Juan_Carlos", "discogs": "juan-carlos-cobian"},
    "OTVictor":  {"display": "Orquesta Típica Victor", "csv": "Orquesta Típica Victor.csv",
                  "dahr": "Orquesta_Tpica_Victor", "discogs": "orquesta-tipica-victor"},
    "Gobbi":     {"display": "Alfredo Gobbi",      "csv": "Alfredo Gobbi.csv",
                  "tangoinfo": "Alfredo_J_Gobbi",  "discogs": "alfredo-gobbi"},
    "Pontier":   {"display": "Armando Pontier",    "csv": "Armando Pontier.csv",
                  "tangoinfo": "Armando_Pontier"},
    "FedericoDomingo": {"display": "Domingo Federico", "csv": "Domingo Federico.csv",
                  "tangoinfo": "Domingo_Federico", "discogs": "domingo-federico"},
    "Maderna":   {"display": "Osmar Maderna",      "csv": "Osmar Maderna.csv",
                  "tangoinfo": "Osmar_Maderna",    "discogs": "osmar-maderna"},
    "Varela":    {"display": "Héctor Varela",      "csv": "Héctor Varela.csv",
                  "discogs": "hector-varela"},
    "Salgan":    {"display": "Horacio Salgan",     "csv": "Horacio Salgan.csv",
                  "discogs": "horacio-salgan"},
}


def run(local: str, args) -> int:
    if local not in ARTISTS:
        print(f"error: unknown artist '{local}'. Add it to ARTISTS.", file=sys.stderr)
        return 2
    cfg = ARTISTS[local]
    disco_csv = REPO / "csv_files" / cfg["csv"]
    bandleader = cfg["display"]
    singles_root = REPO / "images" / local / "Singles"

    exact, byyear, n_rows, n_indexed = build_disco_index(disco_csv, bandleader)
    print(f"disco: {n_rows} rows, {n_indexed} imageable (have Date+Title)")

    sources = collect_sources(source_dirs(cfg))
    print(f"sources: {len(sources)} parsable images from {len([d for d in source_dirs(cfg) if d.is_dir()])} dir(s)")

    # Source priority for tie-breaks: tangoinfo (0) > dahr (1) > discogs (2), by dir order.
    src_priority: dict[Path, int] = {d: i for i, d in enumerate(source_dirs(cfg))}

    def priority_of(path: Path) -> int:
        return src_priority.get(path.parent, len(src_priority))

    _long_edge_cache: dict[Path, int] = {}

    def long_edge(path: Path) -> int:
        # Image.open(...).size reads only the header, so this is cheap (no decode).
        if path not in _long_edge_cache:
            try:
                with Image.open(path) as im:
                    w, h = im.size
                _long_edge_cache[path] = max(w, h)
            except (UnidentifiedImageError, OSError, ValueError):
                _long_edge_cache[path] = 0
        return _long_edge_cache[path]

    # Collect ALL candidate sources per target, then score-pick a winner.
    candidates: dict[tuple, list[Path]] = defaultdict(list)
    unmatched: list[tuple[str, str, Path]] = []
    for date, title, path in sources:
        tgts = match_targets(date, title, exact, byyear)
        if not tgts:
            unmatched.append((date, title, path))
            continue
        for t in tgts:
            candidates[(t.bucket, t.stem)].append(path)

    MIN_EDGE = 250  # skip tiny scans when a usable candidate exists

    def score(path: Path):
        # Bigger is better: bucket the long edge (//400) so near-ties fall through to
        # source priority; negate priority so tangoinfo (0) sorts ahead of discogs (2).
        return (long_edge(path) // 400, -priority_of(path))

    chosen: dict[tuple, Path] = {}
    runner_ups: list[tuple[str, str, str]] = []  # (target, chosen_path, alt_paths)
    for key, paths in candidates.items():
        uniq = list(dict.fromkeys(paths))  # a source can match a target via >1 heuristic
        usable = [p for p in uniq if long_edge(p) >= MIN_EDGE]
        pool = usable if usable else uniq
        ranked = sorted(pool, key=score, reverse=True)
        chosen[key] = ranked[0]
        alts = ranked[1:] + [p for p in uniq if p not in pool]
        if alts:
            runner_ups.append((f"{key[0]}/{key[1]}.webp", str(ranked[0]),
                               "|".join(str(p) for p in alts)))

    print(f"matched -> {len(chosen)} distinct target images; {len(unmatched)} source images unmatched")

    # Record runner-up candidates so a later retro pass can revisit choices.
    if runner_ups:
        cand_csv = REPO / "images" / local / "_import_candidates.csv"
        cand_csv.parent.mkdir(parents=True, exist_ok=True)
        with cand_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["target", "chosen_path", "alt_paths"])
            w.writerows(sorted(runner_ups))
        print(f"recorded {len(runner_ups)} multi-candidate targets -> {cand_csv.relative_to(REPO)}")

    # Target names per bucket dir, compared case-SENSITIVELY (R2 keys are).
    target_by_dir: dict[str, set[str]] = defaultdict(set)
    for bucket, stem in chosen:
        target_by_dir[bucket].add(f"{stem}.webp")

    # Existing files that aren't an exact-case target -> stray (wrong case, .jpg, orphan).
    strays = []
    if singles_root.is_dir():
        for p in singles_root.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMG_EXTS:
                if p.name not in target_by_dir.get(p.parent.name, set()):
                    strays.append(p)

    # Writes: targets whose exact-case webp isn't already present. With --clean we
    # regenerate all (strays are wiped first, so nothing is "already present").
    present = {(b, n) for b in target_by_dir for n in actual_names(singles_root / b)}
    writes = []
    already = 0
    for (bucket, stem), src in sorted(chosen.items()):
        dest = singles_root / bucket / f"{stem}.webp"
        if not args.clean and (bucket, f"{stem}.webp") in present:
            already += 1
            continue
        writes.append((src, dest))
    print(f"would write {len(writes)} webp ({already} already present, exact case)")

    if args.show_unmatched:
        print("\n-- sample unmatched sources --")
        for date, title, path in unmatched[:40]:
            print(f"  {date}  {title!r}  <- {path.name}")

    print(f"\nstray files (wrong-case / .jpg / orphan): {len(strays)}")
    for p in strays[:15]:
        print(f"  stray: {p.relative_to(REPO)}")

    if not args.apply:
        print("\n(dry-run; pass --apply to write, --clean to also remove strays)")
        return 0

    if args.clean and strays:
        for p in strays:
            p.unlink()
        print(f"cleaned {len(strays)} stray files")

    wrote = 0
    for src, dest in writes:
        if write_webp(src, dest, args.quality):
            wrote += 1
    print(f"wrote {wrote} webp into {singles_root.relative_to(REPO)}")

    if args.upload:
        print("\n== uploading via sync_artist_images.py ==")
        import subprocess
        rc = subprocess.call([sys.executable, str(REPO / "sync_artist_images.py"), local], cwd=str(REPO))
        return rc
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("artist", help="local images/<folder> name, e.g. Gardel")
    ap.add_argument("--apply", action="store_true", help="write webp files")
    ap.add_argument("--clean", action="store_true", help="remove stray non-target images from the Singles tree")
    ap.add_argument("--upload", action="store_true", help="run sync_artist_images.py after writing")
    ap.add_argument("--quality", type=int, default=85)
    ap.add_argument("--show-unmatched", action="store_true")
    ap.add_argument("--no-crop", action="store_true",
                    help="disable auto-cropping full-disc photos down to the label")
    args = ap.parse_args()
    global CROP_LABELS
    CROP_LABELS = not args.no_crop
    return run(args.artist, args)


if __name__ == "__main__":
    sys.exit(main())
