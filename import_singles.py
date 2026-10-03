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
sync_artist_images.py afterwards. A dry-run writes NOTHING (not even
``_import_candidates.csv``, which only ``--apply`` records).

Quarantine awareness. A target a vision pass already judged bad sits in
``Singles/_suspect/`` (and hand-set-aside images in ``Incorrect/``), but its
source scan is still in the parse repo, so every import used to re-create it
in a served folder -- often under a DIFFERENT target name than the one it was
quarantined as. Every candidate is therefore rendered exactly as it would be
written (label crop + downscale + webp) and compared against the WHOLE
``_suspect``/``Incorrect`` tree: an exact byte match, or a 256-bit average
hash (the harvest's ``harvest/dedupe.py`` hash) within Hamming 3 of either
the rendered or the uncropped image, CONFIRMED by the label's title band
(see BAND_MIN_NCC: one label design carries many titles, and the whole-image
hash alone called Maglio's 'Flor De Zanahoria' a copy of 'El Alero'). A
match is skipped with a log line and
the next-ranked candidate is tried. The same check against the SERVED tree
skips a candidate that is already another recording's image (one photograph
cannot be two recordings).

``--harvest-only`` restricts sources to the vision-matched harvest crops
(``Marketplace Harvest/matched/<Key>/``). The other sources are matched on a
filename's date+title alone and have been badly wrong (8 of 12 on Fresedo;
28 of Fresedo's 51 heuristic targets were already quarantined); a harvest
crop's identity was established by a vision read of its catalog/matrix.
It cannot be combined with ``--clean`` (every non-harvest file would look
like a stray).

``--replace-reissues`` is the ONE exception to never-overwrite. Policy: a
reissue is a valid image, but the original issue replaces a served reissue.
A present target is replaced only when BOTH hold:

* the served file is recorded as a reissue -- status ``reissue`` in
  ``_verification_report.csv``, a kept row whose ``disc_verdict`` is
  ``mismatch``, or it is (ahash-)the image of a harvest crop whose label
  catalog mismatches the row's ``Disc``; and
* the candidate is a harvest crop whose label catalog MATCHES the row's
  ``Disc`` (read from the harvest extraction of that exact crop).

The original must also be at least ``MIN_REPLACE_PX`` on its shorter side
as written (``--min-replace-px``); a smaller one is logged "too small to
replace" and the reissue stays. Ordinary imports skip candidates under
``MIN_IMPORT_PX`` (``--min-import-px``), logged "too small to import".

Each replacement is printed, and the replaced file is moved to
``Singles/_replaced/<bucket>/`` first (``_``-prefixed, so worklists and sync
skip it like ``_suspect``). Its report row is reset to ``unchecked`` so the
next vision pass judges the new image. Everything else keeps the
one-image-per-recording, never-overwrite rule.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
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
# The date carries whatever precision the discography has: YYYY, YYYY-MM or
# YYYY-MM-DD. Month precision is not a curiosity -- 277 rows across csv_files
# have it (109 Pugliese, 84 Di Sarli), and while it was unrepresentable those
# rows could never be given a single at all: iso_date returned None, so emit
# skipped them and the client built no URL for them.
FNAME_RE = re.compile(
    r"^(?P<date>\d{4}(?:-\d{2}(?:-\d{2})?)?)_(?P<title>.+?)_(?P<suffix>[A-Za-z][A-Za-z-]*?)(?:_\d+)?\.(?P<ext>jpg|jpeg|png|webp)$",
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
    """Normalize a discography Date to the filename's date segment, or None.

    Returns the date at the precision the CSV states it -- YYYY, YYYY-MM or
    YYYY-MM-DD -- because that segment is both the filename key and the key
    a row is looked up by. Downgrading YYYY-MM to YYYY would silently merge
    it with a bare-year row of the same title, so precision is preserved
    rather than truncated.
    """
    d = (date or "").strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", d)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return d
    m = re.match(r"^(\d{4})-(\d{1,2})$", d)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
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
    __slots__ = ("bucket", "stem", "disc")

    def __init__(self, bucket: str, stem: str, disc: str = ""):
        self.bucket = bucket
        self.stem = stem
        self.disc = disc          # the row's CSV Disc (the ORIGINAL issue)

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
            tgt = Target(bucket, stem, (row.get("Disc") or "").strip())
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
    # de-dup by (bucket, stem); keep the first row that records a Disc
    uniq: dict[tuple, Target] = {}
    for t in hits:
        if t.key() not in uniq or (t.disc and not uniq[t.key()].disc):
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

# Minimum pixel size, on the SHORTER side of the image as written (after the
# label crop and downscale). Measured 2026-09-29 on the 17 originals that
# --replace-reissues put in place of served reissues: 240 px ('Aunque No Lo
# Crean', a 15 KB original that displaced a 2400 px, 1.6 MB scan) and two
# 400x266 photos (label ~260 px across) are thumbnail-grade -- the title is
# legible only just; the next size up, 398 px ('La Ultima Copa'), reads
# cleanly, as do 413-1200 px. Of 5,316 served singles, 19 are under 250 px.
#   MIN_REPLACE_PX: an original issue replaces a served reissue only at or
#     above this ("decent definition"); below it the reissue stays and the
#     original is logged "too small to replace" (override --min-replace-px).
#   MIN_IMPORT_PX: an ordinary import never makes a thumbnail the only image
#     of a recording; a smaller candidate is logged "too small to import" and
#     the next candidate is tried (override --min-import-px).
MIN_REPLACE_PX = 350
MIN_IMPORT_PX = 250


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


class Rendered:
    """A source image rendered exactly as it would be written, plus the
    hashes the quarantine check compares.

    ``size`` (pixel w, h of the written webp) and ``band_out`` (its title-band
    signature, see :func:`title_band`) are computed from ``data`` on first
    use; ``band_raw`` is the uncropped source's band, when known."""
    __slots__ = ("data", "sha", "ahash_out", "ahash_raw", "band_raw", "_size", "_band_out")

    def __init__(self, data: bytes, ahash_out: int, ahash_raw: int,
                 band_raw: list[float] | None = None):
        self.data = data
        self.sha = hashlib.sha256(data).hexdigest()
        self.ahash_out = ahash_out
        self.ahash_raw = ahash_raw
        self.band_raw = band_raw
        self._size: tuple[int, int] | None = None
        self._band_out: list[float] | None = None

    def _decode(self) -> None:
        try:
            with Image.open(io.BytesIO(self.data)) as im:
                self._size = im.size
                self._band_out = title_band(im)
        except (UnidentifiedImageError, OSError, ValueError):
            self._size, self._band_out = (0, 0), []

    @property
    def size(self) -> tuple[int, int]:
        if self._size is None:
            self._decode()
        return self._size

    @property
    def short_side(self) -> int:
        return min(self.size)

    @property
    def band_out(self) -> list[float]:
        if self._band_out is None:
            self._decode()
        return self._band_out


def render_webp(src: Path, quality: int) -> Rendered | None:
    """The webp bytes write_webp would produce for ``src`` (None if unreadable)."""
    try:
        with Image.open(src) as img:
            # draft() lets the JPEG decoder emit at ~target size (1/2,1/4,1/8), turning
            # a 34MP decode into a fraction of the work. Aim at 2*MAX_DIM so a cropped
            # label region still resolves to >=MAX_DIM when the source is large enough.
            img.draft("RGB", (2 * MAX_DIM, 2 * MAX_DIM))
            raw = img.convert("RGB")
            out = _downscaled(_maybe_crop_label(raw))
            buf = io.BytesIO()
            out.save(buf, "WEBP", quality=quality)
            data = buf.getvalue()
            with Image.open(io.BytesIO(data)) as back:
                h_out = ahash(back)
            return Rendered(data, h_out, ahash(raw), band_raw=title_band(raw))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        print(f"  warn: convert failed {src.name}: {e}", file=sys.stderr)
        return None


def write_webp(src: Path, dest: Path, quality: int,
               rendered: Rendered | None = None) -> bool:
    """Write ``src`` as webp at ``dest`` (or the pre-rendered bytes, if given)."""
    if rendered is None:
        rendered = render_webp(src, quality)
        if rendered is None:
            return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    remove_case_collision(dest)
    try:
        dest.write_bytes(rendered.data)
        return True
    except OSError as e:
        print(f"  warn: write failed {dest.name}: {e}", file=sys.stderr)
        return False


# ---------- quarantine awareness (see module docstring) ----------
# Same hash as parse-tango-discographies/harvest/dedupe.py (a 16x16 average
# hash survives our own re-encode/resize) but a TIGHTER threshold than its 5.
# Measured 2026-09-29 on Fresedo/Canaro/Biagi/DeCaro: every re-materialized
# suspect sat at 0-1 (69 more were byte-exact), while two DIFFERENT Columbia
# labels of one design (Canaro 'El Tiburón' T1206 vs 'Los Indios' T1210) sat
# at exactly 5. A false match here drops a good image, so 3.
AHASH_SIZE = 16
SUSPECT_MAX_DISTANCE = 3
# Folders whose images are known-bad for this artist: the vision quarantine,
# and the maintainer's hand-curated set-aside folder.
BAD_DIRS = ("_suspect", "Incorrect")


def ahash(img: Image.Image) -> int:
    """256-bit average hash (parity with harvest/dedupe.py ahash)."""
    g = img.convert("L").resize((AHASH_SIZE, AHASH_SIZE), Image.Resampling.LANCZOS)
    px = list(g.getdata())
    avg = sum(px) / len(px)
    bits = 0
    for i, p in enumerate(px):
        if p >= avg:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _file_ahash(path: Path) -> int | None:
    try:
        with Image.open(path) as im:
            return ahash(im)
    except (UnidentifiedImageError, OSError, ValueError):
        return None


# A 16x16 average hash is dominated by a label's LAYOUT, so two different
# titles on one label design can sit inside the threshold: Maglio's Columbia
# 'Flor De...Zanahoria' (TX763) scored d=3 against the served 'El Alero'
# (TX764) -- same portrait, logo and typography, different title, composer
# and catalog -- and was skipped as a duplicate; Canaro 'El Que A Hierro
# Mata' also sat at d=3 from 'Quiero Verte Una Vez Mas'. An ahash hit is
# therefore CONFIRMED on the label's title band (the centre-lower strip
# holding title, performer and catalog; see title_band): the normalized
# cross-correlation of the two bands at 96x40 grey. Measured 2026-09-29 over
# every ahash hit (d <= 6) of an instrumented dry-run of Fresedo/Biagi/
# DeCaro/Canaro/Maglio: all 114 same-image hits (ahash 0-2; 83 byte-exact,
# the rest re-encoded, cropped or raw) scored >= 0.993; the 16 different-
# label hits scored 0.07 (Flor De Zanahoria/El Alero), 0.13 (El Que A
# Hierro Mata/Quiero Verte), 0.31-0.54 (Biagi/Maglio pairs at d=4-6) and at
# most 0.86 (Columbia El Tiburon/Los Indios, d=5).
BAND_MIN_NCC = 0.95
_BAND_SIZE = (96, 40)


def title_band(img: Image.Image) -> list[float]:
    """Grey 96x40 samples of the label's title band (x 20-80%, y 55-80%)."""
    w, h = img.size
    if w < 4 or h < 4:
        return []
    band = img.crop((int(w * .2), int(h * .55), int(w * .8), int(h * .8)))
    return [float(v) for v in
            band.convert("L").resize(_BAND_SIZE, Image.Resampling.LANCZOS).getdata()]


def band_ncc(a: list[float] | None, b: list[float] | None) -> float:
    """Normalized cross-correlation of two title bands (1.0 = identical);
    -1.0 when either is unknown."""
    if not a or not b or len(a) != len(b):
        return -1.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sa = sb = 0.0
    for x, y in zip(a, b):
        dx, dy = x - ma, y - mb
        num += dx * dy
        sa += dx * dx
        sb += dy * dy
    if sa == 0 or sb == 0:
        return 1.0 if sa == sb else 0.0
    return num / (sa * sb) ** 0.5


def _file_sig(path: Path) -> tuple[int, list[float]] | None:
    """(ahash, title band) of an image file, or None if unreadable."""
    try:
        with Image.open(path) as im:
            return ahash(im), title_band(im)
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def same_image(h: int, band: list[float], r: Rendered,
               max_distance: int = SUSPECT_MAX_DISTANCE) -> int | None:
    """Hamming distance when (h, band) is the same photograph as ``r``: an
    ahash within ``max_distance`` of r's rendered or raw hash, CONFIRMED by
    the title band (see BAND_MIN_NCC). None otherwise."""
    best = None
    for rh, rb in ((r.ahash_out, None), (r.ahash_raw, r.band_raw)):
        d = hamming(h, rh)
        if d > max_distance:
            continue
        rband = r.band_out if rb is None else rb
        if band_ncc(band, rband) >= BAND_MIN_NCC and (best is None or d < best):
            best = d
    return best


class SuspectIndex:
    """Every known-bad image of one artist: exact sha256 + average hash
    (confirmed on the title band)."""

    def __init__(self, singles_root: Path, max_distance: int = SUSPECT_MAX_DISTANCE):
        self.root = singles_root
        self.max_distance = max_distance
        self.by_sha: dict[str, Path] = {}
        self.hashes: list[tuple[int, Path]] = []
        self.bands: dict[Path, list[float]] = {}
        for d in BAD_DIRS:
            base = singles_root / d
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in IMG_EXTS:
                    continue
                self._add(p)

    def _add(self, p: Path) -> None:
        self.by_sha.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), p)
        sig = _file_sig(p)
        if sig is not None:
            self.hashes.append((sig[0], p))
            self.bands[p] = sig[1]

    @classmethod
    def served(cls, singles_root: Path,
               max_distance: int = SUSPECT_MAX_DISTANCE) -> "SuspectIndex":
        """The same index over the SERVED tree (every non-``_``/Incorrect dir):
        a candidate that is already the image of another recording would
        serve one photograph for two recordings."""
        idx = cls.__new__(cls)
        idx.root, idx.max_distance = singles_root, max_distance
        idx.by_sha, idx.hashes, idx.bands = {}, [], {}
        if singles_root.is_dir():
            for p in sorted(singles_root.rglob("*.webp")):
                dirs = p.relative_to(singles_root).parts[:-1]
                if any(d.startswith("_") or d in BAD_DIRS for d in dirs):
                    continue
                idx._add(p)
        return idx

    def __len__(self) -> int:
        return len(self.hashes)

    def match(self, r: Rendered, exclude: Path | None = None) -> tuple[str, Path] | None:
        """('exact' | 'ahash d=N', indexed path) when ``r`` is an indexed image
        (``exclude``: a path that does not count, e.g. the target itself).
        An ahash hit counts only when the title band confirms it."""
        if r.sha in self.by_sha and self.by_sha[r.sha] != exclude:
            return "exact", self.by_sha[r.sha]
        best: tuple[int, Path] | None = None
        for h, p in self.hashes:
            if p == exclude:
                continue
            if min(hamming(h, r.ahash_out), hamming(h, r.ahash_raw)) > self.max_distance:
                continue
            d = same_image(h, self.bands.get(p, []), r, self.max_distance)
            if d is not None and (best is None or d < best[0]):
                best = (d, p)
        return (f"ahash d={best[0]}", best[1]) if best else None


