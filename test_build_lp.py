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


def test_lp_image_url_routes_to_eps_when_kind_ep():
    url = build.lp_image_url("DArienzoJuan", "Bien Porteno", "Front", kind="EP")
    assert url == "https://pub-df59ead2b87f40468ed4dcba1d274efa.r2.dev/DArienzoJuan/EPs/Bien%20Porteno/Bien%20Porteno%20Front.webp"


def test_lp_image_url_defaults_to_lps_when_kind_missing():
    url = build.lp_image_url("DArienzoJuan", "Armenonville", "Front")
    assert "/LPs/" in url
    assert url.endswith("Armenonville%20Front.webp")


# --- Current Img_* / load_manifests API ---
#
# apply_lp_images(rows, manifests) sets LP_Title (from Img_Album) and LP_Images
# on rows whose Img_Type is EP/LP and whose Img_Folder matches a manifest entry.
# `manifests` is {artist_key: {folder: [(type, kind), ...]}} (see load_manifests).


def test_apply_lp_images_lp_cover_is_front_first_when_present():
    manifests = {
        "juan darienzo": {
            "Tigre Viejo": [("Back", "LP"), ("Front", "LP"), ("Disk 1", "LP")],
        }
    }
    rows = [{"Bandleader": "Juan D'Arienzo", "Img_Type": "LP",
             "Img_Folder": "Tigre Viejo", "Img_Album": "Tigre Viejo"}]
    build.apply_lp_images(rows, manifests)
    assert rows[0]["LP_Title"] == "Tigre Viejo"
    images = json.loads(rows[0]["LP_Images"])
    assert images[0]["type"] == "Front"  # Front floats to cover regardless of manifest order
    assert {im["type"] for im in images} == {"Front", "Back", "Disk 1"}
    assert images[0]["url"].endswith("/Tigre%20Viejo/Tigre%20Viejo%20Front.webp")


def test_apply_lp_images_no_img_type_leaves_empty():
    rows = [{"Bandleader": "Juan D'Arienzo", "Img_Type": "", "Img_Folder": ""}]
    build.apply_lp_images(rows, {"juan darienzo": {"Tigre Viejo": [("Front", "LP")]}})
    assert rows[0].get("LP_Title", "") == ""
    assert rows[0].get("LP_Images", "") == ""


def test_apply_lp_images_other_artist_untouched():
    manifests = {"juan darienzo": {"Tigre Viejo": [("Front", "LP")]}}
    rows = [{"Bandleader": "Aníbal Troilo", "Img_Type": "LP",
             "Img_Folder": "Tigre Viejo", "Img_Album": "Tigre Viejo"}]
    build.apply_lp_images(rows, manifests)
    assert rows[0].get("LP_Images", "") == ""


def test_apply_lp_images_ep_uses_eps_subdir_and_side_disk():
    """EP cover = the disk for the track's side (A -> Disk 1), via the EPs/ subdir."""
    manifests = {
        "juan darienzo": {
            "Bien Porteno": [("Front", "EP"), ("Disk 1", "EP"), ("Disk 2", "EP")],
        }
    }
    rows = [{"Bandleader": "Juan D'Arienzo", "Img_Type": "EP", "Img_Side": "A",
             "Img_Folder": "Bien Porteno", "Img_Album": "Bien Porteno"}]
    build.apply_lp_images(rows, manifests)
    imgs = json.loads(rows[0]["LP_Images"])
    assert "/EPs/Bien%20Porteno/" in imgs[0]["url"]
    assert imgs[0]["type"] == "Disk 1"  # side A -> Disk 1


# --- L1 cover fallback + M3 empty-folder drift warning ---


def test_apply_lp_images_cover_falls_back_when_front_absent():
    """L1: with no Front in the manifest, the cover must not silently be 'Back';
    fall back through front -> disk 1 -> side 1."""
    manifests = {
        "juan darienzo": {
            "Tigre Viejo": [("Back", "LP"), ("Disk 1", "LP")],
        }
    }
    rows = [{"Bandleader": "Juan D'Arienzo", "Img_Type": "LP",
             "Img_Folder": "Tigre Viejo", "Img_Album": "Tigre Viejo"}]
    build.apply_lp_images(rows, manifests)
    images = json.loads(rows[0]["LP_Images"])
    assert images[0]["type"] == "Disk 1"  # Back was NOT chosen as cover


def test_apply_lp_images_cover_warns_and_keeps_order_when_no_preferred(capsys):
    """L1: no front/disk 1/side 1 -> warn to stderr, keep first manifest entry."""
    manifests = {
        "juan darienzo": {
            "Tigre Viejo": [("Back", "LP"), ("Inner", "LP")],
        }
    }
    rows = [{"Bandleader": "Juan D'Arienzo", "Img_Type": "LP",
             "Img_Folder": "Tigre Viejo", "Img_Album": "Tigre Viejo"}]
    build.apply_lp_images(rows, manifests)
    err = capsys.readouterr().err
    assert "Tigre Viejo" in err
    assert "no preferred cover" in err
    images = json.loads(rows[0]["LP_Images"])
    assert images[0]["type"] == "Back"  # current order preserved


def test_apply_lp_images_returns_empty_folder_drift():
    """M3: an EP/LP row whose Img_Folder has no matching manifest entry is
    reported back to the caller for a build-time warning."""
    manifests = {"juan darienzo": {"Tigre Viejo": [("Front", "LP")]}}
    rows = [
        {"Bandleader": "Juan D'Arienzo", "Img_Type": "LP", "Img_Folder": "Missing Folder"},
        {"Bandleader": "Juan D'Arienzo", "Img_Type": "LP", "Img_Folder": "Tigre Viejo"},
    ]
    empty = build.apply_lp_images(rows, manifests)
    assert empty == {("LP", "Missing Folder"): 1}
    assert rows[0].get("LP_Images", "") == ""   # drift row stays empty
    assert rows[1].get("LP_Images", "") != ""   # matched row gets art
