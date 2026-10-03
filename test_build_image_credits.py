"""build_image_credits: per-source refs, the credits file format, same-photo
attribution of singles (never by filename alone), fetch-log attribution of
album folders, and parity with the source table in index.html."""
from __future__ import annotations

import csv
import os
import random
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import build_image_credits as bic

REPO = Path(__file__).resolve().parent

ARAQUE_POST = "https://fresedotracks.blogspot.com/2023/03/00182-nueva-york-osvaldo-fresedo-1920.html"
ARAQUE_REF = "fresedotracks/2023/03/00182-nueva-york-osvaldo-fresedo-1920"


# ---------------------------------------------------------------- refs
@pytest.mark.parametrize("code,url,ref", [
    ("a", ARAQUE_POST, ARAQUE_REF),
    ("a", "https://evil.blogspot.com/2023/03/x.html", ""),          # not one of his blogs
    ("d", "https://adp.library.ucsb.edu/index.php/matrix/refer/600004307", "600004307"),
    ("d", "http://adp.library.ucsb.edu/index.php/matrix/detail/2000441980/BAVE-012762-Arrabalero",
     "2000441980"),
    ("ti", "https://tango.info/02480002701026", "02480002701026"),
    ("t78", "https://www.tangos78rpm.com/la-morocha-parlophone-no-po-96/", "la-morocha-parlophone-no-po-96"),
    ("ia", "https://archive.org/details/78_soy-porteo_gbia0400687b", "78_soy-porteo_gbia0400687b"),
    ("eb", "https://www.ebay.com/itm/127736331666", "127736331666"),
    ("ps", "https://www.popsike.com/Odeon-78-rpm-Canaro/111361982944.html", "Odeon-78-rpm-Canaro/111361982944"),
    ("bc", "https://someband.bandcamp.com/album/el-sonido-vol-1", "someband/album/el-sonido-vol-1"),
    ("bc", "https://someband.example.com/album/x", ""),
    ("dg", "15113733", "15113733"),
    ("it", "ar/1583226403", "ar/1583226403"),
    ("ti", "", ""),
    ("ap", "https://astorpiazzolla.com/discography/", ""),          # no per-image page
    ("zz", "https://example.com/", ""),
])
def test_ref_for(code, url, ref):
    assert bic.ref_for(code, url) == ref


def test_harvest_credit():
    assert bic.harvest_credit("araque", ARAQUE_POST) == ("a", ARAQUE_REF)
    # the local copy of Araque's scans is credited to him, with no post to link
    assert bic.harvest_credit("josemanuel", "file:///C:/x/1924-12-04 pobre margot") == ("a", "")
    # archive rows carry the item identifier as the listing id
    assert bic.harvest_credit("archive", "", "78_adios_gbia3014820a__label_01") == ("ia", "78_adios_gbia3014820a")
    assert bic.harvest_credit("dahr", "https://adp.library.ucsb.edu/index.php/matrix/refer/5") == ("d", "5")
    assert bic.harvest_credit("somewhere-new", "https://x/") is None


# ---------------------------------------------------------------- file format
ENTRIES = {
    "FresedoOsvaldo/Singles/1920-1924/1920-09-03_Nueva-York_Fresedo.webp": ("a", ARAQUE_REF),
    "FresedoOsvaldo/Singles/1920-1924/1922-04-26_Siete-Pelos_Fresedo.webp": ("d", "600004307"),
    "CaroJulioDe/Singles/1930-1934/1930_El-Pillete_De-Caro.webp": ("a", ""),
    "SarliCarlosDi/LPs/Los Tangos Más Buscados": ("dg", "123"),
    "SarliCarlosDi/EPs/Bahia Blanca": ("dz", "457000855"),
}


def test_render_parse_roundtrip_and_shape():
    text = bic.render_credits(ENTRIES)
    assert bic.parse_credits(text) == ENTRIES
    assert text == bic.render_credits(dict(reversed(list(ENTRIES.items()))))   # order-independent
    assert text.endswith("\n") and "\r" not in text
    lines = text.split("\n")
    # singles share their folder prefix and "_Suffix.webp" through the group line
    assert ">FresedoOsvaldo/Singles/1920-1924/|_Fresedo.webp" in lines
    assert "\t1920-09-03_Nueva-York\ta\t" + ARAQUE_REF in lines
    assert "\t1930_El-Pillete\ta" in lines                  # no ref -> no third column
    assert ">SarliCarlosDi/LPs/|" in lines and "\tLos Tangos Más Buscados\tdg\t123" in lines
    assert "http" not in text.split("\n", 2)[2]             # refs only, never raw URLs


