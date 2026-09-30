"""import_singles: quarantine awareness, dry-run purity, --harvest-only and
--replace-reissues.

The landmine these guard: import_singles matched source scans to rows and
wrote the winner into a SERVED folder with no knowledge of Singles/_suspect/,
so every --apply re-created targets a vision pass had already judged bad --
often under a different name than the one they were quarantined as (a
1930-10-15 Sentimiento Gaucho write was byte-identical to
_suspect/1930-12-12_Sentimiento-Gaucho_Canaro~2.webp).
"""
from __future__ import annotations

import csv
import hashlib
import random
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

import import_singles as im

REPO = Path(__file__).resolve().parent


def _label(path: Path, seed: int, size=(900, 900)) -> Path:
    """A synthetic 'label': random blocks, distinct per seed."""
    rng = random.Random(seed)
    img = Image.new("RGB", size, (rng.randrange(256), 40, 40))
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        d.rectangle([x, y, x + rng.randrange(40, 300), y + rng.randrange(40, 300)],
                    fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=95)
    return path


@pytest.fixture(autouse=True)
def _no_crop(monkeypatch):
    # Synthetic images are not discs; keep the render deterministic.
    monkeypatch.setattr(im, "CROP_LABELS", False)


# --- SuspectIndex -----------------------------------------------------------

def test_rematerialized_suspect_under_another_name_is_caught(tmp_path):
    """The known case: the write for 1930-10-15 was byte-identical to a file
    quarantined as 1930-12-12_...~2 -- a basename check cannot see it."""
    src = _label(tmp_path / "src" / "1930-10-15_sentimiento-gaucho_canaro.jpg", 1)
    singles = tmp_path / "Singles"
    r = im.render_webp(src, 85)
    bad = singles / "_suspect" / "1930-12-12_Sentimiento-Gaucho_Canaro~2.webp"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(r.data)

    hit = im.SuspectIndex(singles).match(im.render_webp(src, 85))
    assert hit == ("exact", bad)


def test_reencoded_and_resized_suspect_is_caught_perceptually(tmp_path):
    src = _label(tmp_path / "src" / "a.jpg", 2)
    singles = tmp_path / "Singles"
    bad = singles / "_suspect" / "old_name.webp"
    bad.parent.mkdir(parents=True)
    with Image.open(src) as img:
        img.resize((700, 700)).save(bad, "WEBP", quality=60)
    kind, path = im.SuspectIndex(singles).match(im.render_webp(src, 85))
    assert kind.startswith("ahash") and path == bad


def test_distinct_label_and_incorrect_folder(tmp_path):
    singles = tmp_path / "Singles"
    other = _label(tmp_path / "src" / "other.jpg", 3)
    set_aside = singles / "Incorrect" / "x.webp"
    set_aside.parent.mkdir(parents=True)
    set_aside.write_bytes(im.render_webp(_label(tmp_path / "src" / "bad.jpg", 4), 85).data)
    idx = im.SuspectIndex(singles)
    assert idx.match(im.render_webp(other, 85)) is None
    assert idx.match(im.render_webp(tmp_path / "src" / "bad.jpg", 85))[1] == set_aside


@pytest.mark.skipif(
    not (REPO / "images/Canaro/Singles/_suspect/1930-12-12_Sentimiento-Gaucho_Canaro~2.webp").exists()
    or not (im.PARSE / "Tangos78/matched_discography/francisco-canaro/"
            "1930-10-15_sentimiento-gaucho_canaro.webp").exists(),
    reason="live Canaro data not present")
def test_live_sentimiento_gaucho_case(monkeypatch):
    monkeypatch.setattr(im, "CROP_LABELS", True)
    src = (im.PARSE / "Tangos78/matched_discography/francisco-canaro/"
           "1930-10-15_sentimiento-gaucho_canaro.webp")
    bad = REPO / "images/Canaro/Singles/_suspect/1930-12-12_Sentimiento-Gaucho_Canaro~2.webp"
    r = im.render_webp(src, 85)
    assert r.sha == hashlib.sha256(bad.read_bytes()).hexdigest()
    hit = im.SuspectIndex(REPO / "images/Canaro/Singles").match(r)
    assert hit and hit[0] == "exact"


# --- run(): end to end on a throwaway repo ------------------------------------

DISCO_FIELDS = ["Date", "Title", "AltTitle", "Disc"]


