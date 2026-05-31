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
