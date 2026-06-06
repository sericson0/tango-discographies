"""Year-flex matcher reused for LPs.csv and EPs.csv against a per-artist discography."""
from __future__ import annotations

import csv
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

FUZZY_MIN = 0.84

OUTPUT_COLS = [
    "LP_Folder", "LP_Catalog", "LP_Title", "Side", "Track_No", "Track_Title",
    "Track_Year", "Match_Status", "Disc_Date", "Disc_Title", "Disc_AltTitle",
    "Singer", "Master", "Matrix", "Note", "Kind",
]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Genre/form labels LPs append to track titles (e.g. "Tierra Querida - Tango").
# Ordered longest-first so multi-word labels match before their prefixes.
_GENRES = (
    r"(?:tango canci[oó]n|milonga candombe|paso doble|fox trot|tango|milonga|"
    r"vals|candombe|foxtrot|ranchera|zamba|estilo|marcha|polca)"
)
_TRAIL_DATE = re.compile(r"[(\[]?\s*\d{1,2}/\d{1,2}/\d{2,4}\s*[)\]]?\s*$")
_TRAIL_GENRE = re.compile(
    r"[,\-(\[]\s*" + _GENRES + r"\s*[)\]\-]?\s*$", re.IGNORECASE)
_PARENS = re.compile(r"[(\[]([^)\]]+)[)\]]")
_GENRE_ONLY = re.compile(r"^\s*" + _GENRES + r"\s*$", re.IGNORECASE)


def strip_descriptors(title: str) -> str:
    """Drop trailing genre labels and recording dates LPs tack onto track titles.

    'Tierra Querida - Tango' -> 'Tierra Querida'; 'Los Mareados -Tango-' ->
    'Los Mareados'; 'Por Un Cariño (Tango) 7/8/59' -> 'Por Un Cariño'.
    A genre is only stripped when it trails a , - ( [ delimiter, so titles like
    'El Tango Es Una Historia' or a track simply named 'Tango' are left intact.
    """
    s = (title or "").strip()
    s = _TRAIL_DATE.sub("", s).strip()
    while True:
        stripped = _TRAIL_GENRE.sub("", s).strip().strip("-,([ ").strip()
        if stripped == s or not stripped:
            break
        s = stripped
    return s or (title or "").strip()


def title_candidates(title: str) -> list[str]:
    """Ordered, deduped match candidates: cleaned title then parenthetical alts.

    'Mala Estampa (Mala Pinta)' -> ['Mala Estampa', 'Mala Pinta']: both the text
    outside the parentheses and the contents are tried, since the parenthetical
    is often the recording's real/alternate title on compilation LPs. A
    parenthetical that is itself only a genre label ('(Tango)') is dropped so it
    can't stray-match a recording literally titled 'Tango'.
    """
    raw = title or ""
    alts = [p for p in _PARENS.findall(raw) if not _GENRE_ONLY.match(p.strip())]
    if alts:
        bases = [strip_descriptors(_PARENS.sub("", raw))] + [strip_descriptors(p) for p in alts]
    else:
        bases = [strip_descriptors(raw)]
    out: list[str] = []
    seen: set[str] = set()
    for cand in bases:
        key = norm(cand)
        if key and key not in seen:
            seen.add(key)
            out.append(cand)
    return out


def _year_of(date_str: str) -> str:
    s = (date_str or "").strip()
    if "/" in s:
        return s.rsplit("/", 1)[-1][:4]
    return s[:4]


def prepare(recs: list[dict]) -> list[dict]:
    """Annotate each discography row with normalized fields used by the matcher."""
    out = []
    for r in recs:
        c = dict(r)
        c["_year"] = _year_of(c.get("Date", ""))
        c["_nt"] = norm(c.get("Title", ""))
        c["_na"] = norm(c.get("AltTitle", ""))
        c["_ntc"] = c["_nt"].replace(" ", "")
        c["_nac"] = c["_na"].replace(" ", "")
        out.append(c)
    return out


def _pick(cand: list[dict], status: str, note: str) -> tuple[dict, str, str]:
    if len(cand) > 1:
        singers = ", ".join(sorted({r.get("Singer", "") for r in cand}))
        extra = f"{len(cand)} recordings same title+year (singers: {singers})"
        note = f"{note}; {extra}" if note else extra
        status = "matched_multiple_takes"
    return cand[0], status, note


def _closest_year(cands: list[dict], year: str) -> list[dict]:
    """Narrow year-flex candidates to those whose year is nearest the LP's year.

    When a title was recorded several times, prefer the take closest to the LP's
    (release) year — e.g. a 1994 live-album 'Arrabal' matches the 1989 concert
    take, not the 1943 studio one. Returns all candidates tied for nearest.
    """
    try:
        ty = int(year)
    except (TypeError, ValueError):
        return cands

    def diff(r: dict) -> int:
        y = r.get("_year", "")
        return abs(int(y) - ty) if y.isdigit() else 10 ** 6

    best = min(diff(r) for r in cands)
    return [r for r in cands if diff(r) == best]


def match_track(title: str, year: str, recs: list[dict]) -> tuple[dict | None, str, str]:
    """Match a track title against the prepared discography.

    The raw LP title is first reduced to candidates (genre/date labels stripped,
    parenthetical alternates split out); each is tried in turn and the first to
    match wins. See _match_one for the per-candidate cascade.

    Returns (hit, status, note). When no candidate matches, hit is None,
    status is 'no_title_match', and note is ''.
    """
    for cand in title_candidates(title):
        hit, status, note = _match_one(cand, year, recs)
        if hit:
            return hit, status, note
    return None, "no_title_match", ""


