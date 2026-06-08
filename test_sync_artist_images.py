from pathlib import Path

import pytest
import sync_artist_images as sai


def test_parse_args_full_run():
    ns = sai.parse_args(["DArienzo"])
    assert ns.artist == "DArienzo"
    assert ns.all is False
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


def _make_png(path, size=(8, 8), color=(0, 255, 0, 128)):
    img = Image.new("RGBA", size, color)
    img.save(path, "PNG")


def test_convert_converts_png_to_webp(tmp_path):
    p = tmp_path / "Front.png"
    _make_png(p, color=(0, 255, 0, 255))
    pending = _convert.convert_tree(tmp_path, quality=85)
    w = tmp_path / "Front.webp"
    assert w.exists()
    assert pending == [(p, w)]


def test_convert_png_with_alpha_preserves_transparency(tmp_path):
    p = tmp_path / "Cover.png"
    _make_png(p, color=(0, 255, 0, 128))  # half-transparent
    _convert.convert_tree(tmp_path, quality=85, lossless=True)
    w = tmp_path / "Cover.webp"
    with Image.open(w) as out:
        assert out.mode in ("RGBA", "LA")  # alpha kept
        assert out.getpixel((0, 0))[3] == 128


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


def test_type_from_filename_accepts_intentional_alternate(capsys):
    assert _manifest.type_from_filename("Foo", "Foo Disk 1 V2.webp") == "Disk 1 V2"
    assert _manifest.type_from_filename("Foo", "Foo Front Alt.webp") == "Front Alt"
    assert _manifest.type_from_filename("Foo", "Foo Back Alt 2.webp") == "Back Alt 2"


def test_type_from_filename_keeps_nonstandard_type_with_warning(capsys):
    # A recognized prefix but a non-standard type ('Image 3') -> KEPT (it's a valid
    # extra gallery image; can never be the cover), with a drift warning.
    t = _manifest.type_from_filename("Foo", "Foo Image 3.webp")
    assert t == "Image 3"
    err = capsys.readouterr().err
    assert "non-standard cover type" in err


def test_walk_keeps_nonstandard_types(tmp_path):
    lps = tmp_path / "LPs" / "Foo"
    lps.mkdir(parents=True)
    (lps / "Foo Front.webp").write_bytes(b"")
    (lps / "Foo Image 3.webp").write_bytes(b"")  # non-standard, kept as extra gallery image
    rows = _manifest.walk_collection(tmp_path)
    assert {"Folder": "Foo", "Type": "Front", "Kind": "LP"} in rows
    assert {"Folder": "Foo", "Type": "Image 3", "Kind": "LP"} in rows


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
    assert r["Disc_Date"] == "1958-10-29"
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
    assert r["Disc_Date"] == "1958-10-29"


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


from unittest import mock
import _r2


def test_key_for_singles_keeps_grouping_subdir(tmp_path):
    # images/DArienzo/Singles/28-30 Sextet/1928_x_y.webp -> DArienzoJuan/Singles/28-30 Sextet/1928_x_y.webp
    images_dir = tmp_path / "images" / "DArienzo"
    f = images_dir / "Singles" / "28-30 Sextet" / "1928_x_y.webp"
    f.parent.mkdir(parents=True)
    f.touch()
    key = _r2.key_for_local(f, artist_root=images_dir, bandleader_folder_name="DArienzoJuan")
    assert key == "DArienzoJuan/Singles/28-30 Sextet/1928_x_y.webp"


def test_key_for_lp_routes_under_lps_subdir(tmp_path):
    images_dir = tmp_path / "images" / "DArienzo"
    f = images_dir / "LPs" / "Armenonville" / "Armenonville Front.webp"
    f.parent.mkdir(parents=True)
    f.touch()
    key = _r2.key_for_local(f, artist_root=images_dir, bandleader_folder_name="DArienzoJuan")
    assert key == "DArienzoJuan/LPs/Armenonville/Armenonville Front.webp"


def test_head_exists_returns_true_on_200():
    with mock.patch("_r2.urllib.request.urlopen") as op:
        op.return_value.__enter__.return_value.status = 200
        assert _r2.head_exists("https://x/y.webp") is True


def test_public_url_percent_encodes_spaces_and_keeps_slashes():
    url = _r2.public_url("https://pub-x.r2.dev", "Artist/LPs/Some LP/Some LP Front.webp")
    assert url == "https://pub-x.r2.dev/Artist/LPs/Some%20LP/Some%20LP%20Front.webp"


def test_public_url_strips_trailing_slash_on_base():
    assert _r2.public_url("https://pub-x.r2.dev/", "a/b.webp") == "https://pub-x.r2.dev/a/b.webp"


def test_head_exists_returns_false_on_404():
    with mock.patch("_r2.urllib.request.urlopen") as op:
        import urllib.error
        op.side_effect = urllib.error.HTTPError("u", 404, "nf", {}, None)
        assert _r2.head_exists("https://x/y.webp") is False


