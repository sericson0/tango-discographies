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
