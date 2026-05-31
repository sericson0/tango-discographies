import pytest
import sync_artist_images as sai


def test_parse_args_full_run():
    ns = sai.parse_args(["DArienzo"])
    assert ns.artist == "DArienzo"
    assert ns.all is False
    assert ns.match_only is False
    assert ns.convert_only is False
    assert ns.upload_only is False
    assert ns.force is False
    assert ns.dry_run is False
    assert ns.quality == 85


def test_parse_args_all_flag():
    ns = sai.parse_args(["--all"])
    assert ns.all is True
    assert ns.artist is None


def test_parse_args_phase_flags():
    ns = sai.parse_args(["DArienzo", "--upload-only", "--force"])
    assert ns.upload_only is True
    assert ns.force is True


def test_parse_args_dry_run():
    ns = sai.parse_args(["DArienzo", "--dry-run"])
    assert ns.dry_run is True


def test_parse_args_quality():
    ns = sai.parse_args(["DArienzo", "--quality", "90"])
    assert ns.quality == 90


import _convert
from PIL import Image


def _make_jpeg(path, size=(8, 8), color=(255, 0, 0)):
    img = Image.new("RGB", size, color)
    img.save(path, "JPEG")


def test_convert_creates_webp_next_to_jpeg(tmp_path):
    j = tmp_path / "Front.jpg"
    _make_jpeg(j)
    pending = _convert.convert_tree(tmp_path, quality=85)
    w = tmp_path / "Front.webp"
    assert w.exists()
    assert pending == [(j, w)]


def test_convert_skips_when_webp_already_exists(tmp_path):
    j = tmp_path / "Front.jpg"
    _make_jpeg(j)
    w = tmp_path / "Front.webp"
    _make_jpeg(j)
    w.write_bytes(b"existing")
    pending = _convert.convert_tree(tmp_path, quality=85)
    assert pending == []
    assert w.read_bytes() == b"existing"


def test_convert_walks_recursively(tmp_path):
    sub = tmp_path / "EPs" / "Some EP"
    sub.mkdir(parents=True)
    _make_jpeg(sub / "Some EP Front.jpg")
    _make_jpeg(sub / "Some EP Back.jpeg")
    pending = _convert.convert_tree(tmp_path, quality=85)
    assert (sub / "Some EP Front.webp").exists()
    assert (sub / "Some EP Back.webp").exists()
    assert len(pending) == 2


def test_convert_logs_and_continues_on_pil_error(tmp_path, capsys):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not a real jpeg")
    good = tmp_path / "ok.jpg"
    _make_jpeg(good)
    pending = _convert.convert_tree(tmp_path, quality=85)
    assert (tmp_path / "ok.webp").exists()
    assert len(pending) == 1
    err = capsys.readouterr().err
    assert "broken.jpg" in err


import _manifest


def test_type_from_filename_strips_prefix_and_suffix():
    assert _manifest.type_from_filename("Armenonville", "Armenonville Front.webp") == "Front"
    assert _manifest.type_from_filename("Armenonville", "Armenonville Side 1.webp") == "Side 1"
    assert _manifest.type_from_filename("Mi Noche Triste", "Mi Noche Triste Disk 1 Alt.webp") == "Disk 1 Alt"


def test_type_from_filename_returns_empty_when_prefix_missing(capsys):
    # A stray file that doesn't match the convention -> empty type, warning logged.
    t = _manifest.type_from_filename("Armenonville", "weird-file.webp")
    assert t == ""


def test_walk_returns_rows_per_folder_per_webp(tmp_path):
    lps = tmp_path / "LPs"
    eps = tmp_path / "EPs"
    (lps / "Armenonville").mkdir(parents=True)
    (lps / "Armenonville" / "Armenonville Front.webp").write_bytes(b"")
    (lps / "Armenonville" / "Armenonville Back.webp").write_bytes(b"")
    (eps / "Chirusa").mkdir(parents=True)
    (eps / "Chirusa" / "Chirusa Front.webp").write_bytes(b"")
    rows = _manifest.walk_collection(tmp_path)
    rows = sorted(rows, key=lambda r: (r["Kind"], r["Folder"], r["Type"]))
    assert rows == [
        {"Folder": "Chirusa", "Type": "Front", "Kind": "EP"},
        {"Folder": "Armenonville", "Type": "Back", "Kind": "LP"},
        {"Folder": "Armenonville", "Type": "Front", "Kind": "LP"},
    ]


def test_walk_skips_jpegs(tmp_path):
    (tmp_path / "LPs" / "X").mkdir(parents=True)
    (tmp_path / "LPs" / "X" / "X Front.jpg").write_bytes(b"")
    (tmp_path / "LPs" / "X" / "X Front.webp").write_bytes(b"")
    rows = _manifest.walk_collection(tmp_path)
    assert len(rows) == 1
    assert rows[0]["Type"] == "Front"