def test_head_exists_returns_unknown_on_5xx():
    with mock.patch("_r2.urllib.request.urlopen") as op:
        import urllib.error
        op.side_effect = urllib.error.HTTPError("u", 503, "busy", {}, None)
        assert _r2.head_exists("https://x/y.webp") is _r2.HEAD_UNKNOWN


def test_head_exists_returns_unknown_on_connection_error():
    with mock.patch("_r2.urllib.request.urlopen") as op:
        op.side_effect = TimeoutError("timed out")
        assert _r2.head_exists("https://x/y.webp") is _r2.HEAD_UNKNOWN


def test_upload_calls_put_object_with_content_type(tmp_path):
    f = tmp_path / "x.webp"
    f.write_bytes(b"webp-bytes")
    client = mock.MagicMock()
    _r2.upload_file(client, bucket="b", key="k/x.webp", path=f)
    client.put_object.assert_called_once()
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "b"
    assert kwargs["Key"] == "k/x.webp"
    assert kwargs["ContentType"] == "image/webp"
    assert kwargs["Body"].read() == b"webp-bytes"


def test_upload_retries_on_transient_then_succeeds(tmp_path):
    from botocore.exceptions import EndpointConnectionError
    f = tmp_path / "x.webp"
    f.write_bytes(b"x")
    client = mock.MagicMock()
    client.put_object.side_effect = [
        EndpointConnectionError(endpoint_url="x"),
        EndpointConnectionError(endpoint_url="x"),
        None,
    ]
    _r2.upload_file(client, bucket="b", key="k/x.webp", path=f, sleeper=lambda _: None)
    assert client.put_object.call_count == 3


def test_upload_gives_up_after_three_failures(tmp_path):
    from botocore.exceptions import EndpointConnectionError
    f = tmp_path / "x.webp"
    f.write_bytes(b"x")
    client = mock.MagicMock()
    client.put_object.side_effect = EndpointConnectionError(endpoint_url="x")
    with pytest.raises(EndpointConnectionError):
        _r2.upload_file(client, bucket="b", key="k/x.webp", path=f, sleeper=lambda _: None)
    assert client.put_object.call_count == 3


def test_make_client_reads_env(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "mybucket")
    monkeypatch.setenv("R2_PUBLIC_BASE", "https://example/r2")
    cfg = _r2.load_env()
    assert cfg.bucket == "mybucket"
    assert cfg.public_base == "https://example/r2"
    assert cfg.endpoint_url == "https://acct123.r2.cloudflarestorage.com"


def test_resolve_artist_known(monkeypatch):
    monkeypatch.setattr(sai, "ARTIST_DISPLAY", {"Foo": "Foo Bar"})
    display, csv_path = sai.resolve_artist("Foo", repo_root=Path("/r"))
    assert display == "Foo Bar"
    assert csv_path == Path("/r/csv_files/Foo Bar.csv")


def test_resolve_artist_unknown_raises():
    with pytest.raises(SystemExit):
        sai.resolve_artist("NotInMap", repo_root=Path("/r"))


def test_artist_root_under_images(tmp_path):
    root = tmp_path / "images" / "Foo"
    root.mkdir(parents=True)
    assert sai.artist_root("Foo", repo_root=tmp_path) == root


def test_sibling_originals_lists_raster_suffixes(tmp_path):
    w = tmp_path / "X" / "X Front.webp"
    sibs = sai._sibling_originals(w)
    names = {s.name for s in sibs}
    assert "X Front.jpg" in names
    assert "X Front.png" in names
    assert "X Front.jpeg" in names


def _stub_env_and_artist(tmp_path, monkeypatch):
    images = tmp_path / "images" / "Foo"
    monkeypatch.setattr(sai, "ARTIST_DISPLAY", {"Foo": "Foo"})
    (tmp_path / "csv_files").mkdir(exist_ok=True)
    (tmp_path / "csv_files" / "Foo.csv").write_text(
        "Bandleader,Date,Title,AltTitle,Singer,Master,Matrix\n", encoding="utf-8-sig"
    )
    monkeypatch.chdir(tmp_path)
    # Stub R2 layer so no network is needed.
    cfg = _r2.R2Config("acct", "ak", "sk", "bucket", "https://pub")
    monkeypatch.setattr(_r2, "load_env", lambda: cfg)
    monkeypatch.setattr(_r2, "make_client", lambda c: mock.MagicMock())
    return images


def test_upload_only_cleans_confirmed_original(tmp_path, monkeypatch, capsys):
    images = _stub_env_and_artist(tmp_path, monkeypatch)
    folder = images / "LPs" / "X"
    folder.mkdir(parents=True)
    webp = folder / "X Front.webp"
    webp.write_bytes(b"webp")
    jpg = folder / "X Front.jpg"
    jpg.write_bytes(b"jpg")
    # HEAD says already-present (confirmed) -> upload skipped, original cleaned.
    monkeypatch.setattr(_r2, "head_exists", lambda url: True)
    rc = sai.main(["Foo", "--upload-only", "--prune"])
    assert rc == 0
    assert not jpg.exists()  # confirmed -> deleted