def test_render_rejects_bad_entries():
    with pytest.raises(ValueError):
        bic.render_credits({"A/Singles/1920-1924/x_A.webp": ("nope", "")})
    with pytest.raises(ValueError):
        bic.render_credits({"A/LPs/Bad\tName": ("dg", "1")})


def test_parse_ignores_noise():
    text = "# c\n\torphan\ta\n>P/|_S.webp\n\tn\ta\tr\nstray line\n\t\ta\n"
    assert bic.parse_credits(text) == {"P/n_S.webp": ("a", "r")}


# ---------------------------------------------------------------- decide
def test_decide():
    assert bic.decide([]) == "none"
    assert bic.decide([(0, 1, "ti", "111")]) == ("ti", "111")
    # the same photograph under two sources: which one it came from is unknown
    assert bic.decide([(0, 0, "ti", "111"), (2, 0, "t78", "slug")]) == "ambiguous"
    # one source, two records: the closest best-tier match supplies the link ...
    assert bic.decide([(0, 2, "d", "5"), (0, 0, "d", "7"), (2, 0, "d", "9")]) == ("d", "7")
    # ... and equally close records that disagree leave the credit unlinked
    assert bic.decide([(0, 0, "d", "5"), (0, 0, "d", "7")]) == ("d", "")
    assert bic.decide([(2, 1, "a", ""), (2, 1, "a", ARAQUE_REF)]) == ("a", ARAQUE_REF)


# ---------------------------------------------------------------- singles
def _label(path: Path, seed: int, size=(700, 700)) -> Path:
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


