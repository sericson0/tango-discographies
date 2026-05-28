import csv
import json

import build


def test_strip_accents():
    assert build.strip_accents("Aníbal") == "Anibal"
    assert build.strip_accents("Cumpleaños") == "Cumpleanos"


def test_bandleader_folder_strips_apostrophes_and_accents():
    assert build.bandleader_folder("Juan D'Arienzo") == "DArienzoJuan"
    assert build.bandleader_folder("Aníbal Troilo") == "TroiloAnibal"
    assert build.bandleader_folder("Ángel D'Agostino") == "DAgostinoAngel"
    assert build.bandleader_folder("") == ""


def test_artist_match_key_matches_filename_to_bandleader():
    assert build.artist_match_key("Juan DArienzo") == build.artist_match_key("Juan D'Arienzo")
    assert build.artist_match_key("Juan D'Arienzo") == "juan darienzo"


def test_normalize_date_to_iso():
    assert build.normalize_date("10/20/1969") == "1969-10-20"
    assert build.normalize_date("1969-10-20") == "1969-10-20"
    assert build.normalize_date("1969-9-18") == "1969-09-18"
    assert build.normalize_date("1969") == "1969"  # year-only passes through


def test_normalize_title_accent_case_insensitive():
    assert build.normalize_title("  Tigre   Viejo ") == "tigre viejo"
    assert build.normalize_title("Cumpleaños") == "cumpleanos"


def test_catalog_sort_value_uses_longest_digit_run():
    assert build.catalog_sort_value("AVL-3989") == 3989
    assert build.catalog_sort_value("AVL/AVS-3854") == 3854
    assert build.catalog_sort_value("") == 10**9


def test_lp_image_url_encodes_segments():
    url = build.lp_image_url("DArienzoJuan", "Tigre Viejo", "Front")
    assert url == (
        "https://pub-df59ead2b87f40468ed4dcba1d274efa.r2.dev"
        "/DArienzoJuan/LPs/Tigre%20Viejo/Tigre%20Viejo%20Front.webp"
    )
    url2 = build.lp_image_url("DArienzoJuan", "Tigre Viejo", "Disk 1")
    assert url2.endswith("/Tigre%20Viejo/Tigre%20Viejo%20Disk%201.webp")


def _write(path, header, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_load_lp_data_pairs_matches_and_manifest(tmp_path):
    lp_dir = tmp_path / "lp_matches"
    lp_dir.mkdir()
    _write(
        lp_dir / "Juan DArienzo.csv",
        ["LP_Folder", "LP_Catalog", "LP_Title", "Disc_Date", "Disc_Title", "Singer"],
        [
            ["Tigre Viejo", "AVL-3925", "Tigre Viejo", "1969-09-18", "Tigre Viejo", "Instrumental"],
            ["Con tu Compas", "AVL-3883", "Con Tu Compás", "1969-09-18", "Tigre Viejo", "Instrumental"],
        ],
    )
    _write(
        lp_dir / "Juan DArienzo images.csv",
        ["LP_Folder", "Type"],
        [
            ["Tigre Viejo", "Front"],
            ["Tigre Viejo", "Back"],
        ],
    )

    data = build.load_lp_data(lp_dir)
    key = "juan darienzo"
    assert key in data
    jk = build.join_key("1969-09-18", "Tigre Viejo", "Instrumental")
    cands = data[key]["matches"][jk]
    assert {c["LP_Folder"] for c in cands} == {"Tigre Viejo", "Con tu Compas"}
    assert data[key]["manifest"]["Tigre Viejo"] == ["Front", "Back"]


def test_load_lp_data_missing_dir_returns_empty(tmp_path):
    assert build.load_lp_data(tmp_path / "nope") == {}