def test_upload_only_keeps_original_when_head_unknown(tmp_path, monkeypatch, capsys):
    images = _stub_env_and_artist(tmp_path, monkeypatch)
    folder = images / "LPs" / "X"
    folder.mkdir(parents=True)
    webp = folder / "X Front.webp"
    webp.write_bytes(b"webp")
    jpg = folder / "X Front.jpg"
    jpg.write_bytes(b"jpg")
    # HEAD unknown -> no upload, no delete.
    monkeypatch.setattr(_r2, "head_exists", lambda url: _r2.HEAD_UNKNOWN)
    uploaded = {"n": 0}

    def _fake_upload(*a, **k):
        uploaded["n"] += 1

    monkeypatch.setattr(_r2, "upload_file", _fake_upload)
    rc = sai.main(["Foo", "--upload-only", "--prune"])
    assert rc == 0
    assert jpg.exists()          # NOT deleted: state unknown
    assert uploaded["n"] == 0    # NOT re-uploaded


def test_upload_only_uploads_then_cleans_on_404(tmp_path, monkeypatch, capsys):
    images = _stub_env_and_artist(tmp_path, monkeypatch)
    folder = images / "LPs" / "X"
    folder.mkdir(parents=True)
    webp = folder / "X Front.webp"
    webp.write_bytes(b"webp")
    png = folder / "X Front.png"
    png.write_bytes(b"png")
    monkeypatch.setattr(_r2, "head_exists", lambda url: False)  # missing -> upload
    monkeypatch.setattr(_r2, "upload_file", lambda *a, **k: None)
    rc = sai.main(["Foo", "--upload-only", "--prune"])
    assert rc == 0
    assert not png.exists()  # uploaded successfully -> original cleaned


def test_upload_only_keeps_original_when_upload_fails(tmp_path, monkeypatch, capsys):
    images = _stub_env_and_artist(tmp_path, monkeypatch)
    folder = images / "LPs" / "X"
    folder.mkdir(parents=True)
    webp = folder / "X Front.webp"
    webp.write_bytes(b"webp")
    jpg = folder / "X Front.jpg"
    jpg.write_bytes(b"jpg")
    monkeypatch.setattr(_r2, "head_exists", lambda url: False)

    def _boom(*a, **k):
        raise RuntimeError("upload failed")

    monkeypatch.setattr(_r2, "upload_file", _boom)
    rc = sai.main(["Foo", "--upload-only", "--prune"])
    assert rc == 0
    assert jpg.exists()  # upload failed -> original kept


def test_upload_only_without_prune_keeps_originals(tmp_path, monkeypatch, capsys):
    # Default (no --prune): a confirmed webp does NOT cause its original to be deleted.
    images = _stub_env_and_artist(tmp_path, monkeypatch)
    folder = images / "LPs" / "X"
    folder.mkdir(parents=True)
    (folder / "X Front.webp").write_bytes(b"webp")
    jpg = folder / "X Front.jpg"
    jpg.write_bytes(b"jpg")
    monkeypatch.setattr(_r2, "head_exists", lambda url: True)
    rc = sai.main(["Foo", "--upload-only"])
    assert rc == 0
    assert jpg.exists()  # no --prune -> original retained


def test_dry_run_does_not_create_files(tmp_path, monkeypatch, capsys):
    # Minimal artist tree
    images = tmp_path / "images" / "Foo"
    (images / "LPs" / "X").mkdir(parents=True)
    (images / "LPs" / "X" / "X Front.jpg").write_bytes(b"fakejpeg")
    monkeypatch.setattr(sai, "ARTIST_DISPLAY", {"Foo": "Foo"})
    # Stub the discography CSV
    (tmp_path / "csv_files").mkdir()
    (tmp_path / "csv_files" / "Foo.csv").write_text("Bandleader,Date,Title,AltTitle,Singer,Master,Matrix\n", encoding="utf-8-sig")
    # Stub the LPs.csv
    (images / "LPs" / "LPs.csv").write_text(
        "LP_Folder,LP_Catalog,LP_Title,Side,Track_No,Track_Title,Track_Year\nX,X-1,X,A,1,Y,1958\n",
        encoding="utf-8-sig",
    )
    monkeypatch.chdir(tmp_path)
    rc = sai.main(["Foo", "--dry-run"])
    assert rc == 0
    # No webp created, no matches file written, no jpeg deleted
    assert (images / "LPs" / "X" / "X Front.jpg").exists()
    assert not (images / "LPs" / "X" / "X Front.webp").exists()
    assert not (tmp_path / "lp_matches" / "Foo.csv").exists()
