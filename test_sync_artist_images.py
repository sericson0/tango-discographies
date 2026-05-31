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