# ---------- original-issue vs served-reissue (--replace-reissues) ----------
HARVEST_DATA = PARSE / "harvest_data"


class HarvestCatalog:
    """Label catalog read off a harvest crop, by the crop file itself.

    emit.py copies the verified crop byte-for-byte, so an emitted file is
    found among the extraction crops by size + sha256 (all artists: a crop
    can be adopted cross-artist). A crop recropped after emit falls back to
    the artist's current matches.csv by emitted stem. '' when unknown -- an
    unknown catalog is never evidence of an original issue.
    """

    def __init__(self, harvest_key: str, suffix: str,
                 data_root: Path | None = None):
        data_root = data_root or HARVEST_DATA
        self.by_size: dict[int, list[tuple[Path, str]]] = defaultdict(list)
        self.by_stem: dict[str, str] = {}
        for ex_csv in sorted((data_root / "extraction").glob("*/extracted.csv")):
            with ex_csv.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    p = Path(row.get("crop_path") or "")
                    try:
                        self.by_size[p.stat().st_size].append((p, row.get("catalog", "")))
                    except OSError:
                        continue
        matches = data_root / "match" / harvest_key / "matches.csv"
        if matches.is_file():
            with matches.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    iso = iso_date(row.get("date", ""))
                    title = (row.get("title") or "").strip()
                    if iso and title:
                        self.by_stem[f"{iso}_{title_segment(title)}_{suffix}".lower()] = \
                            row.get("label_catalog", "")

    def catalog_for(self, path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError:
            return ""
        same = self.by_size.get(size, [])
        if same:
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            for crop, cat in same:
                try:
                    if hashlib.sha256(crop.read_bytes()).hexdigest() == sha:
                        return cat
                except OSError:
                    continue
        stem = re.sub(r"_\d+$", "", path.stem).lower()
        return self.by_stem.get(stem, "")


def review_queue_originals(harvest_key: str, suffix: str,
                           data_root: Path | None = None) -> dict[str, list[tuple[Path, str]]]:
    """Harvest crops staged as ``already_have_image`` for a covered row, by
    lowercased target stem -> [(crop, label catalog)].

    match.py never emits a crop for a row that already has an image; it
    queues it for review instead. Those are exactly the original-issue
    labels that can replace a served reissue, so --replace-reissues reads
    them here (only those whose label catalog matches the row's disc and
    whose title agrees -- the caller re-checks the disc against the CSV).
    """
    import _verify_singles as vs
    path = (data_root or HARVEST_DATA) / "match" / harvest_key / "review_queue.csv"
    out: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    if not path.is_file():
        return out
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("reason") != "already_have_image":
                continue
            iso = iso_date(row.get("disco_date", ""))
            title = (row.get("disco_title") or "").strip()
            cat = row.get("label_catalog", "")
            crop = Path(row.get("crop_path") or "")
            if not (iso and title and cat) or not crop.is_file():
                continue
            if not vs.title_matches(title, row.get("label_title", "")):
                continue
            out[f"{iso}_{title_segment(title)}_{suffix}".lower()].append((crop, cat))
    return out


def _disc_agrees(expected: str, label: str) -> str:
    """'match' | 'mismatch' | 'unverifiable' (verify_singles' comparison)."""
    import _verify_singles as vs
    return vs.classify_disc(expected, label)


def report_keep_eras(singles_root: Path, disco_csv: Path | None) -> frozenset[str]:
    """Staging folders whose files stay distinct report keys (see
    verify_singles.keep_eras): non-year eras that are not a CSV Grouping."""
    import _verify_singles as vs
    eras = {p.name for p in singles_root.iterdir() if p.is_dir()} if singles_root.is_dir() else set()
    eras |= {r.get("era", "") or "" for r in vs.read_report(singles_root / "_verification_report.csv")}
    groupings: set[str] = set()
    if disco_csv is not None and disco_csv.exists():
        with disco_csv.open(encoding="utf-8-sig", newline="") as f:
            groupings = {(r.get("Grouping") or "").strip() for r in csv.DictReader(f)}
    return vs.staging_eras({e for e in eras if e and not e.startswith("_")}, groupings)


def load_report(singles_root: Path, keep_eras=frozenset()) -> dict[tuple, dict]:
    """_verification_report.csv rows by CANONICAL key (see
    _verify_singles.report_key): an old era-folder row and the year-bucket
    row of the same file are one entry, the most informative one (a stale
    ``unchecked`` duplicate used to hide a real ``reissue`` verdict here --
    three Troilo reissues). Look a served file up with :func:`report_row`."""
    import _verify_singles as vs
    return vs.report_index(vs.read_report(singles_root / "_verification_report.csv"),
                           keep_eras)


def report_row(report: dict[tuple, dict], dest: Path, keep_eras=frozenset()) -> dict | None:
    """The report row for the served file ``dest`` (Singles/<bucket>/<name>)."""
    import _verify_singles as vs
    return report.get(vs.report_key({"era": dest.parent.name, "filename": dest.name},
                                    keep_eras))


def backup_path(singles_root: Path, dest: Path, box: str = "_replaced") -> Path:
    """Free path for ``dest`` under Singles/<box>/<bucket>/ (``~N`` on collision)."""
    base = singles_root / box / dest.parent.name
    out = base / dest.name
    n = 1
    while out.exists():
        out = base / f"{dest.stem}~{n}{dest.suffix}"
        n += 1
    return out


def mark_report_replaced(singles_root: Path, dest: Path, note: str,
                         keep_eras=frozenset()) -> None:
    """Reset the served file ``dest``'s report row to ``unchecked`` with a
    note, so the row describes the NEW image (not yet vision-verified).

    The row is found by canonical key (old era-folder duplicates collapse
    into it; see _verify_singles.dedupe_report); a missing row is added.
    The report is rewritten atomically.
    """
    import _verify_singles as vs
    path = singles_root / "_verification_report.csv"
    rows, _ = vs.dedupe_report(vs.read_report(path), keep_eras)
    key = vs.report_key({"era": dest.parent.name, "filename": dest.name}, keep_eras)
    for row in rows:
        if vs.report_key(row, keep_eras) == key:
            row["status"] = "unchecked"
            row["notes"] = f"{note} | was: {row.get('notes', '')}"
            break
    else:
        meta = vs.parse_single_filename(dest.name)
        rows.append({"filename": dest.name, "era": key[0], "date": meta["date"],
                     "title": meta["title"], "artist": meta["artist"],
                     "status": "unchecked", "notes": note})
    vs.write_report(path, rows)


# ---------- per-artist source config ----------
def source_dirs(cfg: dict) -> list[Path]:
    dirs = []
    if cfg.get("tangoinfo"):
        dirs.append(PARSE / "TangoInfo data" / "ScrapeShellac" / "out" / cfg["tangoinfo"] / "images")
    if cfg.get("dahr"):
        dirs.append(PARSE / "DAHR Parsing" / "output" / cfg["dahr"] / "images")
    if cfg.get("discogs"):
        dirs.append(PARSE / "Tangos78" / "matched_discography" / cfg["discogs"])
    if cfg.get("harvest"):
        dirs.append(PARSE / "Marketplace Harvest" / "matched" / cfg["harvest"])
    return dirs


# local folder -> {display, csv, tangoinfo, dahr, discogs, harvest}
# tangoinfo: TangoInfo data/ScrapeShellac/out/<name>/images
# dahr:      DAHR Parsing/output/<name>/images   (omit if the artist isn't in DAHR)
# discogs:   Tangos78/matched_discography/<slug>
# harvest:   Marketplace Harvest/matched/<Key>   (appended LAST: curated sources win)
ARTISTS = {
    "Gardel":    {"display": "Carlos Gardel",     "csv": "Carlos Gardel.csv",
                  "tangoinfo": "Carlos_Gardel",    "dahr": "Gardel_Carlos",     "discogs": "carlos-gardel",
                  "harvest": "Gardel"},
    "Canaro":    {"display": "Francisco Canaro",   "csv": "Francisco Canaro.csv",
                  "tangoinfo": "Francisco_Canaro", "dahr": "Canaro_Francisco",  "discogs": "francisco-canaro",
                  "harvest": "Canaro"},
    "Firpo":     {"display": "Roberto Firpo",      "csv": "Roberto Firpo.csv",
                  "tangoinfo": "Roberto_Firpo",    "dahr": "Firpo_Roberto",     "discogs": "roberto-firpo",
                  "harvest": "Firpo"},
    "Fresedo":   {"display": "Osvaldo Fresedo",    "csv": "Osvaldo Fresedo.csv",
                  "tangoinfo": "Osvaldo_Fresedo",  "dahr": "Fresedo_Osvaldo",   "discogs": "osvaldo-fresedo",
                  "harvest": "Fresedo"},
    "DeCaro":    {"display": "Julio De Caro",      "csv": "Julio De Caro.csv",
                  "tangoinfo": "Julio_De_Caro",    "dahr": "Caro_Julio_de",     "discogs": "julio-de-caro",
                  "harvest": "DeCaro"},
    "Lomuto":    {"display": "Francisco Lomuto",   "csv": "Francisco Lomuto.csv",
                  "tangoinfo": "Francisco_Lomuto", "dahr": "Lomuto_Francisco_J", "discogs": "francisco-lomuto",
                  "harvest": "Lomuto"},
    "Demare":    {"display": "Lucio Demare",       "csv": "Lucio Demare.csv",
                  "tangoinfo": "Lucio_Demare",     "dahr": "Demare_Lucio",      "discogs": "lucio-demare"},
    "DeAngelis": {"display": "Alfredo De Angelis", "csv": "Alfredo De Angelis.csv",
                  "tangoinfo": "Alfredo_De_Angelis", "discogs": "alfredo-de-angelis",
                  "harvest": "DeAngelis"},
    "Piazzolla": {"display": "Astor Piazzolla",    "csv": "Astor Piazzolla.csv",
                  "tangoinfo": "Astor_Piazzolla",  "discogs": "astor-piazzolla",
                  "harvest": "Piazzolla"},
    "Basso":     {"display": "José Basso",         "csv": "José Basso.csv",
                  "tangoinfo": "Jose_Basso",       "discogs": "jose-basso"},
    "Francini":  {"display": "Enrique Francini",   "csv": "Enrique Francini.csv",
                  "tangoinfo": "Enrique_Francini", "discogs": "enrique-francini"},
    # --- expanded coverage (folder keys reuse existing images/<key> where present) ---
    "DArienzo":  {"display": "Juan D'Arienzo",     "csv": "Juan D'Arienzo.csv",
                  "tangoinfo": "Juan_DArienzo",    "dahr": "DArienzo_Juan",     "discogs": "juan-d-arienzo",
                  "harvest": "DArienzo"},
    "DiSarli":   {"display": "Carlos Di Sarli",    "csv": "Carlos Di Sarli.csv",
                  "tangoinfo": "Carlos_Di_Sarli",  "dahr": "Di_Sarli_Carlos",   "discogs": "carlos-di-sarli",
                  "harvest": "DiSarli"},
    "Troilo":    {"display": "Anibal Troilo",      "csv": "Anibal Troilo.csv",
                  "tangoinfo": "Anibal_Troilo",    "dahr": "Orquesta_Tpica_Anibal_Troilo", "discogs": "anibal-troilo",
                  "harvest": "Troilo"},
    "Tanturi":   {"display": "Ricardo Tanturi",    "csv": "Ricardo Tanturi.csv",
                  "tangoinfo": "Ricardo_Tanturi",  "dahr": "Tanturi_Ricardo",   "discogs": "ricardo-tanturi"},
    "Laurenz":   {"display": "Pedro Laurenz",      "csv": "Pedro Laurenz.csv",
                  "dahr": "Laurenz_Pedro",         "discogs": "pedro-laurenz"},
    "Pugliese":  {"display": "Osvaldo Pugliese",   "csv": "Osvaldo Pugliese.csv",
                  "tangoinfo": "Osvaldo_Pugliese", "discogs": "osvaldo-pugliese",
                  "harvest": "Pugliese"},
    "Biagi":     {"display": "Rodolfo Biagi",      "csv": "Rodolfo Biagi.csv",
                  "tangoinfo": "Rodolfo_Biagi",    "discogs": "rodolfo-biagi",
                  "harvest": "Biagi"},
    "Calo":      {"display": "Miguel Calo",        "csv": "Miguel Calo.csv",
                  "tangoinfo": "Miguel_Calo",      "discogs": "miguel-calo"},
    "Castillo":  {"display": "Alberto Castillo",   "csv": "Alberto Castillo.csv",
                  "tangoinfo": "Alberto_Castillo", "discogs": "alberto-castillo",
                  "harvest": "Castillo"},
    "DAgostino": {"display": "Angel D'Agostino",   "csv": "Angel D'Agostino.csv",
                  "dahr": "Agostino_Angel_d",      "discogs": "angel-d-agostino",
                  "harvest": "DAgostino"},
    "Vargas":    {"display": "Angel Vargas",       "csv": "Angel Vargas.csv",
                  "dahr": "Vargas_Angel",          "discogs": "angel-vargas",
                  "harvest": "Vargas"},
    "Rodriguez": {"display": "Enrique Rodriguez",  "csv": "Enrique Rodriguez.csv",
                  "dahr": "Rodrguez_Enrique",      "discogs": "enrique-rodriguez"},
    "Donato":    {"display": "Edgardo Donato",     "csv": "Edgardo Donato.csv",
                  "dahr": "Donato_Edgardo",        "discogs": "edgardo-donato",
                  "harvest": "Donato"},
    "Maglio":    {"display": "Juan Maglio",        "csv": "Juan Maglio.csv",
                  "dahr": "Maglio_Juan",           "discogs": "juan-maglio",
                  "harvest": "Maglio"},
    "Maffia":    {"display": "Pedro Maffia",       "csv": "Pedro Maffia.csv",
                  "dahr": "Maffia_Pedro",          "discogs": "pedro-maffia",
                  "harvest": "Maffia"},
    "Carabelli": {"display": "Adolfo Carabelli",   "csv": "Adolfo Carabelli.csv",
                  "dahr": "Carabelli_Adolfo",      "discogs": "adolfo-carabelli",
                  "harvest": "Carabelli"},
    "Aieta":     {"display": "Anselmo Aieta",      "csv": "Anselmo Aieta.csv",
                  "dahr": "Aieta_Anselmo",         "discogs": "anselmo-aieta",
                  "harvest": "Aieta"},
    "Cobian":    {"display": "Juan Carlos Cobian", "csv": "Juan Carlos Cobian.csv",
                  "tangoinfo": "Juan_Carlos_Cobian", "dahr": "Cobin_Juan_Carlos", "discogs": "juan-carlos-cobian",
                  "harvest": "Cobian"},
    "OTVictor":  {"display": "Orquesta Típica Victor", "csv": "Orquesta Típica Victor.csv",
                  "dahr": "Orquesta_Tpica_Victor", "discogs": "orquesta-tipica-victor",
                  "harvest": "OTVictor"},
    "Gobbi":     {"display": "Alfredo Gobbi",      "csv": "Alfredo Gobbi.csv",
                  "tangoinfo": "Alfredo_J_Gobbi",  "discogs": "alfredo-gobbi"},
    "Pontier":   {"display": "Armando Pontier",    "csv": "Armando Pontier.csv",
                  "tangoinfo": "Armando_Pontier"},
    "FedericoDomingo": {"display": "Domingo Federico", "csv": "Domingo Federico.csv",
                  "tangoinfo": "Domingo_Federico", "discogs": "domingo-federico"},
    "Maderna":   {"display": "Osmar Maderna",      "csv": "Osmar Maderna.csv",
                  "tangoinfo": "Osmar_Maderna",    "discogs": "osmar-maderna"},
    "Varela":    {"display": "Héctor Varela",      "csv": "Héctor Varela.csv",
                  "discogs": "hector-varela",      "harvest": "Varela"},
    "Salgan":    {"display": "Horacio Salgan",     "csv": "Horacio Salgan.csv",
                  "discogs": "horacio-salgan",     "harvest": "Salgan"},
    # Acoustic-era artists added Aug 2026. Magaldi has no DAHR talent page
    # (he never recorded for a US-linked label), so tangos78rpm is his only source.
    "Corsini":   {"display": "Ignacio Corsini",    "csv": "Ignacio Corsini.csv",
                  "dahr": "Corsini_Ignacio",       "discogs": "ignacio-corsini",
                  "harvest": "Corsini"},
    # 2026-10-02: the note above is stale — Magaldi DOES have a DAHR page
    # (mastertalent 107868, scraped to DAHR Parsing/output/Magaldi_Agustn).
    # Its raw-ID scans reach the site only through the harvest funnel, so
    # there is deliberately no "dahr" key; import new ones with --harvest-only.
    "Magaldi":   {"display": "Agustín Magaldi",    "csv": "Agustín Magaldi.csv",
                  "discogs": "agustin-magaldi",    "harvest": "Magaldi"},
    "Lamarque":  {"display": "Libertad Lamarque",  "csv": "Libertad Lamarque.csv",
                  "harvest": "Lamarque"},
    "Villoldo":  {"display": "Angel Villoldo",     "csv": "Angel Villoldo.csv",
                  "dahr": "Villoldo_Angel_Gregorio", "discogs": "angel-villoldo",
                  "harvest": "Villoldo"},
    "Greco":     {"display": "Vicente Greco",      "csv": "Vicente Greco.csv",
                  "dahr": "Greco_Vicente",         "discogs": "vicente-greco",
                  "harvest": "Greco"},
    "Quiroga":   {"display": "Rosita Quiroga",     "csv": "Rosita Quiroga.csv",
                  "dahr": "Quiroga_Rosita",        "discogs": "rosita-quiroga",
                  "harvest": "Quiroga"},
    "Delfino":   {"display": "Enrique Delfino",    "csv": "Enrique Delfino.csv",
                  "dahr": "Delfino_Enrique",       "discogs": "enrique-delfino",
                  "harvest": "Delfino"},
    # Wired 2026-10-02 (TangoInfo singer-named folders + tangos78rpm/DAHR scans
    # on disk), through the harvest funnel only: the TangoInfo files are raw
    # `<Singer>__<tinp>__sideN__<title>.jpg` names that carry no date, so there
    # is no filename-matched source to list. Import with --harvest-only.
    "Charlo":    {"display": "Charlo",             "csv": "Charlo.csv",
                  "harvest": "Charlo"},
    "Simone":    {"display": "Mercedes Simone",    "csv": "Mercedes Simone.csv",
                  "harvest": "Simone"},
    # Wired 2026-10-02 from the tangos78rpm/DAHR scans already on disk, through
    # the harvest funnel only (vision-read catalog/matrix): their DAHR folders
    # hold raw-ID filenames and composer-credit discs, so there is no
    # filename-matched source to list. Import with --harvest-only.
    "Ferrer":    {"display": "Celestino Ferrer",   "csv": "Celestino Ferrer.csv",
                  "harvest": "Ferrer"},
    "Esposito":  {"display": "Gennaro Esposito",   "csv": "Genaro Esposito.csv",  # csv filename has one n
                  "harvest": "Esposito"},
    "Ferrazzano": {"display": "Agesilao Ferrazzano", "csv": "Agesilao Ferrazzano.csv",
                  "harvest": "Ferrazzano"},
    "Bianco":    {"display": "Eduardo Bianco",     "csv": "Eduardo Bianco.csv",
                  "harvest": "Bianco"},
    "BiancoBachicha": {"display": "Bianco-Bachicha", "csv": "Bianco-Bachicha.csv",
                  "harvest": "BiancoBachicha"},
    "CanaroRafael": {"display": "Rafael Canaro",   "csv": "Rafael Canaro.csv",
                  "harvest": "CanaroRafael"},
    "Berto":     {"display": "Augusto Berto",      "csv": "Augusto Berto.csv",
                  "harvest": "Berto"},
    "Rotundo":   {"display": "Francisco Rotundo",  "csv": "Francisco Rotundo.csv",
                  "harvest": "Rotundo"},
    "Arolas":    {"display": "Eduardo Arolas",     "csv": "Eduardo Arolas.csv",
                  "harvest": "Arolas"},
    "Pollero":   {"display": "Julio Pollero",      "csv": "Julio Pollero.csv",
                  "harvest": "Pollero"},
    "DiCicco":   {"display": "Minotto Di Cicco",   "csv": "Minotto Di Cicco.csv",
                  "harvest": "DiCicco"},
    "Maizani":   {"display": "Azucena Maizani",    "csv": "Azucena Maizani.csv",
                  "harvest": "Maizani"},
    "Falcon":    {"display": "Ada Falcón",         "csv": "Ada Falcón.csv",
                  "harvest": "Falcon"},
    # Wired 2026-10-02 (Araque blogs + tangos78rpm/DAHR scans on disk), through
    # the harvest funnel only -- every image vision-read. Import with --harvest-only.
    "Pizarro":   {"display": "Manuel Pizarro",     "csv": "Manuel Pizarro.csv",
                  "harvest": "Pizarro"},
    "OTSelect":  {"display": "Orquesta Típica Select", "csv": "Orquesta Típica Select.csv",
                  "harvest": "OTSelect"},
    "Loduca":    {"display": "Vicente Loduca",     "csv": "Vicente Loduca.csv",
                  "harvest": "Loduca"},
}


def run(local: str, args) -> int:
    if local not in ARTISTS:
        print(f"error: unknown artist '{local}'. Add it to ARTISTS.", file=sys.stderr)
        return 2
    cfg = ARTISTS[local]
    harvest_only = getattr(args, "harvest_only", False)
    replace_reissues = getattr(args, "replace_reissues", False)
    if harvest_only and not cfg.get("harvest"):
        print(f"error: --harvest-only: '{local}' has no harvest source "
              "(add a \"harvest\" key to its ARTISTS entry)", file=sys.stderr)
        return 2
    if harvest_only and args.clean:
        print("error: --harvest-only cannot be combined with --clean: every file "
              "from the other sources would look like a stray and be deleted",
              file=sys.stderr)
        return 2
    disco_csv = REPO / "csv_files" / cfg["csv"]
    bandleader = cfg["display"]
    singles_root = REPO / "images" / local / "Singles"

    exact, byyear, n_rows, n_indexed = build_disco_index(disco_csv, bandleader)
    print(f"disco: {n_rows} rows, {n_indexed} imageable (have Date+Title)")

    all_dirs = source_dirs(cfg)
    harvest_dir = all_dirs[-1] if cfg.get("harvest") else None
    dirs = [harvest_dir] if harvest_only else all_dirs
    sources = collect_sources(dirs)
    print(f"sources: {len(sources)} parsable images from {len([d for d in dirs if d.is_dir()])} dir(s)"
          + (" [--harvest-only]" if harvest_only else ""))

    # Source priority for tie-breaks: tangoinfo (0) > dahr (1) > discogs (2), by dir order.
    src_priority: dict[Path, int] = {d: i for i, d in enumerate(all_dirs)}

    def priority_of(path: Path) -> int:
        return src_priority.get(path.parent, len(src_priority))

    def is_harvest(path: Path) -> bool:
        return harvest_dir is not None and path.parent == harvest_dir

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
    target_of: dict[tuple, Target] = {}
    unmatched: list[tuple[str, str, Path]] = []
    for date, title, path in sources:
        tgts = match_targets(date, title, exact, byyear)
        if not tgts:
            unmatched.append((date, title, path))
            continue
        for t in tgts:
            candidates[(t.bucket, t.stem)].append(path)
            if t.key() not in target_of or (t.disc and not target_of[t.key()].disc):
                target_of[t.key()] = t

    MIN_EDGE = 250  # skip tiny scans when a usable candidate exists

    def score(path: Path):
        # A harvest crop outranks everything, resolution included. It is the only
        # candidate whose *identity* was established rather than inferred: a vision
        # pass read the title and catalog number off that label and match.py tied
        # them to this discography row. Every other source is matched on a filename's
        # date+title, which cannot tell two recordings of one title apart. Trading
        # that for a larger unverified scan is how the wrong pressing gets served
        # (see the popsike Caminito del Taller, which lost to a Discogs scan of a
        # target a previous vision pass had already quarantined).
        # Then: bigger is better, bucketing the long edge (//400) so near-ties fall
        # through to source priority; negate priority so tangoinfo (0) sorts ahead
        # of discogs (2).
        return (is_harvest(path), long_edge(path) // 400, -priority_of(path))

    chosen: dict[tuple, Path] = {}
    ranked_all: dict[tuple, list[Path]] = {}
    runner_ups: list[tuple[str, str, str]] = []  # (target, chosen_path, alt_paths)
    for key, paths in candidates.items():
        uniq = list(dict.fromkeys(paths))  # a source can match a target via >1 heuristic
        usable = [p for p in uniq if long_edge(p) >= MIN_EDGE]
        pool = usable if usable else uniq
        ranked = sorted(pool, key=score, reverse=True)
        chosen[key] = ranked[0]
        alts = ranked[1:] + [p for p in uniq if p not in pool]
        ranked_all[key] = [ranked[0]] + alts
        if alts:
            runner_ups.append((f"{key[0]}/{key[1]}.webp", str(ranked[0]),
                               "|".join(str(p) for p in alts)))

    print(f"matched -> {len(chosen)} distinct target images; {len(unmatched)} source images unmatched")

    # Record runner-up candidates so a later retro pass can revisit choices.
    # --apply only: a dry-run must leave the tree untouched.
    if runner_ups:
        cand_csv = REPO / "images" / local / "_import_candidates.csv"
        if args.apply:
            cand_csv.parent.mkdir(parents=True, exist_ok=True)
            with cand_csv.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["target", "chosen_path", "alt_paths"])
                w.writerows(sorted(runner_ups))
            print(f"recorded {len(runner_ups)} multi-candidate targets -> {cand_csv.relative_to(REPO)}")
        else:
            print(f"{len(runner_ups)} multi-candidate targets (recorded to "
                  f"{cand_csv.relative_to(REPO)} on --apply only)")

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

    suspects = SuspectIndex(singles_root)
    served_idx = SuspectIndex.served(singles_root)
    print(f"quarantine: {len(suspects)} known-bad image(s) under "
          f"{'/'.join(BAD_DIRS)} checked against every candidate "
          f"(and {len(served_idx)} served images, for one-photo-two-recordings)")

    _rendered: dict[Path, Rendered | None] = {}

    def rendered(path: Path) -> Rendered | None:
        if path not in _rendered:
            _rendered[path] = render_webp(path, args.quality)
        return _rendered[path]

    min_import = getattr(args, "min_import_px", MIN_IMPORT_PX)
    min_replace = getattr(args, "min_replace_px", MIN_REPLACE_PX)
    too_small = [0]

    def first_clean(key: tuple, cands: list[Path], dest: Path):
        """First candidate (in rank order) that renders, is large enough and
        is not known-bad."""
        for src in cands:
            r = rendered(src)
            if r is None:
                continue
            if r.short_side < min_import:
                w, h = r.size
                print(f"  too small to import: {dest.relative_to(REPO)} <- {src.name} "
                      f"({src.parent.name}) is {w}x{h} (< {min_import} px)")
                too_small[0] += 1
                continue
            hit = suspects.match(r)
            if hit:
                how, bad = hit
                print(f"  suspect-skip: {dest.relative_to(REPO)} <- {src.name} "
                      f"({src.parent.name}) matches {bad.relative_to(singles_root)} [{how}]")
                continue
            dup = served_idx.match(r, exclude=dest)
            if dup:
                how, other = dup
                print(f"  served-dup-skip: {dest.relative_to(REPO)} <- {src.name} "
                      f"({src.parent.name}) is already served as "
                      f"{other.relative_to(singles_root)} [{how}]")
                served_dup[0] += 1
                continue
            return src, r
        return None

    served_dup = [0]

    # Writes: targets whose exact-case webp isn't already present. With --clean we
    # regenerate all (strays are wiped first, so nothing is "already present").
    present = {(b, n) for b in target_by_dir for n in actual_names(singles_root / b)}
    writes: list[tuple[Path, Path, Rendered]] = []
    already = suspect_blocked = suspect_fallback = 0
    present_keys: list[tuple] = []
    for key in sorted(chosen):
        bucket, stem = key
        dest = singles_root / bucket / f"{stem}.webp"
        if not args.clean and (bucket, f"{stem}.webp") in present:
            already += 1
            present_keys.append(key)
            continue
        picked = first_clean(key, ranked_all[key], dest)
        if picked is None:
            suspect_blocked += 1
            continue
        if picked[0] != chosen[key]:
            suspect_fallback += 1
            print(f"  fallback: {dest.relative_to(REPO)} <- {picked[0].name} "
                  f"({picked[0].parent.name}), next-ranked clean candidate")
        writes.append((picked[0], dest, picked[1]))
    print(f"would write {len(writes)} webp ({already} already present, exact case)")
    print(f"suspect-skip: {suspect_blocked} target(s) skipped (every candidate is a "
          f"quarantined image or already another recording's image); "
          f"{suspect_fallback} written from a lower-ranked clean candidate; "
          f"{served_dup[0]} candidate(s) skipped as already served elsewhere; "
          f"{too_small[0]} candidate(s) too small to import (< {min_import} px)")

    # --replace-reissues: the one sanctioned overwrite (see module docstring).
    replaces: list[tuple[Path, Path, Rendered, str]] = []
    if replace_reissues and harvest_dir is not None:
        cat_index = HarvestCatalog(cfg["harvest"], artist_suffix(bandleader))
        queued = review_queue_originals(cfg["harvest"], artist_suffix(bandleader))
        keep = report_keep_eras(singles_root, disco_csv)
        report = load_report(singles_root, keep)
        # Served targets reachable only through the review queue (no source
        # file maps to them) are candidates too.
        by_stem: dict[str, Target] = {}
        for lst in exact.values():
            for t in lst:
                if t.stem.lower() not in by_stem or (t.disc and not by_stem[t.stem.lower()].disc):
                    by_stem[t.stem.lower()] = t
        consider = list(present_keys)
        for stem_l in queued:
            t = by_stem.get(stem_l)
            if t is None or t.key() in chosen:
                continue
            if f"{t.stem}.webp" in actual_names(singles_root / t.bucket):
                consider.append(t.key())
                target_of[t.key()] = t
        for key in consider:
            tgt = target_of.get(key)
            if tgt is None or not tgt.disc:
                continue
            dest = singles_root / key[0] / f"{key[1]}.webp"
            harvest_cands = [(p, cat_index.catalog_for(p))
                             for p in ranked_all.get(key, []) if is_harvest(p)]
            harvest_cands += queued.get(key[1].lower(), [])
            if not harvest_cands:
                continue
            originals, reissues = [], []
            for p, cat in harvest_cands:
                verdict = _disc_agrees(tgt.disc, cat) if cat else "unverifiable"
                if verdict == "match":
                    originals.append((p, cat))
                elif verdict == "mismatch":
                    reissues.append((p, cat))
            if not originals:
                continue
            served_sig = _file_sig(dest)
            why = ""
            row = report_row(report, dest, keep)
            if row and row.get("status") == "reissue":
                why = f"report: reissue (label {row.get('disc_on_label', '') or '?'})"
            elif row and row.get("status") in ("ok", "cover") and row.get("disc_verdict") == "mismatch":
                why = f"report: disc mismatch (label {row.get('disc_on_label', '') or '?'})"
            elif served_sig is not None:
                for p, cat in reissues:
                    r = rendered(p)
                    if r and same_image(*served_sig, r) is not None:
                        why = f"served image is harvest crop {p.name} (label {cat})"
                        break
            if not why:
                continue
            for p, cat in originals:
                r = rendered(p)
                if r is None or suspects.match(r) or served_idx.match(r, exclude=dest):
                    continue
                if served_sig is not None and same_image(*served_sig, r) is not None:
                    break              # the original IS what is served already
                if r.short_side < min_replace:
                    w, h = r.size
                    print(f"  too small to replace: {dest.relative_to(REPO)} <- {p.name} "
                          f"is {w}x{h} (< {min_replace} px); the reissue stays")
                    continue
                replaces.append((p, dest, r,
                                 f"{why}; original label {cat} = Disc {tgt.disc}"))
                break
        verb = "replace" if args.apply else "would replace"
        for src, dest, _, why in replaces:
            print(f"  {verb} reissue: {dest.relative_to(REPO)} <- {src.name} [{why}]")
        print(f"replace-reissues: {len(replaces)} served reissue(s) "
              f"{'to replace' if args.apply else 'would be replaced'} by the original issue")
    elif replace_reissues and harvest_dir is None:
        print("replace-reissues: no harvest source for this artist; nothing to do")

    if args.show_unmatched:
        print("\n-- sample unmatched sources --")
        for date, title, path in unmatched[:40]:
            print(f"  {date}  {title!r}  <- {path.name}")

    if not harvest_only:
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
    for src, dest, r in writes:
        if write_webp(src, dest, args.quality, r):
            wrote += 1
    print(f"wrote {wrote} webp into {singles_root.relative_to(REPO)}")

    replaced = 0
    for src, dest, r, why in replaces:
        backup = backup_path(singles_root, dest)
        backup.parent.mkdir(parents=True, exist_ok=True)
        os.replace(dest, backup)
        if write_webp(src, dest, args.quality, r):
            replaced += 1
            w, h = r.size
            mark_report_replaced(singles_root, dest,
                                 f"replaced by original issue {src.name} ({w}x{h}); "
                                 f"re-verify", keep)
            print(f"  replaced: {dest.relative_to(REPO)} (reissue kept at "
                  f"{backup.relative_to(REPO)})")
        else:
            os.replace(backup, dest)       # restore the reissue; never leave a hole
            print(f"  warn: replace failed, restored {dest.relative_to(REPO)}", file=sys.stderr)
    if replaces:
        print(f"replaced {replaced} reissue(s); originals of the replaced files are in "
              f"{(singles_root / '_replaced').relative_to(REPO)}")

    if args.upload:
        print("\n== uploading via sync_artist_images.py ==")
        import subprocess
        cmd = [sys.executable, str(REPO / "sync_artist_images.py"), local]
        if replaced:
            # a replaced file changed content under an existing key; sync's
            # HEAD-exists skip would otherwise leave the reissue live on R2
            print("  note: replaced files need a forced upload: "
                  f"python upload_files.py (or sync_artist_images.py {local} --force)")
        rc = subprocess.call(cmd, cwd=str(REPO))
        print("next: python sync_thumbs.py --apply (thumbnails + singles_manifest.txt), "
              "then commit singles_manifest.txt -- the site only shows singles listed in it")
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
    ap.add_argument("--harvest-only", action="store_true",
                    help="import only vision-matched harvest crops (Marketplace Harvest/matched/<Key>/)")
    ap.add_argument("--replace-reissues", action="store_true",
                    help="let an ORIGINAL-issue harvest crop replace a served image recorded as a "
                         "reissue (backup kept under Singles/_replaced/)")
    ap.add_argument("--min-replace-px", type=int, default=MIN_REPLACE_PX,
                    help="--replace-reissues: an original whose shorter side (as written) is "
                         f"below this never replaces a reissue (default {MIN_REPLACE_PX})")
    ap.add_argument("--min-import-px", type=int, default=MIN_IMPORT_PX,
                    help="skip candidates whose shorter side (as written) is below this "
                         f"(default {MIN_IMPORT_PX}); logged as too small to import")
    args = ap.parse_args()
    global CROP_LABELS
    CROP_LABELS = not args.no_crop
    return run(args.artist, args)


if __name__ == "__main__":
    sys.exit(main())
