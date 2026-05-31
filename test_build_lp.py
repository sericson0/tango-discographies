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
    assert data[key]["manifest"]["Tigre Viejo"] == [("Front", "LP"), ("Back", "LP")]


def test_load_lp_data_missing_dir_returns_empty(tmp_path):
    assert build.load_lp_data(tmp_path / "nope") == {}


def _lp_data():
    return {
        "juan darienzo": {
            "matches": {
                build.join_key("1969-09-18", "Tigre Viejo", "Instrumental"): [
                    {"LP_Folder": "Tigre Viejo", "LP_Catalog": "AVL-3925", "LP_Title": "Tigre Viejo"},
                ],
                build.join_key("1965-08-11", "La Tablada", "Instrumental"): [
                    {"LP_Folder": "DArienzo Interpreta a Canaro", "LP_Catalog": "AVL-3603",
                     "LP_Title": "Juan D'Arienzo Interpreta a Canaro"},
                    {"LP_Folder": "Tiempos Viejos", "LP_Catalog": "AVS-4360", "LP_Title": "Tiempos Viejos"},
                ],
            },
            "manifest": {
                "Tigre Viejo": [("Back", "LP"), ("Front", "LP"), ("Disk 1", "LP")],
                "DArienzo Interpreta a Canaro": [("Front", "LP")],
                "Tiempos Viejos": [("Front", "LP")],
            },
        }
    }


def test_apply_lp_images_sets_columns_front_first():
    rows = [{"Bandleader": "Juan D'Arienzo", "Date": "1969-09-18",
             "Title": "Tigre Viejo", "Singer": "Instrumental"}]
    build.apply_lp_images(rows, _lp_data())
    assert rows[0]["LP_Title"] == "Tigre Viejo"
    images = json.loads(rows[0]["LP_Images"])
    assert images[0]["type"] == "Front"  # Front floated to front regardless of manifest order
    assert {im["type"] for im in images} == {"Front", "Back", "Disk 1"}
    assert images[0]["url"].endswith("/Tigre%20Viejo/Tigre%20Viejo%20Front.webp")


def test_apply_lp_images_picks_lowest_catalog():
    rows = [{"Bandleader": "Juan D'Arienzo", "Date": "1965-08-11",
             "Title": "La Tablada", "Singer": "Instrumental"}]
    build.apply_lp_images(rows, _lp_data())
    # AVL-3603 < AVS-4360 -> original "Interpreta a Canaro" wins
    assert rows[0]["LP_Title"] == "Juan D'Arienzo Interpreta a Canaro"
    images = json.loads(rows[0]["LP_Images"])
    assert "DArienzo%20Interpreta%20a%20Canaro" in images[0]["url"]


def test_apply_lp_images_no_match_leaves_empty():
    rows = [{"Bandleader": "Juan D'Arienzo", "Date": "1928",
             "Title": "Callejas Solo", "Singer": "Carlos Dante"}]
    build.apply_lp_images(rows, _lp_data())
    assert rows[0].get("LP_Title", "") == ""
    assert rows[0].get("LP_Images", "") == ""


def test_apply_lp_images_chosen_lp_without_manifest_falls_back():
    data = _lp_data()
    del data["juan darienzo"]["manifest"]["Tigre Viejo"]
    rows = [{"Bandleader": "Juan D'Arienzo", "Date": "1969-09-18",
             "Title": "Tigre Viejo", "Singer": "Instrumental"}]
    build.apply_lp_images(rows, data)
    assert rows[0].get("LP_Images", "") == ""


def test_apply_lp_images_other_artist_untouched():
    rows = [{"Bandleader": "Aníbal Troilo", "Date": "1969-09-18",
             "Title": "Tigre Viejo", "Singer": "Instrumental"}]
    build.apply_lp_images(rows, _lp_data())
    assert rows[0].get("LP_Images", "") == ""


def test_lp_image_url_routes_to_eps_when_kind_ep():
    url = build.lp_image_url("DArienzoJuan", "Bien Porteno", "Front", kind="EP")
    assert url == "https://pub-df59ead2b87f40468ed4dcba1d274efa.r2.dev/DArienzoJuan/EPs/Bien%20Porteno/Bien%20Porteno%20Front.webp"


def test_lp_image_url_defaults_to_lps_when_kind_missing():
    url = build.lp_image_url("DArienzoJuan", "Armenonville", "Front")
    assert "/LPs/" in url
    assert url.endswith("Armenonville%20Front.webp")