def test_write_manifest_writes_correct_header_and_rows(tmp_path):
    rows = [
        {"Folder": "Armenonville", "Type": "Front", "Kind": "LP"},
        {"Folder": "Chirusa", "Type": "Front", "Kind": "EP"},
    ]
    out = tmp_path / "manifest.csv"
    _manifest.write_manifest(out, rows)
    text = out.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "LP_Folder,Type,Kind"
    assert "Armenonville,Front,LP" in text
    assert "Chirusa,Front,EP" in text


import _match


SAMPLE_DISCOG = [
    {"Date": "10/29/1958", "Title": "Adiós Chantecler", "AltTitle": "", "Singer": "Jorge Valdez", "Master": "Tape", "Matrix": "0741"},
    {"Date": "10/29/1958", "Title": "Gerardo Matos Rodríguez", "AltTitle": "", "Singer": "Mario Bustos", "Master": "Tape", "Matrix": "0743"},
    {"Date": "1/30/1975", "Title": "Para Vos Mi Querida Pebeta", "AltTitle": "", "Singer": "Osvaldo Ramos", "Master": "Tape-S", "Matrix": "16808"},
]


def test_prepare_indexes_normalizes_year_title_alt():
    recs = _match.prepare(SAMPLE_DISCOG)
    assert recs[0]["_year"] == "1958"
    assert recs[0]["_nt"] == "adios chantecler"
    assert recs[2]["_year"] == "1975"


def test_match_exact_title_same_year():
    recs = _match.prepare(SAMPLE_DISCOG)
    hit, status, note = _match.match_track("Adiós Chantecler", "1958", recs)
    assert status == "matched"
    assert hit["Singer"] == "Jorge Valdez"
    assert note == ""


def test_match_fuzzy_variant_same_year():
    recs = _match.prepare(SAMPLE_DISCOG)
    hit, status, note = _match.match_track("Para Vos Querida Pebeta", "1975", recs)
    assert status == "matched_variant"
    assert hit["Title"] == "Para Vos Mi Querida Pebeta"


def test_match_year_flex_falls_back_across_years():
    recs = _match.prepare(SAMPLE_DISCOG)
    hit, status, note = _match.match_track("Adiós Chantecler", "1959", recs)
    assert status == "matched_year_flex"
    assert hit["Date"] == "10/29/1958"
    assert "1959" in note and "1958" in note


def test_match_returns_no_title_match_when_nothing_close():
    recs = _match.prepare(SAMPLE_DISCOG)
    hit, status, note = _match.match_track("Completely Unknown Song", "1958", recs)
    assert hit is None
    assert status == "no_title_match"


def test_match_lps_csv_writes_full_row_with_kind_lp(tmp_path):
    lps_csv = tmp_path / "LPs.csv"
    lps_csv.write_text(
        "LP_Folder,LP_Catalog,LP_Title,Side,Track_No,Track_Title,Track_Year,Match_Status,Disc_Date,Disc_Title,Disc_AltTitle,Singer,Master,Matrix,Note\n"
        "Foo,AVL-1,Foo Title,A,1,Adiós Chantecler,1959,,,,,,,,\n",
        encoding="utf-8-sig",
    )
    folder_names = {"foo": "Foo"}  # case-insensitive lookup -> actual folder name
    out_rows = _match.match_lps_csv(lps_csv, _match.prepare(SAMPLE_DISCOG), folder_names)
    assert len(out_rows) == 1
    r = out_rows[0]
    assert r["Kind"] == "LP"
    assert r["LP_Folder"] == "Foo"
    assert r["Match_Status"] == "matched_year_flex"
    assert r["Disc_Date"] == "10/29/1958"
    assert r["Singer"] == "Jorge Valdez"


def test_match_eps_csv_writes_row_with_kind_ep(tmp_path):
    eps_csv = tmp_path / "EPs.csv"
    eps_csv.write_text(
        "EP,ID,Title,Year\nBar,AVE-1,Adiós Chantecler,1958\n",
        encoding="utf-8-sig",
    )
    folder_names = {"bar": "Bar"}
    out_rows = _match.match_eps_csv(eps_csv, _match.prepare(SAMPLE_DISCOG), folder_names)
    assert len(out_rows) == 1
    r = out_rows[0]
    assert r["Kind"] == "EP"
    assert r["LP_Folder"] == "Bar"
    assert r["LP_Catalog"] == "AVE-1"
    assert r["LP_Title"] == ""  # EPs.csv has no separate LP title
    assert r["Track_Title"] == "Adiós Chantecler"
    assert r["Match_Status"] == "matched"
    assert r["Disc_Date"] == "10/29/1958"


def test_write_matches_csv_includes_kind_column(tmp_path):
    rows = [{
        "LP_Folder": "Foo", "LP_Catalog": "AVL-1", "LP_Title": "Foo",
        "Side": "A", "Track_No": "1", "Track_Title": "X", "Track_Year": "1958",
        "Match_Status": "matched", "Disc_Date": "1958-01-01", "Disc_Title": "X",
        "Disc_AltTitle": "", "Singer": "Y", "Master": "M", "Matrix": "0",
        "Note": "", "Kind": "LP",
    }]
    out = tmp_path / "matches.csv"
    _match.write_matches_csv(out, rows)
    header = out.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert header[-1] == "Kind"
    assert "LP_Folder" in header