def _tree(tmp_path, monkeypatch, rows, harvest=True):
    repo, parse = tmp_path / "repo", tmp_path / "parse"
    (repo / "csv_files").mkdir(parents=True)
    with (repo / "csv_files" / "Test Artist.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DISCO_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    cfg = {"display": "Test Artist", "csv": "Test Artist.csv", "tangoinfo": "TI"}
    if harvest:
        cfg["harvest"] = "TA"
    monkeypatch.setattr(im, "REPO", repo)
    monkeypatch.setattr(im, "PARSE", parse)
    monkeypatch.setattr(im, "HARVEST_DATA", parse / "harvest_data")
    monkeypatch.setattr(im, "ARTISTS", {"TA": cfg})
    ti = parse / "TangoInfo data" / "ScrapeShellac" / "out" / "TI" / "images"
    hv = parse / "Marketplace Harvest" / "matched" / "TA"
    singles = repo / "images" / "TA" / "Singles"
    return SimpleNamespace(repo=repo, parse=parse, ti=ti, hv=hv, singles=singles)


def _args(**kw):
    base = dict(apply=False, clean=False, upload=False, quality=85,
                show_unmatched=False, harvest_only=False, replace_reissues=False,
                min_import_px=im.MIN_IMPORT_PX, min_replace_px=im.MIN_REPLACE_PX)
    base.update(kw)
    return SimpleNamespace(**base)


def test_dry_run_writes_nothing_not_even_the_candidates_csv(tmp_path, monkeypatch):
    t = _tree(tmp_path, monkeypatch, [{"Date": "1930-10-15", "Title": "Sentimiento Gaucho"}])
    _label(t.ti / "1930-10-15_Sentimiento-Gaucho_Artist.jpg", 5)
    _label(t.hv / "1930-10-15_Sentimiento-Gaucho_Artist.webp", 6)   # 2 candidates
    assert im.run("TA", _args()) == 0
    assert not (t.repo / "images" / "TA" / "_import_candidates.csv").exists()
    assert not t.singles.exists()
    assert im.run("TA", _args(apply=True)) == 0
    assert (t.repo / "images" / "TA" / "_import_candidates.csv").exists()
    assert (t.singles / "1930-1934" / "1930-10-15_Sentimiento-Gaucho_Artist.webp").exists()


def test_apply_skips_a_quarantined_candidate_and_falls_back(tmp_path, monkeypatch, capsys):
    t = _tree(tmp_path, monkeypatch, [{"Date": "1930-10-15", "Title": "Sentimiento Gaucho"}])
    top = _label(t.hv / "1930-10-15_Sentimiento-Gaucho_Artist.webp", 7)   # harvest ranks first
    alt = _label(t.ti / "1930-10-15_Sentimiento-Gaucho_Artist.jpg", 8)
    bad = t.singles / "_suspect" / "1930-12-12_Sentimiento-Gaucho_Artist~2.webp"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(im.render_webp(top, 85).data)

    assert im.run("TA", _args(apply=True)) == 0
    out = capsys.readouterr().out
    assert "suspect-skip" in out and "1930-12-12_Sentimiento-Gaucho_Artist~2.webp" in out
    dest = t.singles / "1930-1934" / "1930-10-15_Sentimiento-Gaucho_Artist.webp"
    assert dest.read_bytes() == im.render_webp(alt, 85).data


def test_every_candidate_quarantined_means_no_write(tmp_path, monkeypatch):
    t = _tree(tmp_path, monkeypatch, [{"Date": "1930-10-15", "Title": "Sentimiento Gaucho"}])
    only = _label(t.ti / "1930-10-15_Sentimiento-Gaucho_Artist.jpg", 9)
    bad = t.singles / "_suspect" / "whatever.webp"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(im.render_webp(only, 85).data)
    im.run("TA", _args(apply=True))
    assert not (t.singles / "1930-1934").exists()


def test_candidate_already_served_for_another_recording_is_skipped(tmp_path, monkeypatch):
    t = _tree(tmp_path, monkeypatch, [{"Date": "1925", "Title": "Oro y Seda"},
                                      {"Date": "1925-07-08", "Title": "Oro y Seda"}])
    src = _label(t.ti / "1925_Oro-Y-Seda_Artist.jpg", 10)
    served = t.singles / "1925-1929" / "1925-07-08_Oro-Y-Seda_Artist.webp"
    served.parent.mkdir(parents=True)
    served.write_bytes(im.render_webp(src, 85).data)
    im.run("TA", _args(apply=True))
    assert not (t.singles / "1925-1929" / "1925_Oro-Y-Seda_Artist.webp").exists()


def test_harvest_only_restricts_sources_and_refuses_clean(tmp_path, monkeypatch):
    t = _tree(tmp_path, monkeypatch, [{"Date": "1930-10-15", "Title": "Sentimiento Gaucho"},
                                      {"Date": "1931", "Title": "Otra"}])
    _label(t.hv / "1930-10-15_Sentimiento-Gaucho_Artist.webp", 11)
    _label(t.ti / "1931_Otra_Artist.jpg", 12)
    assert im.run("TA", _args(apply=True, harvest_only=True)) == 0
    assert (t.singles / "1930-1934" / "1930-10-15_Sentimiento-Gaucho_Artist.webp").exists()
    assert not (t.singles / "1930-1934" / "1931_Otra_Artist.webp").exists()
    assert im.run("TA", _args(harvest_only=True, clean=True)) == 2


def test_harvest_only_needs_a_harvest_source(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch, [{"Date": "1931", "Title": "Otra"}], harvest=False)
    assert im.run("TA", _args(harvest_only=True)) == 2


# --- --replace-reissues ----------------------------------------------------------

def _report(singles: Path, rows: list[dict]):
    import _verify_singles as vs
    vs.write_report(singles / "_verification_report.csv", rows)


def _review_queue(parse: Path, rows: list[dict]):
    d = parse / "harvest_data" / "match" / "TA"
    d.mkdir(parents=True, exist_ok=True)
    fields = ["crop_path", "disco_date", "disco_title", "disco_disc",
              "label_title", "label_catalog", "reason"]
    with (d / "review_queue.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _served_reissue(t, seed=20):
    served = t.singles / "1940-1944" / "1942-10-22_El-Chupete_Artist.webp"
    _label(tmp := t.parse / "old.jpg", seed)
    served.parent.mkdir(parents=True, exist_ok=True)
    served.write_bytes(im.render_webp(tmp, 85).data)
    return served


def test_original_issue_replaces_a_served_reissue(tmp_path, monkeypatch, capsys):
    t = _tree(tmp_path, monkeypatch,
              [{"Date": "1942-10-22", "Title": "El Chupete", "Disc": "39771 B"}])
    served = _served_reissue(t)
    old = served.read_bytes()
    _report(t.singles, [{"filename": served.name, "era": "1940-1944",
                         "status": "reissue", "disc_on_label": "68-1432-B"}])
    crop = _label(t.parse / "crops" / "el-chupete-39771-b.webp", 21)
    _review_queue(t.parse, [{"crop_path": str(crop), "disco_date": "1942-10-22",
                             "disco_title": "El Chupete", "disco_disc": "39771 B",
                             "label_title": "EL CHUPETE", "label_catalog": "39771-B",
                             "reason": "already_have_image"}])

    im.run("TA", _args(replace_reissues=True))                  # dry-run
    assert "would replace reissue" in capsys.readouterr().out
    assert served.read_bytes() == old

    im.run("TA", _args(apply=True, replace_reissues=True))
    out = capsys.readouterr().out
    assert "replaced:" in out
    assert served.read_bytes() == im.render_webp(crop, 85).data
    backup = t.singles / "_replaced" / "1940-1944" / served.name
    assert backup.read_bytes() == old
    row = next(csv.DictReader((t.singles / "_verification_report.csv").open(encoding="utf-8-sig")))
    assert row["status"] == "unchecked"


def test_without_the_flag_nothing_is_overwritten(tmp_path, monkeypatch):
    t = _tree(tmp_path, monkeypatch,
              [{"Date": "1942-10-22", "Title": "El Chupete", "Disc": "39771 B"}])
    served = _served_reissue(t)
    old = served.read_bytes()
    _report(t.singles, [{"filename": served.name, "era": "1940-1944", "status": "reissue"}])
    crop = _label(t.parse / "crops" / "c.webp", 22)
    _review_queue(t.parse, [{"crop_path": str(crop), "disco_date": "1942-10-22",
                             "disco_title": "El Chupete", "disco_disc": "39771 B",
                             "label_title": "EL CHUPETE", "label_catalog": "39771-B",
                             "reason": "already_have_image"}])
    im.run("TA", _args(apply=True))
    assert served.read_bytes() == old


@pytest.mark.parametrize("status,label_cat", [
    ("ok", "39771-B"),          # served image is not a recorded reissue
    ("reissue", "68-1432-B"),   # candidate is ANOTHER reissue, not the original
])
def test_replace_needs_a_reissue_served_and_an_original_offered(
        tmp_path, monkeypatch, status, label_cat):
    t = _tree(tmp_path, monkeypatch,
              [{"Date": "1942-10-22", "Title": "El Chupete", "Disc": "39771 B"}])
    served = _served_reissue(t)
    old = served.read_bytes()
    _report(t.singles, [{"filename": served.name, "era": "1940-1944", "status": status,
                         "disc_verdict": "match" if status == "ok" else "mismatch"}])
    crop = _label(t.parse / "crops" / "c.webp", 23)
    _review_queue(t.parse, [{"crop_path": str(crop), "disco_date": "1942-10-22",
                             "disco_title": "El Chupete", "disco_disc": "39771 B",
                             "label_title": "EL CHUPETE", "label_catalog": label_cat,
                             "reason": "already_have_image"}])
    im.run("TA", _args(apply=True, replace_reissues=True))
    assert served.read_bytes() == old
    assert not (t.singles / "_replaced").exists()


# --- Fix 5 (2026-09-29e): one label design, different titles ----------------------

def _design(title_seed: int, size=900) -> Image.Image:
    """A synthetic label DESIGN (ring, logo, bottom band) whose only per-title
    difference is the 'text' in the title band -- like the Columbia 'Pacho'
    labels of Flor De Zanahoria (TX763) and El Alero (TX764)."""
    img = Image.new("RGB", (900, 900), (230, 225, 210))
    d = ImageDraw.Draw(img)
    d.ellipse([60, 60, 840, 840], outline=(20, 20, 90), width=30)
    d.rectangle([300, 120, 600, 380], fill=(30, 30, 110))
    d.rectangle([100, 700, 800, 780], fill=(30, 30, 110))
    rng = random.Random(title_seed)
    for i in range(8):
        y, x = 510 + i * 22, 200
        while x < 700:
            w = rng.randrange(6, 30)
            d.rectangle([x, y, x + w, y + 12], fill=(40, 40, 120))
            x += w + rng.randrange(6, 16)
    return img.resize((size, size)) if size != 900 else img


def test_same_design_different_title_is_not_a_duplicate(tmp_path):
    singles = tmp_path / "Singles"
    served = singles / "1910-1914" / "1912_El-Alero_Artist.webp"
    served.parent.mkdir(parents=True)
    _design(1).save(served, "WEBP", quality=85)
    src = tmp_path / "src" / "1913_Flor-De-Zanahoria_Artist.jpg"
    src.parent.mkdir()
    _design(2).save(src, quality=95)
    r = im.render_webp(src, 85)
    # the whole-image hash alone cannot tell them apart...
    assert im.hamming(im._file_ahash(served), r.ahash_out) <= 6
    assert im.band_ncc(im._file_sig(served)[1], r.band_out) < im.BAND_MIN_NCC
    # ...the title band can
    assert im.SuspectIndex.served(singles, max_distance=6).match(r) is None


def test_same_image_reencoded_and_resized_is_still_a_duplicate(tmp_path):
    singles = tmp_path / "Singles"
    served = singles / "1910-1914" / "1912_El-Alero_Artist.webp"
    served.parent.mkdir(parents=True)
    _design(1, size=700).save(served, "WEBP", quality=60)
    src = tmp_path / "src" / "a.jpg"
    src.parent.mkdir()
    _design(1).save(src, quality=95)
    kind, path = im.SuspectIndex.served(singles).match(im.render_webp(src, 85))
    assert kind.startswith("ahash") and path == served


MAGLIO = REPO / "images/Maglio/Singles/1910-1914/1912_El-Alero_Maglio.webp"
FLOR = im.PARSE / "Marketplace Harvest/matched/Maglio/1913_Flor-De-Zanahoria_Maglio.webp"


@pytest.mark.skipif(not (MAGLIO.exists() and FLOR.exists()), reason="live Maglio data not present")
def test_live_flor_de_zanahoria_is_not_el_alero(monkeypatch):
    monkeypatch.setattr(im, "CROP_LABELS", True)
    r = im.render_webp(FLOR, 85)
    h, band = im._file_sig(MAGLIO)
    assert im.hamming(h, r.ahash_out) <= im.SUSPECT_MAX_DISTANCE    # the old false match
    assert im.same_image(h, band, r) is None


# --- Fix 4: minimum resolution --------------------------------------------------------

def test_tiny_candidate_is_not_imported(tmp_path, monkeypatch, capsys):
    t = _tree(tmp_path, monkeypatch, [{"Date": "1930-10-15", "Title": "Sentimiento Gaucho"}])
    _label(t.ti / "1930-10-15_Sentimiento-Gaucho_Artist.jpg", 30, size=(200, 180))
    im.run("TA", _args(apply=True))
    assert "too small to import" in capsys.readouterr().out
    assert not (t.singles / "1930-1934").exists()
    # the override lets it through
    im.run("TA", _args(apply=True, min_import_px=100))
    assert (t.singles / "1930-1934" / "1930-10-15_Sentimiento-Gaucho_Artist.webp").exists()


def test_small_original_does_not_replace_a_reissue(tmp_path, monkeypatch, capsys):
    """'Aunque No Lo Crean': a 240 px original displaced a 2400 px reissue."""
    t = _tree(tmp_path, monkeypatch,
              [{"Date": "1942-10-22", "Title": "El Chupete", "Disc": "39771 B"}])
    served = _served_reissue(t)
    old = served.read_bytes()
    _report(t.singles, [{"filename": served.name, "era": "1940-1944", "status": "reissue"}])
    crop = _label(t.parse / "crops" / "small.webp", 31, size=(240, 241))
    _review_queue(t.parse, [{"crop_path": str(crop), "disco_date": "1942-10-22",
                             "disco_title": "El Chupete", "disco_disc": "39771 B",
                             "label_title": "EL CHUPETE", "label_catalog": "39771-B",
                             "reason": "already_have_image"}])
    im.run("TA", _args(apply=True, replace_reissues=True))
    assert "too small to replace" in capsys.readouterr().out
    assert served.read_bytes() == old
    assert not (t.singles / "_replaced").exists()
    # at or above the minimum it replaces
    im.run("TA", _args(apply=True, replace_reissues=True, min_replace_px=200))
    assert served.read_bytes() == im.render_webp(crop, 85).data


# --- Fix 1: --replace-reissues reads the report by canonical key ----------------------

def test_old_era_reissue_row_is_not_hidden_by_an_unchecked_duplicate(tmp_path, monkeypatch):
    """The Troilo case: the real 'reissue' verdict sat on the OLD era row, and
    an 'unchecked' year-bucket duplicate (last row wins) hid it."""
    t = _tree(tmp_path, monkeypatch,
              [{"Date": "1942-10-22", "Title": "El Chupete", "Disc": "39771 B"}])
    served = _served_reissue(t)
    _report(t.singles, [
        {"filename": served.name, "era": "42-43 Goni Post-Malena", "status": "reissue",
         "disc_on_label": "68-1432-B"},
        {"filename": served.name, "era": "1940-1944", "status": "unchecked",
         "notes": "no verdict supplied"},
    ])
    # this test CSV has no Grouping column (the old era would count as a
    # staging folder); the real Troilo CSV lists it as a Grouping
    monkeypatch.setattr(im, "report_keep_eras", lambda root, disco: frozenset())
    crop = _label(t.parse / "crops" / "c.webp", 32)
    _review_queue(t.parse, [{"crop_path": str(crop), "disco_date": "1942-10-22",
                             "disco_title": "El Chupete", "disco_disc": "39771 B",
                             "label_title": "EL CHUPETE", "label_catalog": "39771-B",
                             "reason": "already_have_image"}])
    im.run("TA", _args(apply=True, replace_reissues=True))
    assert served.read_bytes() == im.render_webp(crop, 85).data
    rows = list(csv.DictReader((t.singles / "_verification_report.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 1                                   # duplicates collapsed
    assert rows[0]["era"] == "1940-1944" and rows[0]["status"] == "unchecked"
    assert "replaced by original issue" in rows[0]["notes"]


def test_report_keep_eras_uses_the_csv_groupings(tmp_path):
    singles = tmp_path / "Singles"
    for d in ("1940-1944", "tango_info", "42-43 Goni Post-Malena", "_suspect"):
        (singles / d).mkdir(parents=True)
    disco = tmp_path / "x.csv"
    with disco.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Date", "Title", "Grouping"])
        w.writeheader()
        w.writerow({"Date": "1942", "Title": "A", "Grouping": "42-43 Goni Post-Malena"})
    assert im.report_keep_eras(singles, disco) == {"tango_info"}