def _match_one(title: str, year: str, recs: list[dict]) -> tuple[dict | None, str, str]:
    """Match a single cleaned title against the prepared discography.

    Tries exact -> compact (no-spaces) -> fuzzy (ratio >= 0.84) within the
    given year first. If year is empty, the same cascade runs across all
    recordings. If year is non-empty and nothing matches, falls back to the
    same cascade ignoring year ('year-flex'), producing 'matched_year_flex' /
    'matched_year_flex_variant'.
    """
    t = norm(title)
    tc = t.replace(" ", "")
    pool_year = [r for r in recs if r["_year"] == year] if year else recs

    ex = [r for r in pool_year if r["_nt"] == t or r["_na"] == t]
    if ex:
        return _pick(ex, "matched", "")
    cp = [r for r in pool_year if r["_ntc"] == tc or (r["_nac"] and r["_nac"] == tc)]
    if cp:
        return _pick(cp, "matched_variant", f"variant of {cp[0]['Title']!r}")
    best, best_r = None, 0.0
    for r in pool_year:
        for c in (r["_nt"], r["_na"]):
            if not c:
                continue
            ratio = SequenceMatcher(None, t, c).ratio()
            if ratio > best_r:
                best, best_r = r, ratio
    if best and best_r >= FUZZY_MIN:
        return best, "matched_variant", f"fuzzy {best_r:.2f} of {best['Title']!r}"

    if year:
        ex = [r for r in recs if r["_nt"] == t or r["_na"] == t]
        if ex:
            ex = _closest_year(ex, year)
            hit, _s, note = _pick(ex, "matched", "")
            ynote = f"LP year {year} != recording year {hit['_year']}"
            return hit, "matched_year_flex", f"{note}; {ynote}" if note else ynote
        cp = [r for r in recs if r["_ntc"] == tc or (r["_nac"] and r["_nac"] == tc)]
        if cp:
            cp = _closest_year(cp, year)
            hit, _s, note = _pick(cp, "matched_variant", f"variant of {cp[0]['Title']!r}")
            ynote = f"LP year {year} != recording year {hit['_year']}"
            return hit, "matched_year_flex_variant", f"{note}; {ynote}"
        best2, best2_r = None, 0.0
        for r in recs:
            for c in (r["_nt"], r["_na"]):
                if not c:
                    continue
                ratio = SequenceMatcher(None, t, c).ratio()
                if ratio > best2_r:
                    best2, best2_r = r, ratio
        if best2 and best2_r >= FUZZY_MIN:
            ynote = f"LP year {year} != recording year {best2['_year']}"
            return best2, "matched_year_flex_variant", f"fuzzy {best2_r:.2f} of {best2['Title']!r}; {ynote}"

    return None, "no_title_match", ""


def _row_from_hit(hit: dict | None, base: dict, status: str, note: str) -> dict:
    import build  # lazy to avoid circular-import surface
    r = dict(base)
    r["Match_Status"] = status
    r["Note"] = note
    if hit:
        r["Disc_Date"] = build.normalize_date(hit.get("Date", ""))
        r["Disc_Title"] = hit.get("Title", "")
        r["Disc_AltTitle"] = hit.get("AltTitle", "")
        r["Singer"] = hit.get("Singer", "")
        r["Master"] = hit.get("Master", "")
        r["Matrix"] = hit.get("Matrix", "")
    return r


def _resolve_folder(name: str, folder_names: dict[str, str]) -> str:
    """Case-insensitive lookup of canonical folder name on disk; returns CSV value if no match."""
    return folder_names.get((name or "").strip().lower(), name)


def match_lps_csv(path: Path, recs_prepared: list[dict], folder_names: dict[str, str]) -> list[dict]:
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        hit, status, note = match_track(r.get("Track_Title", ""), (r.get("Track_Year") or "").strip(), recs_prepared)
        base = {
            "LP_Folder": _resolve_folder(r.get("LP_Folder", ""), folder_names),
            "LP_Catalog": r.get("LP_Catalog", ""),
            "LP_Title": r.get("LP_Title", ""),
            "Side": r.get("Side", ""),
            "Track_No": r.get("Track_No", ""),
            "Track_Title": r.get("Track_Title", ""),
            "Track_Year": r.get("Track_Year", ""),
            "Disc_Date": "", "Disc_Title": "", "Disc_AltTitle": "",
            "Singer": "", "Master": "", "Matrix": "",
            "Kind": "LP",
        }
        out.append(_row_from_hit(hit, base, status, note))
    return out


def match_eps_csv(path: Path, recs_prepared: list[dict], folder_names: dict[str, str]) -> list[dict]:
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        title = r.get("Title", "")
        year = (r.get("Year") or "").strip()
        hit, status, note = match_track(title, year, recs_prepared)
        base = {
            "LP_Folder": _resolve_folder(r.get("EP", ""), folder_names),
            "LP_Catalog": r.get("ID", ""),
            "LP_Title": "",
            "Side": "", "Track_No": "",
            "Track_Title": title,
            "Track_Year": year,
            "Disc_Date": "", "Disc_Title": "", "Disc_AltTitle": "",
            "Singer": "", "Master": "", "Matrix": "",
            "Kind": "EP",
        }
        out.append(_row_from_hit(hit, base, status, note))
    return out


def write_matches_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in OUTPUT_COLS})