def _serve(src: Path, dest: Path) -> Path:
    """Re-encode ``src`` smaller, as the importer would (same photograph)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        img.convert("RGB").resize((500, 500), Image.LANCZOS).save(dest, "WEBP", quality=85)
    return dest


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


CFG = {"display": "Osvaldo Fresedo", "csv": "Osvaldo Fresedo.csv",
       "tangoinfo": "Osvaldo_Fresedo", "discogs": "osvaldo-fresedo", "harvest": "Fresedo"}


@pytest.fixture
def world(tmp_path):
    """A miniature site repo + parse repo with one artist and five recordings."""
    repo, parse = tmp_path / "site", tmp_path / "parse"
    titles = ["Nueva York", "Siete Pelos", "Sangre Azul", "Mario", "Lina", "Firulete"]
    _write_csv(repo / "csv_files" / "Osvaldo Fresedo.csv",
               ["Bandleader", "Date", "Title", "AltTitle", "Disc"],
               [["Osvaldo Fresedo", f"1922-0{i + 1}-10", t, "", ""] for i, t in enumerate(titles)])
    singles = repo / "images" / "Fresedo" / "Singles" / "1920-1924"

    ti = parse / "TangoInfo data" / "ScrapeShellac" / "out" / "Osvaldo_Fresedo"
    t78 = parse / "Tangos78" / "matched_discography" / "osvaldo-fresedo"
    crops = parse / "harvest_data" / "extraction" / "Fresedo" / "crops"

    # 1. Nueva York: served from the tango.info scan (name-linked)
    a = _label(ti / "images" / "1922-01-10_Nueva-York_Fresedo.jpg", 1)
    _serve(a, singles / "1922-01-10_Nueva-York_Fresedo.webp")
    # 2. Siete Pelos: served from an Araque harvest crop that no match row links
    b = _label(crops / "araque__fresedotracks-1__photo_01.webp", 2)
    _serve(b, singles / "1922-02-10_Siete-Pelos_Fresedo.webp")
    # 3. Sangre Azul: a tango.info file maps to it by NAME but is another photo
    _label(ti / "images" / "1922-03-10_Sangre-Azul_Fresedo.jpg", 3)
    _serve(_label(tmp_path / "elsewhere.jpg", 33), singles / "1922-03-10_Sangre-Azul_Fresedo.webp")
    # 4. Mario: the same photograph sits in tango.info AND tangos78
    c = _label(ti / "images" / "1922-04-10_Mario_Fresedo.jpg", 4)
    t78.mkdir(parents=True)
    with Image.open(c) as img:
        img.save(t78 / "1922-04-10_mario_fresedo.webp", "WEBP", quality=90)
    _serve(c, singles / "1922-04-10_Mario_Fresedo.webp")
    # 5. Lina: nothing anywhere
    _serve(_label(tmp_path / "unknown.jpg", 5), singles / "1922-05-10_Lina_Fresedo.webp")
    # 6. Firulete: served from the tangos78 file (record URL via match_report)
    e = _label(tmp_path / "t78src.jpg", 6)
    with Image.open(e) as img:
        img.save(t78 / "1922-06-10_firulete_fresedo.webp", "WEBP", quality=90)
    _serve(e, singles / "1922-06-10_Firulete_Fresedo.webp")
    # a quarantined image is not served and must not be reported
    _serve(a, singles.parent / "_suspect" / "1922-01-10_Nueva-York_Fresedo.webp")

    _write_csv(ti / "shellac_tracks.csv", ["image_filename", "product_url"], [
        ["1922-01-10_Nueva-York_Fresedo.jpg", "https://tango.info/02480002701026"],
        ["1922-03-10_Sangre-Azul_Fresedo.jpg", "https://tango.info/02480002701027"],
        ["1922-04-10_Mario_Fresedo.jpg", "https://tango.info/02480002701028"]])
    _write_csv(parse / "Tangos78" / "match_report.csv",
               ["status", "bandleader", "date", "title", "matched_record_slug"], [
                   ["matched", "Osvaldo Fresedo", "1922-06-10", "Firulete", "firulete-victor-no-73300-a"],
                   ["matched", "Osvaldo Fresedo", "1922-04-10", "Mario", "mario-victor-no-1-a"],
                   ["unmatched", "Osvaldo Fresedo", "1922-05-10", "Lina", ""]])
    _write_csv(crops.parent / "extracted.csv", ["crop_path", "listing_id", "source", "listing_url"],
               [[str(b), "fresedotracks-1", "araque", ARAQUE_POST]])
    return repo, parse


def _attribute(repo, parse):
    cache = bic.SigCache(None)
    return bic.attribute_singles("Fresedo", CFG, repo, parse, bic.HarvestIndex(parse), cache)


def test_singles_are_credited_by_photograph_not_by_name(world):
    repo, parse = world
    credits, reasons = _attribute(repo, parse)
    b = "1920-1924"
    assert credits == {
        (b, "1922-01-10_Nueva-York_Fresedo"): ("ti", "02480002701026"),
        (b, "1922-02-10_Siete-Pelos_Fresedo"): ("a", ARAQUE_REF),
        (b, "1922-06-10_Firulete_Fresedo"): ("t78", "firulete-victor-no-73300-a"),
    }
    # Sangre Azul has a same-named source file showing a different label;
    # Mario's photograph is held under two sources; Lina is found nowhere.
    assert dict(reasons) == {"name-only": 1, "ambiguous": 1, "none": 1}


def test_emitted_harvest_copy_links_the_crop_by_name(world):
    """emit.py copies the verified crop byte-for-byte into Marketplace
    Harvest/matched/<Key>/ under the recording's name; that copy is what ties
    a crop to its recording when matches.csv has since been overwritten."""
    repo, parse = world
    crop = parse / "harvest_data" / "extraction" / "Fresedo" / "crops" / "araque__fresedotracks-1__photo_01.webp"
    emitted = parse / "Marketplace Harvest" / "matched" / "Fresedo" / "1922-02-10_Siete-Pelos_Fresedo.webp"
    emitted.parent.mkdir(parents=True)
    shutil.copyfile(crop, emitted)
    cands = bic.artist_candidates("Fresedo", CFG, repo, parse, bic.HarvestIndex(parse), bic.SigCache(None))
    by_path = {c.path: c for c in cands}
    assert by_path[crop].targets == {("1920-1924", "1922-02-10_Siete-Pelos_Fresedo")}
    assert (by_path[crop].code, by_path[crop].ref) == ("a", ARAQUE_REF)
    assert emitted not in by_path            # the copy is the crop, not a second source


def test_build_writes_keys_and_is_idempotent(world, monkeypatch, tmp_path):
    repo, parse = world
    (repo / "singles_manifest.txt").write_text(
        "FresedoOsvaldo/Singles/1920-1924/1922-01-10_Nueva-York_Fresedo.webp\n"
        "FresedoOsvaldo/Singles/1920-1924/1921_Gone-Locally_Fresedo.webp\n", encoding="utf-8")
    import import_singles as im
    monkeypatch.setattr(im, "ARTISTS", {"Fresedo": CFG})
    out: list[str] = []
    cache_path = tmp_path / "cache.pkl"
    entries = bic.build(repo, parse, jobs=1, cache_path=cache_path, out=out.append)
    assert entries == {
        "FresedoOsvaldo/Singles/1920-1924/1922-01-10_Nueva-York_Fresedo.webp": ("ti", "02480002701026"),
        "FresedoOsvaldo/Singles/1920-1924/1922-02-10_Siete-Pelos_Fresedo.webp": ("a", ARAQUE_REF),
        "FresedoOsvaldo/Singles/1920-1924/1922-06-10_Firulete_Fresedo.webp": ("t78", "firulete-victor-no-73300-a"),
    }
    report = "\n".join(out)
    # 6 local + 1 manifest-only key; 3 credited; every gap is explained
    assert re.search(r"Fresedo\s+7\s+3\s+4\s", report)
    for why in ("1 name-only", "1 ambiguous", "1 none", "1 no local file"):
        assert why in report
    # second run: served from the signature cache, identical result
    assert cache_path.is_file()
    monkeypatch.setattr(bic, "_sig_task", lambda task: pytest.fail(f"re-read {task[0]}"))
    assert bic.build(repo, parse, jobs=1, cache_path=cache_path, out=lambda s: None) == entries
    assert bic.render_credits(entries) == bic.render_credits(dict(entries))


def test_sig_cache_recomputes_a_changed_file(tmp_path):
    p = _label(tmp_path / "a.jpg", 1)
    cache = bic.SigCache(tmp_path / "c.pkl")
    cache.ensure([p])
    first = cache.get(p)
    cache.save()
    _label(p, 2, size=(640, 640))
    os.utime(p, ns=(1, 1))
    cache = bic.SigCache(tmp_path / "c.pkl")
    cache.ensure([p])
    assert cache.get(p) is not None and cache.get(p) != first
    assert bic.same_photo(first, first, 0, 0.99) == 0
    assert bic.same_photo(first, cache.get(p), 3, 0.95) is None


# ---------------------------------------------------------------- albums
def _cover(folder: Path, kind: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{folder.name} {kind}.webp"
    Image.new("RGB", (40, 40), (10, 20, 30)).save(p, "WEBP")
    os.utime(p, (1_000_000, 1_000_000))
    return p


def test_albums_are_credited_from_fetch_logs_only(tmp_path):
    root = tmp_path / "images" / "DiSarli"
    lps, eps = root / "LPs", root / "EPs"
    _cover(lps / "Discogs Album", "Front"); _cover(lps / "Discogs Album", "Back")
    _cover(lps / "Hand Curated", "Front"); _cover(lps / "Hand Curated", "Disk 1")
    _cover(lps / "Streaming Album", "Front")
    _cover(lps / "Deezer Album", "Front")
    _cover(lps / "Band Album", "Front")
    _cover(lps / "No Record", "Front")
    _cover(lps / "Deezer Rows Only", "Front")
    _cover(eps / "Bahia Blanca", "Front")
    (lps / "Empty").mkdir()
    _write_csv(root / "_discogs_fetch_log.csv",
               ["bucket", "release_id", "title", "year", "country", "n_images", "downloaded", "status", "detail"], [
                   ["LP", "111", "Discogs: Album", "1960", "AR", "2", "2", "ok", ""],
                   ["LP", "222", "Hand Curated", "1960", "AR", "2", "2", "ok", ""],
                   ["EP", "333", "Bahia Blanca", "1958", "AR", "1", "1", "ok", ""],
                   ["LP", "444", "Bahia Blanca", "1958", "AR", "1", "1", "ok", ""],    # other bucket
                   ["LP", "555", "No Record", "1958", "AR", "1", "0", "error", "x"]])
    _write_csv(root / "_itunes_fetch_log.csv", ["collection_id", "folder", "countries", "image"],
               [["1583226403", "Streaming Album", "ar es de", "ok"]])
    _write_csv(root / "_deezer_fetch_log.csv", ["album_id", "folder", "image"],
               [["457000855", "Deezer Album", "1800x1800"],
                ["999", "Deezer Rows Only", "have"]])           # tracklist only: cover kept
    _write_csv(root / "_bandcamp_fetch_log.csv", ["release_id", "folder", "url", "image"],
               [["63171908", "Band Album", "https://someband.bandcamp.com/album/vol-1", "ok"]])
    got = bic.attribute_albums(root)
    assert got[("LPs", "Discogs Album")] == ("dg", "111")       # title -> safe folder name
    assert got[("EPs", "Bahia Blanca")] == ("dg", "333")
    assert got[("LPs", "Streaming Album")] == ("it", "ar/1583226403")
    assert got[("LPs", "Deezer Album")] == ("dz", "457000855")
    assert got[("LPs", "Band Album")] == ("bc", "someband/album/vol-1")
    for folder in ("Hand Curated", "No Record", "Deezer Rows Only"):
        assert isinstance(got[("LPs", folder)], str), folder    # a reason, not a credit
    assert ("LPs", "Empty") not in got


def test_discogs_folder_changed_after_the_fetch_is_not_credited(tmp_path):
    root = tmp_path / "images" / "X"
    _write_csv(root / "_discogs_fetch_log.csv",
               ["bucket", "release_id", "title", "downloaded", "status"],
               [["LP", "111", "Album", "1", "ok"]])
    front = _cover(root / "LPs" / "Album", "Front")
    assert bic.attribute_albums(root)[("LPs", "Album")] == ("dg", "111")
    os.utime(front, None)                                       # replaced after the log was written
    log = root / "_discogs_fetch_log.csv"
    os.utime(log, (2_000_000, 2_000_000))
    assert isinstance(bic.attribute_albums(root)[("LPs", "Album")], str)


def test_served_album_folders(tmp_path):
    _write_csv(tmp_path / "discographies.csv", ["Title", "LP_Images"], [
        ["a", '[{"type": "Front", "url": "https://images.tangotoolkit.com/SarliCarlosDi/LPs/'
              'Los%20Tangos%20M%C3%A1s%20Buscados/Los%20Tangos%20M%C3%A1s%20Buscados%20Front.webp"}]'],
        ["b", ""], ["c", "not json"]])
    assert bic.served_album_folders(tmp_path) == {("SarliCarlosDi", "LPs", "Los Tangos Más Buscados")}
    assert bic.served_album_folders(tmp_path / "missing") is None


# ---------------------------------------------------------------- index.html parity
def test_index_html_knows_every_source_code():
    """The viewer's CREDIT_SOURCES must carry every code the generator emits,
    under the same name (a code it does not know is silently not shown)."""
    html = (REPO / "index.html").read_text(encoding="utf-8")
    block = html[html.index("var CREDIT_SOURCES = {"):html.index("function loadImageCredits")]
    found = dict(re.findall(r"'([a-z0-9]+)':\s*\{ name: '([^']+)'", block))
    found = {k: v.encode("ascii").decode("unicode_escape") for k, v in found.items()}
    assert found == bic.SOURCES


def test_committed_credits_file_is_well_formed():
    path = REPO / bic.OUT_NAME
    if not path.is_file():
        pytest.skip("image_credits.txt not generated yet")
    text = path.read_text(encoding="utf-8")
    entries = bic.parse_credits(text)
    assert entries and bic.render_credits(entries) == text
    for key, (code, ref) in entries.items():
        assert code in bic.SOURCES, key
        assert re.match(r"^[^/]+/(Singles/\d{4}-\d{4}/[^/]+\.webp|(LPs|EPs)/[^/]+)$", key), key
        assert not ref.startswith("http"), key
