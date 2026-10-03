#!/usr/bin/env python3
"""Rebuild image_credits.txt: which source each served image came from.

The viewer shows a small "Image: <source>" credit under every image whose
provenance is on record. This script reconstructs that record from what is on
disk and writes ``image_credits.txt`` (site root, fetched by index.html next
to ``singles_manifest.txt``).

    python build_image_credits.py            # rebuild image_credits.txt
    python build_image_credits.py --check    # report only, write nothing

Run it after ``sync_thumbs.py --apply`` and ``build.py`` (it reads
``singles_manifest.txt`` and ``discographies.csv``), then commit
``image_credits.txt``. It reads the local ``images/`` tree and the sibling
``parse-tango-discographies`` repo, touches neither, and never talks to R2.

It never guesses. A credit is written only when a record ties the served
image to a source:

* 78 rpm singles -- the served file must be the SAME PHOTOGRAPH as a file in
  one of the artist's source folders (the folders import_singles.py reads,
  plus the harvest extraction crops, each of which carries its source and
  listing URL in ``extracted.csv``). "Same photograph" is import_singles'
  own test: 256-bit average hash within a few bits AND the label's title
  band correlating >= 0.95 (two different titles on one label design fail
  the band). A filename that merely maps to the same recording is not
  enough -- many singles pre-date the importer and came from elsewhere.
  If the photograph is found under two different sources, no credit is
  written (ambiguous).
* LP/EP/CD covers -- per album folder, from the fetch logs
  (``images/<Key>/_{itunes,bandcamp,deezer,discogs}_fetch_log.csv``): a row
  that records the cover download into that folder. A Discogs row counts
  only when the folder still holds exactly what the fetcher wrote
  (hand-curated folders are left uncredited).

Everything else gets no entry; the site-level "Image sources" section is the
only credit for those. The run prints per-source and per-artist counts,
including how many served images remain unattributed and why.

Signatures of every image read are cached in ``images/_image_credits_cache.pkl``
(gitignored, keyed by path + size + mtime), so a re-run only opens files that
changed. Output is deterministic: same inputs, byte-identical file.

File format (tab-separated, UTF-8, LF)::

    # comment
    >FresedoOsvaldo/Singles/1920-1924/|_Fresedo.webp     group: key prefix | key suffix
    <TAB>1920-09-03_Nueva-York<TAB>a<TAB>fresedotracks/2023/03/00182-nueva-york
    >SarliCarlosDi/LPs/|                                 album folders: prefix only
    <TAB>Bahia Blanca<TAB>dg<TAB>1234567

An entry's key is prefix + name + suffix; then the source code (see SOURCES)
and an optional reference from which index.html builds the per-image link
with a fixed per-source URL template (the file never carries a raw URL, so it
cannot point the viewer anywhere but the listed sources).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pickle
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parent
PARSE = REPO.parent / "parse-tango-discographies"
OUT_NAME = "image_credits.txt"
CACHE_NAME = "_image_credits_cache.pkl"
IMAGE_BASE = "https://images.tangotoolkit.com/"

# code -> display name. index.html carries the same codes (CREDIT_SOURCES)
# with the link templates; test_build_image_credits.py checks they agree.
SOURCES = {
    "a": "José Manuel Araque / GuardiaVieja.org",
    "d": "DAHR, UC Santa Barbara Library",
    "ti": "tango.info",
    "t78": "tangos78rpm.com",
    "ia": "Internet Archive (Great 78 Project)",
    "45": "45cat",
    "ap": "astorpiazzolla.com",
    "eb": "eBay listing",
    "ml": "Mercado Libre listing",
    "ps": "popsike",
    "dg": "Discogs",
    "it": "Apple Music / iTunes",
    "bc": "Bandcamp",
    "dz": "Deezer",
}

# harvest cache source name -> code
HARVEST_CODE = {
    "araque": "a", "josemanuel": "a", "dahr": "d", "tangoinfo": "ti",
    "tangos78": "t78", "archive": "ia", "45cat": "45", "astorpiazzolla": "ap",
    "ebay": "eb", "mercadolibre": "ml", "popsike": "ps",
}

# Per-source reference: (regex over the source URL, ref built from its groups).
# The ref is only what the URL template in index.html needs. A URL that does
# not fit its source's shape yields no ref (the credit is then unlinked).
ARAQUE_BLOGS = ("fresedotracks", "pizarrotracks", "cobiantracks",
                "decarotracks", "maffia-laurenztracks")
_REF_RULES = {
    "a": (re.compile(r"^https://(%s)\.blogspot\.com/(\d{4}/\d{2}/[A-Za-z0-9_-]+)\.html$"
                     % "|".join(ARAQUE_BLOGS)), r"\1/\2"),
    "d": (re.compile(r"^https?://adp\.library\.ucsb\.edu/index\.php/matrix/"
                     r"(?:refer|detail)/(\d+)(?:/.*)?$"), r"\1"),
    "ti": (re.compile(r"^https://tango\.info/(\d{8,14})$"), r"\1"),
    "t78": (re.compile(r"^https://www\.tangos78rpm\.com/([a-z0-9-]+)/?$"), r"\1"),
    "ia": (re.compile(r"^https://archive\.org/details/([A-Za-z0-9_.-]+)$"), r"\1"),
    "45": (re.compile(r"^https://www\.45cat\.com/(78rpm/record/[A-Za-z0-9]+)$"), r"\1"),
    "eb": (re.compile(r"^https://www\.ebay\.com/itm/(\d+)$"), r"\1"),
    "ml": (re.compile(r"^https://www\.mercadolibre\.com\.ar/([A-Za-z0-9/_-]+)$"), r"\1"),
    "ps": (re.compile(r"^https://www\.popsike\.com/([A-Za-z0-9_-]+/\d+)\.html$"), r"\1"),
    "dg": (re.compile(r"^(\d+)$"), r"\1"),
    "it": (re.compile(r"^([a-z]{2}/\d+)$"), r"\1"),
    "bc": (re.compile(r"^https://([a-z0-9-]+)\.bandcamp\.com/((?:album|track)/[a-z0-9-]+)$"), r"\1/\2"),
    "dz": (re.compile(r"^(\d+)$"), r"\1"),
}


def ref_for(code: str, url: str) -> str:
    """Compact per-image reference for ``url`` under source ``code`` ('' if
    the URL is missing or does not have that source's shape)."""
    rule = _REF_RULES.get(code)
    m = rule[0].match((url or "").strip()) if rule else None
    return m.expand(rule[1]) if m else ""


def harvest_credit(source: str, listing_url: str, listing_id: str = "") -> tuple[str, str] | None:
    """(code, ref) for a harvest-cache record, or None for an unknown source."""
    code = HARVEST_CODE.get((source or "").strip())
    if code is None:
        return None
    url = listing_url
    if code == "ia" and not url and listing_id:
        # archive rows carry the item identifier as listing_id (+ __label_NN)
        url = "https://archive.org/details/" + re.sub(r"__(?:label|photo)_\d+$", "", listing_id)
    if source == "josemanuel":      # local copy of Araque's scans: no post URL
        url = ""
    return code, ref_for(code, url)


# ---------------------------------------------------------------- file format
def render_credits(entries: dict[str, tuple[str, str]]) -> str:
    """image_credits.txt text for {key: (code, ref)} (deterministic)."""
    groups: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for key, (code, ref) in entries.items():
        if code not in SOURCES:
            raise ValueError(f"unknown source code {code!r} for {key}")
        if any(c in key + ref for c in "\t\r\n"):
            raise ValueError(f"control character in entry {key!r}")
        head, _, name = key.rpartition("/")
        suffix = ""
        if "/Singles/" in key and "_" in name:
            cut = name.rfind("_")
            name, suffix = name[:cut], name[cut:]
        groups[(head + "/", suffix)].append((name, code, ref))
    lines = ["# image_credits.txt -- image source credits; generated by "
             "build_image_credits.py, do not edit.",
             "# >prefix|suffix opens a group; entries are <TAB>name<TAB>source[<TAB>ref]."]
    for (prefix, suffix) in sorted(groups):
        lines.append(f">{prefix}|{suffix}")
        for name, code, ref in sorted(groups[(prefix, suffix)]):
            lines.append("\t" + "\t".join([name, code] + ([ref] if ref else [])))
    return "\n".join(lines) + "\n"


def parse_credits(text: str) -> dict[str, tuple[str, str]]:
    """Inverse of render_credits (mirrors the parser in index.html)."""
    out: dict[str, tuple[str, str]] = {}
    prefix = suffix = None
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line.startswith(">"):
            prefix, _, suffix = line[1:].rpartition("|")
        elif line.startswith("\t") and prefix is not None:
            parts = line[1:].split("\t")
            if len(parts) >= 2 and parts[0]:
                out[prefix + parts[0] + suffix] = (parts[1], parts[2] if len(parts) > 2 else "")
    return out


# ------------------------------------------------------------ image signatures
SIG_DIM = 1600          # signatures are taken at <= this long edge
MAX_DISTANCE = 3        # import_singles.SUSPECT_MAX_DISTANCE
MIN_NCC = 0.95          # import_singles.BAND_MIN_NCC
# An image with no filename link to the recording must agree more closely.
UNLINKED_MAX_DISTANCE = 2
UNLINKED_MIN_NCC = 0.97


def _sig_task(task: tuple[str, bool]):
    """Worker: (path, cropped?) -> (path, cropped?, sig). sig is (ahash int,
    title-band bytes), or None when unreadable / (cropped) when the label
    detector leaves the image as it is."""
    path, cropped = task
    try:
        import import_singles as im
        from PIL import Image
        with Image.open(path) as img:
            img.draft("RGB", (SIG_DIM, SIG_DIM))
            raw = img.convert("RGB")
        raw.thumbnail((SIG_DIM, SIG_DIM))
        if cropped:
            out = im._maybe_crop_label(raw)
            if out is raw or out.size == raw.size:
                return path, cropped, None
        else:
            out = raw
        band = bytes(max(0, min(255, int(round(v)))) for v in im.title_band(out))
        return path, cropped, (im.ahash(out), band)
    except Exception:
        return path, cropped, None


class SigCache:
    """path -> signature, persisted across runs; invalidated by size/mtime."""

    def __init__(self, path: Path | None, jobs: int = 1):
        self.path = path
        self.jobs = max(1, jobs)
        self.data: dict = {}
        self.dirty = False
        if path and path.is_file():
            try:
                self.data = pickle.loads(path.read_bytes())
            except Exception:
                self.data = {}

    @staticmethod
    def _stamp(p: str):
        try:
            st = os.stat(p)
        except OSError:
            return None
        return st.st_size, st.st_mtime_ns

    def ensure(self, paths, cropped: bool = False) -> None:
        """Compute (in parallel) every signature not cached yet."""
        todo = []
        for p in dict.fromkeys(str(x) for x in paths):
            stamp = self._stamp(p)
            hit = self.data.get((p, cropped))
            if stamp is None or (hit is not None and hit[0] == stamp):
                continue
            todo.append((p, stamp))
        if not todo:
            return
        tasks = [(p, cropped) for p, _ in todo]
        stamps = dict(todo)
        if self.jobs > 1 and len(tasks) > 8:
            with ProcessPoolExecutor(self.jobs) as ex:
                results = list(ex.map(_sig_task, tasks, chunksize=8))
        else:
            results = [_sig_task(t) for t in tasks]
        for p, c, sig in results:
            self.data[(p, c)] = (stamps[p], sig)
        self.dirty = True

    def get(self, path, cropped: bool = False):
        hit = self.data.get((str(path), cropped))
        return hit[1] if hit else None

    def sha(self, path) -> str | None:
        p = str(path)
        stamp = self._stamp(p)
        if stamp is None:
            return None
        hit = self.data.get((p, "sha"))
        if hit is None or hit[0] != stamp:
            try:
                hit = (stamp, hashlib.sha256(Path(p).read_bytes()).hexdigest())
            except OSError:
                return None
            self.data[(p, "sha")] = hit
            self.dirty = True
        return hit[1]

    def save(self) -> None:
        if self.path and self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(self.data, protocol=pickle.HIGHEST_PROTOCOL))
            os.replace(tmp, self.path)
            self.dirty = False