def test_apply_lp_images_uses_ep_subdir_when_manifest_kind_is_ep(tmp_path, monkeypatch):
    # Set up lp_matches dir with EP rows
    lp_dir = tmp_path / "lp_matches"
    lp_dir.mkdir()
    (lp_dir / "Juan D'Arienzo.csv").write_text(
        "LP_Folder,LP_Catalog,LP_Title,Side,Track_No,Track_Title,Track_Year,Match_Status,"
        "Disc_Date,Disc_Title,Disc_AltTitle,Singer,Master,Matrix,Note,Kind\n"
        "Bien Porteno,AVE-1,Bien Porteno,A,1,Some Song,1959,matched,"
        "1959-01-01,Some Song,,SomeSinger,,,,EP\n",
        encoding="utf-8-sig",
    )
    (lp_dir / "Juan D'Arienzo images.csv").write_text(
        "LP_Folder,Type,Kind\nBien Porteno,Front,EP\n",
        encoding="utf-8-sig",
    )
    rows = [{"Bandleader": "Juan D'Arienzo", "Date": "1959-01-01", "Title": "Some Song", "Singer": "SomeSinger"}]
    lp_data = build.load_lp_data(lp_dir)
    build.apply_lp_images(rows, lp_data)
    import json
    imgs = json.loads(rows[0]["LP_Images"])
    assert "/EPs/Bien%20Porteno/" in imgs[0]["url"]


def test_apply_lp_images_prefers_lp_over_ep_when_song_is_on_both(tmp_path):
    """When a track appears on both an LP and an EP, the LP cover wins regardless of catalog #."""
    lp_dir = tmp_path / "lp_matches"
    lp_dir.mkdir()
    # LP has a HIGHER catalog # than the EP — old logic (lowest catalog wins) would pick EP.
    (lp_dir / "Juan D'Arienzo.csv").write_text(
        "LP_Folder,LP_Catalog,LP_Title,Side,Track_No,Track_Title,Track_Year,Match_Status,"
        "Disc_Date,Disc_Title,Disc_AltTitle,Singer,Master,Matrix,Note,Kind\n"
        "Big LP,AVL-9999,Big LP Title,A,1,Shared Song,1961,matched,1961-01-01,Shared Song,,Singer,,,,LP\n"
        "Small EP,AVE-100,,A,1,Shared Song,1961,matched,1961-01-01,Shared Song,,Singer,,,,EP\n",
        encoding="utf-8-sig",
    )
    (lp_dir / "Juan D'Arienzo images.csv").write_text(
        "LP_Folder,Type,Kind\nBig LP,Front,LP\nSmall EP,Front,EP\n",
        encoding="utf-8-sig",
    )
    rows = [{"Bandleader": "Juan D'Arienzo", "Date": "1961-01-01", "Title": "Shared Song", "Singer": "Singer"}]
    build.apply_lp_images(rows, build.load_lp_data(lp_dir))
    import json
    imgs = json.loads(rows[0]["LP_Images"])
    assert "/LPs/Big%20LP/" in imgs[0]["url"], imgs


def test_apply_lp_images_skips_rows_older_than_1952(tmp_path):
    """78rpm-era recordings don't get LP/EP art even if matched to a later LP."""
    lp_dir = tmp_path / "lp_matches"
    lp_dir.mkdir()
    (lp_dir / "Juan D'Arienzo.csv").write_text(
        "LP_Folder,LP_Catalog,LP_Title,Side,Track_No,Track_Title,Track_Year,Match_Status,"
        "Disc_Date,Disc_Title,Disc_AltTitle,Singer,Master,Matrix,Note,Kind\n"
        "Reissue,AVL-1,Reissue,A,1,Old Song,1939,matched,1939-08-09,Old Song,,Singer,,,,LP\n"
        "Reissue,AVL-1,Reissue,A,2,New Song,1953,matched,1953-08-09,New Song,,Singer,,,,LP\n",
        encoding="utf-8-sig",
    )
    (lp_dir / "Juan D'Arienzo images.csv").write_text(
        "LP_Folder,Type,Kind\nReissue,Front,LP\n",
        encoding="utf-8-sig",
    )
    rows = [
        {"Bandleader": "Juan D'Arienzo", "Date": "1939-08-09", "Title": "Old Song", "Singer": "Singer"},
        {"Bandleader": "Juan D'Arienzo", "Date": "1953-08-09", "Title": "New Song", "Singer": "Singer"},
    ]
    build.apply_lp_images(rows, build.load_lp_data(lp_dir))
    assert rows[0].get("LP_Images", "") == ""   # 1939 -> skipped (before 1952 cutoff)
    assert rows[1].get("LP_Images", "") != ""   # 1953 -> covered