def _ncc(a: bytes, b: bytes) -> float:
    import import_singles as im
    return im.band_ncc(a, b)


def same_photo(a, b, max_distance: int, min_ncc: float) -> int | None:
    """Hamming distance when signatures a and b are the same photograph."""
    if not a or not b:
        return None
    d = (a[0] ^ b[0]).bit_count()
    if d > max_distance:
        return None
    return d if _ncc(a[1], b[1]) >= min_ncc else None


# ------------------------------------------------------------------ candidates
@dataclass
class Candidate:
    path: Path
    code: str
    ref: str
    targets: set = field(default_factory=set)   # {(bucket, stem)} linked by name


def _read_csv(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _slug(s: str) -> str:
    import import_singles as im
    return re.sub(r"[^a-z0-9]+", "-", im.strip_accents(s or "").lower()).strip("-")


class HarvestIndex:
    """Every harvest extraction crop, with the source record it came from."""

    def __init__(self, parse: Path):
        self.root = parse / "harvest_data"
        self.by_artist: dict[str, list[tuple[Path, str, str]]] = defaultdict(list)
        self.by_size: dict[int, list[Path]] = defaultdict(list)
        self.credit: dict[Path, tuple[str, str]] = {}
        ex_root = self.root / "extraction"
        if not ex_root.is_dir():
            return
        for ex_csv in sorted(ex_root.glob("*/extracted.csv")):
            key = ex_csv.parent.name
            sizes = {}
            crops = ex_csv.parent / "crops"
            if crops.is_dir():
                with os.scandir(crops) as it:
                    for e in it:
                        if e.is_file():
                            sizes[e.name.lower()] = e.stat().st_size
            for row in _read_csv(ex_csv):
                name = Path(row.get("crop_path") or "").name
                if not name or name.lower() not in sizes:
                    continue
                credit = harvest_credit(row.get("source", ""), row.get("listing_url", ""),
                                        row.get("listing_id", ""))
                if credit is None:
                    continue
                p = crops / name
                if p in self.credit:
                    continue
                self.credit[p] = credit
                self.by_artist[key].append((p, *credit))
                self.by_size[sizes[name.lower()]].append(p)

    def match_rows(self, key: str) -> list[dict]:
        return _read_csv(self.root / "match" / key / "matches.csv")

    def credit_of_copy(self, path: Path, cache: SigCache) -> tuple[Path, tuple[str, str]] | None:
        """The crop an emitted file is a byte-for-byte copy of (emit.py copies
        the verified crop unchanged), with its credit."""
        try:
            size = path.stat().st_size
        except OSError:
            return None
        same = self.by_size.get(size)
        if not same:
            return None
        sha = cache.sha(path)
        for crop in same:
            if sha and cache.sha(crop) == sha:
                return crop, self.credit[crop]
        return None


def _tangoinfo_refs(d: Path) -> dict[str, str]:
    """image filename (lowercase) -> tango.info product URL."""
    out: dict[str, str] = {}
    clash: set[str] = set()
    for row in _read_csv(d.parent / "shellac_tracks.csv"):
        name = (row.get("image_filename") or "").strip().lower()
        url = (row.get("product_url") or "").strip()
        if not name or not url:
            continue
        if name in out and out[name] != url:
            clash.add(name)
        out[name] = url
    for name in clash:
        del out[name]
    return out


def _dahr_refs(d: Path) -> dict[str, str]:
    """image filename (lowercase) -> DAHR matrix URL."""
    out: dict[str, str] = {}
    clash: set[str] = set()
    for csv_path in sorted(d.parent.glob("*.csv")):
        for row in _read_csv(csv_path):
            name = Path((row.get("Image_File") or "").strip()).name.lower()
            url = (row.get("Matrix_URL") or "").strip()
            if not name or not url:
                continue
            if name in out and out[name] != url:
                clash.add(name)
            out[name] = url
    for name in clash:
        del out[name]
    return out


_T78_REPORT: dict[Path, dict[tuple[str, str], set[str]]] = {}


def _tangos78_refs(d: Path) -> dict[str, str]:
    """image filename (lowercase) -> tangos78rpm.com record URL, from
    Tangos78/match_report.csv (bandleader + date + title -> record slug).
    A filename stem that two different records map to gets no URL."""
    report = d.parent.parent / "match_report.csv"
    if report not in _T78_REPORT:
        idx: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in _read_csv(report):
            if row.get("status") != "matched" or not row.get("matched_record_slug"):
                continue
            date = row.get("date", "")
            m = re.search(r"\d{4}", date)
            date = date[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", date) else (m.group(0) if m else "unknown")
            stem = f"{date}_{(_slug(row.get('title', '')) or 'untitled')[:80].rstrip('-')}_"
            idx[(_slug(row.get("bandleader", "")), stem)].add(row["matched_record_slug"])
        _T78_REPORT[report] = idx
    idx = _T78_REPORT[report]
    out: dict[str, str] = {}
    if not d.is_dir():
        return out
    by_stem = {stem: slugs for (bl, stem), slugs in idx.items() if bl == d.name}
    for name in os.listdir(d):
        low = name.lower()
        # <date>_<title-slug>_<lastname>.webp; a "_2" collision copy belongs
        # to a different report row and gets no URL.
        hits = {s for stem, slugs in by_stem.items()
                if low.startswith(stem) and re.fullmatch(r"[a-z-]+\.webp", low[len(stem):])
                for s in slugs}
        if len(hits) == 1:
            out[low] = "https://www.tangos78rpm.com/%s/" % next(iter(hits))
    return out


def artist_candidates(key: str, cfg: dict, repo: Path, parse: Path,
                      harvest: HarvestIndex, cache: SigCache) -> list[Candidate]:
    """Every source image on disk for one artist, with its credit and the
    recordings its filename (or its harvest match row) links it to."""
    import import_singles as im
    exact, byyear, _, _ = im.build_disco_index(repo / "csv_files" / cfg["csv"], cfg["display"])
    cands: dict[Path, Candidate] = {}

    def add(path: Path, code: str, ref: str, date: str = "", title: str = "") -> None:
        c = cands.get(path)
        if c is None:
            c = cands[path] = Candidate(path, code, ref)
        if date and title:
            c.targets.update(t.key() for t in im.match_targets(date, title, exact, byyear))

    dirs = []
    if cfg.get("tangoinfo"):
        d = parse / "TangoInfo data" / "ScrapeShellac" / "out" / cfg["tangoinfo"] / "images"
        dirs.append((d, "ti", _tangoinfo_refs(d)))
    if cfg.get("dahr"):
        d = parse / "DAHR Parsing" / "output" / cfg["dahr"] / "images"
        dirs.append((d, "d", _dahr_refs(d)))
    if cfg.get("discogs"):
        # import_singles calls this source "discogs"; the folder is the
        # tangos78rpm.com scrape matched to the discography.
        d = parse / "Tangos78" / "matched_discography" / cfg["discogs"]
        dirs.append((d, "t78", _tangos78_refs(d)))
    for d, code, urls in dirs:
        for date, title, p in im.collect_sources([d]):
            add(p, code, ref_for(code, urls.get(p.name.lower(), "")), date, title)

    hkey = cfg.get("harvest")
    if hkey:
        # extraction crops, linked to a recording through matches.csv
        for p, code, ref in harvest.by_artist.get(hkey, []):
            add(p, code, ref)
        for row in harvest.match_rows(hkey):
            p = harvest.root / "extraction" / hkey / "crops" / Path(row.get("crop_path") or "").name
            if p in cands:
                add(p, cands[p].code, cands[p].ref, row.get("date", ""), row.get("title", ""))
        # emitted copies (possibly of another artist's crop), linked by name
        d = parse / "Marketplace Harvest" / "matched" / hkey
        for date, title, p in im.collect_sources([d]):
            hit = harvest.credit_of_copy(p, cache)
            if hit is None:
                continue
            crop, (code, ref) = hit
            add(crop, code, ref, date, title)
    return list(cands.values())


# --------------------------------------------------------------------- singles
def r2_folder(repo: Path, cfg_csv: str, display: str) -> str:
    """R2 artist folder: bandleader_folder(Bandleader of the CSV's first row)."""
    import import_singles as im
    rows = _read_csv(repo / "csv_files" / cfg_csv)
    bandleader = (rows[0].get("Bandleader") or "").strip() if rows else ""
    return im.bandleader_folder(bandleader or display)


def served_singles(repo: Path, key: str) -> dict[tuple[str, str], Path]:
    """{(bucket, stem): path} of the artist's served local singles."""
    out = {}
    root = repo / "images" / key / "Singles"
    if not root.is_dir():
        return out
    for b in sorted(os.listdir(root)):
        if not re.fullmatch(r"\d{4}-\d{4}", b):
            continue
        for name in sorted(os.listdir(root / b)):
            if name.endswith(".webp"):
                out[(b, name[:-5])] = root / b / name
    return out


def decide(matches: list[tuple[int, int, str, str]]) -> tuple[str, str] | str:
    """Pick the credit from [(tier, distance, code, ref)] (tier 0 = linked by
    name, 1 = linked after label crop, 2 = image only). Returns (code, ref),
    or a reason string: 'none' (no match) / 'ambiguous' (the photograph is
    held under two different sources, so which one it was taken from is
    not on record). The per-image ref comes from the closest best-tier
    match, and is dropped when two equally close matches disagree."""
    if not matches:
        return "none"
    codes = {m[2] for m in matches}
    if len(codes) != 1:
        return "ambiguous"
    tier = min(m[0] for m in matches)
    best = [m for m in matches if m[0] == tier]
    dmin = min(m[1] for m in best if m[3]) if any(m[3] for m in best) else None
    refs = {m[3] for m in best if m[3] and m[1] == dmin}
    return codes.pop(), (refs.pop() if len(refs) == 1 else "")


def attribute_singles(key: str, cfg: dict, repo: Path, parse: Path,
                      harvest: HarvestIndex, cache: SigCache):
    """-> ({(bucket, stem): (code, ref)}, Counter of unattributed reasons)."""
    served = served_singles(repo, key)
    credits: dict[tuple[str, str], tuple[str, str]] = {}
    reasons: Counter = Counter()
    if not served:
        return credits, reasons
    cands = artist_candidates(key, cfg, repo, parse, harvest, cache)
    cache.ensure([*served.values(), *(c.path for c in cands)])
    linked: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for c in cands:
        for t in c.targets:
            linked[t].append(c)
    sigs = [(c, cache.get(c.path)) for c in cands]
    pending: dict[tuple[str, str], list[Candidate]] = {}
    for tkey, path in served.items():
        s = cache.get(path)
        if s is None:
            reasons["unreadable"] += 1
            continue
        own = {id(c) for c in linked.get(tkey, [])}
        matches = []
        for c, cs in sigs:
            if cs is None:
                continue
            if id(c) in own:
                d = same_photo(s, cs, MAX_DISTANCE, MIN_NCC)
                tier = 0
            else:
                d = same_photo(s, cs, UNLINKED_MAX_DISTANCE, UNLINKED_MIN_NCC)
                tier = 2
            if d is not None:
                matches.append((tier, d, c.code, c.ref))
        got = decide(matches)
        if got == "none" and own:
            pending[tkey] = linked[tkey]       # try the label-cropped source
        elif isinstance(got, str):
            reasons[got] += 1
        else:
            credits[tkey] = got
    if pending:
        cache.ensure([c.path for cl in pending.values() for c in cl], cropped=True)
        for tkey, cl in pending.items():
            s = cache.get(served[tkey])
            matches = []
            for c in cl:
                d = same_photo(s, cache.get(c.path, cropped=True), MAX_DISTANCE, MIN_NCC)
                if d is not None:
                    matches.append((1, d, c.code, c.ref))
            got = decide(matches)
            if isinstance(got, str):
                reasons["name-only" if got == "none" else got] += 1
            else:
                credits[tkey] = got
    return credits, reasons


# ---------------------------------------------------------------------- albums
_WIN_ILLEGAL = re.compile(r'[<>:"/\\|?*]')
_STD_TYPE = re.compile(r"^(Front|Back|Image \d+)$")


def safe_folder_name(title: str) -> str:
    """Parity with _discogs_drivers.safe_folder_name (release title -> folder)."""
    s = re.sub(r"\s+", " ", _WIN_ILLEGAL.sub(" ", title or "")).strip()
    return s.rstrip(". ")


def attribute_albums(root: Path) -> dict[tuple[str, str], tuple[str, str] | str]:
    """{(LPs|EPs, folder): (code, ref) | reason} for one images/<Key> folder.

    A folder is credited only from a fetch-log row recording the cover
    download into it; see the module docstring."""
    logs = {n: _read_csv(root / f"_{n}_fetch_log.csv")
            for n in ("discogs", "itunes", "bandcamp", "deezer")}
    try:
        discogs_mtime = (root / "_discogs_fetch_log.csv").stat().st_mtime
    except OSError:
        discogs_mtime = None
    out: dict[tuple[str, str], tuple[str, str] | str] = {}
    for sub, kind in (("LPs", "LP"), ("EPs", "EP")):
        base = root / sub
        if not base.is_dir():
            continue
        for e in sorted(os.scandir(base), key=lambda e: e.name):
            if not e.is_dir():
                continue
            F = e.name
            ents = list(os.scandir(e.path))
            webps = [x for x in ents if x.name.lower().endswith(".webp")]
            rasters = [x for x in ents if x.name.lower().endswith((".jpg", ".jpeg", ".png"))]
            front = any(x.name == F + " Front.webp" for x in webps)
            if not webps:
                continue
            hits = []
            for r in logs["itunes"]:
                if r.get("folder") == F and r.get("image") == "ok":
                    cc = (r.get("countries") or "").split()
                    hits.append(("it", f"{cc[0]}/{r.get('collection_id', '')}" if cc else ""))
            for r in logs["bandcamp"]:
                if r.get("folder") == F and r.get("image") == "ok":
                    hits.append(("bc", r.get("url", "")))
            for r in logs["deezer"]:
                if r.get("folder") == F and re.fullmatch(r"\d+x\d+", r.get("image") or ""):
                    hits.append(("dz", r.get("album_id", "")))
            dg = [r for r in logs["discogs"]
                  if r.get("bucket") == kind and r.get("status") == "ok"
                  and r.get("release_id") and safe_folder_name(r.get("title", "")) == F]
            if hits:
                if len({h[0] for h in hits}) > 1:
                    out[(sub, F)] = "two fetch logs claim the folder"
                elif kind != "LP" or len(webps) != 1 or not front:
                    out[(sub, F)] = "mixed folder (streaming cover + other files)"
                else:
                    refs = {ref_for(h[0], h[1]) for h in hits}
                    out[(sub, F)] = (hits[0][0], refs.pop() if len(refs) == 1 else "")
            elif dg:
                types = [x.name[len(F) + 1:-5] if x.name.startswith(F + " ") else "?" for x in webps]
                clean = (len(dg) == 1 and front and not rasters
                         and all(_STD_TYPE.match(t) for t in types)
                         and (dg[0].get("downloaded") or "").isdigit()
                         and int(dg[0]["downloaded"]) == len(webps)
                         and discogs_mtime is not None
                         and all(x.stat().st_mtime <= discogs_mtime + 120 for x in webps))
                out[(sub, F)] = (("dg", ref_for("dg", dg[0]["release_id"])) if clean
                                 else "Discogs log row, but the folder was hand-curated")
            else:
                out[(sub, F)] = "no fetch-log row"
    return out


def served_album_folders(repo: Path) -> set[tuple[str, str, str]] | None:
    """{(ArtistFolder, LPs|EPs, AlbumFolder)} referenced by LP_Images in
    discographies.csv; None when the file is missing/unreadable."""
    path = repo / "discographies.csv"
    out: set[tuple[str, str, str]] = set()
    try:
        csv.field_size_limit(10 ** 9)
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                v = (row.get("LP_Images") or "").strip()
                if not v:
                    continue
                try:
                    imgs = json.loads(v)
                except ValueError:
                    continue
                for im_ in imgs if isinstance(imgs, list) else []:
                    url = (im_ or {}).get("url") or ""
                    if url.startswith(IMAGE_BASE):
                        parts = unquote(url[len(IMAGE_BASE):]).split("/")
                        if len(parts) >= 4 and parts[1] in ("LPs", "EPs"):
                            out.add((parts[0], parts[1], parts[2]))
    except OSError:
        return None
    return out


# ------------------------------------------------------------------------ main
def build(repo: Path = REPO, parse: Path = PARSE, jobs: int = 1,
          cache_path: Path | None = None, out=print):
    """Attribute every served image; -> {key: (code, ref)}. The report goes to ``out``."""
    import import_singles as im
    from _artist_map import ARTIST_DISPLAY

    cache = SigCache(cache_path, jobs)
    harvest = HarvestIndex(parse)
    entries: dict[str, tuple[str, str]] = {}

    manifest: set[str] = set()
    try:
        manifest = {l.strip() for l in (repo / "singles_manifest.txt").read_text(
            encoding="utf-8").splitlines() if l.strip()}
    except OSError:
        out("warn: singles_manifest.txt not found; using local files only")

    # ---- singles
    out("== 78 rpm singles ==")
    out(f"{'artist':<18}{'served':>7}{'credited':>9}{'none':>6}  unattributed because")
    by_source: Counter = Counter()
    linked_n = 0
    totals = Counter()
    seen_keys: set[str] = set()
    rows = []
    for key in sorted(im.ARTISTS):
        cfg = im.ARTISTS[key]
        served = served_singles(repo, key)
        folder = r2_folder(repo, cfg["csv"], cfg["display"])
        mine = {k for k in manifest if k.startswith(folder + "/Singles/")}
        if not served and not mine:
            continue
        credits, reasons = attribute_singles(key, cfg, repo, parse, harvest, cache)
        cache.save()
        local_keys = set()
        for (bucket, stem), path in served.items():
            k = f"{folder}/Singles/{bucket}/{stem}.webp"
            local_keys.add(k)
            if (bucket, stem) in credits:
                entries[k] = credits[(bucket, stem)]
                by_source[entries[k][0]] += 1
                linked_n += bool(entries[k][1])
        nolocal = len(mine - local_keys)
        if nolocal:
            reasons["no local file"] += nolocal
        seen_keys |= local_keys | mine
        n = len(local_keys | mine)
        rows.append((key, n, len(credits), n - len(credits), reasons))
        totals["served"] += n
        totals["credited"] += len(credits)
    for key, n, c, u, reasons in rows:
        why = ", ".join(f"{v} {k}" for k, v in reasons.most_common())
        out(f"{key:<18}{n:>7}{c:>9}{u:>6}  {why}")
    orphan = manifest - seen_keys
    if orphan:
        folders = Counter(k.split("/", 1)[0] for k in orphan)
        out(f"manifest keys of artists not in import_singles.ARTISTS (uncredited): "
            f"{len(orphan)} {dict(folders)}")
        totals["served"] += len(orphan)
    out(f"{'TOTAL':<18}{totals['served']:>7}{totals['credited']:>9}"
        f"{totals['served'] - totals['credited']:>6}")
    out("reasons: none = photograph not found in any source folder; name-only = a source "
        "file maps to the recording but is a different photograph; ambiguous = found under "
        "two sources; no local file = in the manifest but not in images/.")

    # ---- albums
    out("\n== LP / EP / CD covers (per album folder) ==")
    served_albums = served_album_folders(repo)
    if served_albums is None:
        out("warn: discographies.csv unreadable; crediting every local album folder")
    alb_source: Counter = Counter()
    alb_unknown: Counter = Counter()
    alb_why: Counter = Counter()
    n_alb = 0
    images = repo / "images"
    covered: set[tuple[str, str, str]] = set()
    for key in sorted(os.listdir(images)) if images.is_dir() else []:
        root = images / key
        display = ARTIST_DISPLAY.get(key)
        if not display or not root.is_dir():
            continue
        folder = r2_folder(repo, im.ARTISTS.get(key, {}).get("csv", display + ".csv"), display)
        for (sub, F), got in attribute_albums(root).items():
            if served_albums is not None and (folder, sub, F) not in served_albums:
                continue
            covered.add((folder, sub, F))
            n_alb += 1
            if isinstance(got, str):
                alb_unknown[key] += 1
                alb_why[got] += 1
            else:
                entries[f"{folder}/{sub}/{F}"] = got
                alb_source[got[0]] += 1
                linked_n += bool(got[1])
    if served_albums is not None:
        missing = served_albums - covered
        if missing:
            alb_why["no local folder"] += len(missing)
            n_alb += len(missing)
    out(f"served album folders: {n_alb}; credited: {sum(alb_source.values())}; "
        f"uncredited: {n_alb - sum(alb_source.values())}")
    for why, n in alb_why.most_common():
        out(f"  uncredited, {why}: {n}")
    if alb_unknown:
        out("  uncredited by artist: " + ", ".join(f"{k} {v}" for k, v in alb_unknown.most_common(12)))

    out("\n== credits by source ==")
    for code in SOURCES:
        n = by_source[code] + alb_source[code]
        if n:
            out(f"  {code:<4}{SOURCES[code]:<40}{n:>6}")
    out(f"  total entries: {len(entries)} ({linked_n} with a per-image link)")
    cache.save()
    return entries


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report only; do not write the file")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                    help="worker processes for reading images (default: %(default)s)")
    ap.add_argument("--no-cache", action="store_true", help="ignore and do not write the signature cache")
    ap.add_argument("--parse-repo", type=Path, default=PARSE,
                    help="path to parse-tango-discographies (default: sibling folder)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.parse_repo.is_dir() or not (REPO / "images").is_dir():
        print("error: need the local images/ tree and the parse-tango-discographies repo "
              f"({args.parse_repo}); without them every credit would be dropped. Nothing written.",
              file=sys.stderr)
        return 2
    cache_path = None if args.no_cache else REPO / "images" / CACHE_NAME
    entries = build(REPO, args.parse_repo, args.jobs, cache_path)
    text = render_credits(entries)
    dest = REPO / OUT_NAME
    old = dest.read_text(encoding="utf-8") if dest.is_file() else None
    print(f"\n{OUT_NAME}: {len(entries)} entries, {len(text.encode('utf-8')) / 1024:.0f} KB"
          + (" (unchanged)" if old == text else ""))
    if args.check:
        print("(--check: nothing written)")
    elif old != text:
        with dest.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
