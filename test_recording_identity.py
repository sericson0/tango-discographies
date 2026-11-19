import csv
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import build
import import_singles as importer
import _recording_identity as identity


def recording(**changes):
    return dict(dict(Bandleader="Carlos Di Sarli", Orchestra="Orquesta Carlos Di Sarli",
                     Date="9/1/1942", Title="Corazón", Singer="Roberto Rufino",
                     Label="Victor", Master="", Matrix="123-1", Disc="456 A"), **changes)


def association(row, **changes):
    return dict(dict(reviewed=True, canonical=identity.canonical_identity(row),
                     image_key=identity.legacy_single_key(row).replace(".webp", "_reviewed-abc.webp"),
                     reviewed_by="Test reviewer", reviewed_at="2026-11-18T14:00:00Z",
                     evidence="https://example.org/scan; matrix 123-1 and catalogue 456 A legible",
                     issue_status="original", issue_evidence="Dated 1942 release catalogue",
                     physical_format="78", image_role="label"), **changes)


def test_identity_normalizes_dates_unicode_and_ignores_order_image_and_editorial_metadata():
    a = recording()
    b = recording(Date="1942-9-1", Title="Corazo\u0301n", Img_Folder="New art", Composer="Correction")
    assert identity.recording_id(a) == identity.recording_id(b)
    assert identity.recording_id(a) != identity.recording_id(recording(Date="1942-09"))
    assert identity.recording_id(a) != identity.recording_id(recording(Date="1942"))
    for field in identity.IDENTITY_FIELDS:
        assert identity.recording_id(a) != identity.recording_id(recording(**{field: "Different"}))


@pytest.mark.parametrize("name,date,title", [
    ("Juan D'Arienzo", "1928", "Pa' Que Bailen"),
    ("Carlos Di Sarli", "1952-5", "Colorao, Colorao"),
    ("Ángel D'Agostino", "9/1/1942", "Corazón"),
    ("Aníbal Troilo", "1942-09-01", "Lejos De Buenos Aires"),
])
def test_legacy_key_matches_current_importer(name, date, title):
    canonical = importer.iso_date(date)
    expected = (f"{importer.bandleader_folder(name)}/Singles/{importer.year_bucket(canonical)}/"
                f"{canonical}_{importer.title_segment(title)}_{importer.artist_suffix(name)}.webp")
    assert identity.legacy_single_key(recording(Bandleader=name, Date=date, Title=title)) == expected


@pytest.mark.parametrize("field", ["Singer", "Disc", "Matrix", "Orchestra", "Label", "Master"])
def test_colliding_identity_is_held_but_exact_duplicates_remain_usable(field):
    a, duplicate, b = recording(), recording(Date="1942-09-01"), recording(**{field: "Different"})
    assert not identity.find_legacy_key_conflicts([a, duplicate])
    key = identity.legacy_single_key(a)
    assert identity.find_legacy_key_conflicts([a, duplicate, b])[key] == [a, duplicate, b]
    counts = identity.apply_single_image_associations([a, duplicate, b])
    assert counts == {"held_conflict": 3}
    assert all(row["Single_Image_Key"] == "" for row in (a, duplicate, b))


def test_review_only_resolves_the_specific_identity_and_retains_issue_metadata():
    a, b = recording(), recording(Matrix="123-2")
    reviewed = association(a, issue_status="reissue", physical_format="78")
    counts = identity.apply_single_image_associations([b, a], {identity.recording_id(a): reviewed})
    assert counts == {"held_conflict": 1, "reviewed": 1}
    assert b["Single_Image_Key"] == ""
    assert a["Single_Image_Key"] == reviewed["image_key"]
    assert a["Image_Issue_Status"] == "reissue"
    assert a["Image_Physical_Format"] == "78"
    assert a["Image_Identity_Evidence"] == reviewed["evidence"]


def assessment(**statuses):
    return {name: {"status": statuses.get(name, "unverified"), "evidence": f"Evidence for {name}"}
            for name in identity.MATCH_DIMENSIONS}


def test_availability_routes_collisions_without_guessing_or_merging_takes():
    one, two = recording(), recording(Matrix="123-2")
    key = identity.legacy_single_key(one)
    assert identity.apply_single_image_associations([one, two], available_keys=set()) == {"awaiting_image": 2}
    assert all(r["Single_Image_Key"] == "" for r in [one, two])
    assert one["Single_Image_Conflict_Fields"] == "Matrix"
    assert identity.apply_single_image_associations([one, two], available_keys={key}) == {"held_conflict": 2}
    selected = association(one, image_key=key)
    assert identity.apply_single_image_associations([one, two], {identity.recording_id(one): selected},
                                                   available_keys={key}) == {"reviewed": 1, "held_alternative": 1}
    assert two["Single_Image_Key"] == ""


def test_release_metadata_is_reconciliation_category_not_automatic_recording_merge():
    rows = [recording(), recording(Label="Different release label", Master="LP")]
    key = identity.legacy_single_key(rows[0])
    identity.apply_single_image_associations(rows, available_keys={key})
    assert all(r["Single_Image_Conflict_Kind"] == "release_metadata" for r in rows)
    assert all(r["Single_Image_Status"] == "held_conflict" and not r["Single_Image_Key"] for r in rows)
    assert identity.recording_id(rows[0]) != identity.recording_id(rows[1])


def test_independent_match_dimensions_do_not_certify_take_or_original_issue(tmp_path):
    row = recording()
    rid = identity.recording_id(row)
    review = association(row, assessment=assessment(recording="supported", release="supported"),
                         issue_status="unverified", image_sha256="a" * 64)
    path = tmp_path / "recording_image_reviews.json"
    path.write_text(json.dumps({"version": 1, "reviews": {rid: review}}), encoding="utf-8")
    reviews = identity.load_reviews(path)
    sibling = recording(Matrix="123-2")
    identity.apply_single_image_associations([row, sibling], reviews=reviews)
    assert row["Single_Image_Status"] == "held_conflict" and not row["Single_Image_Key"]
    assert row["Image_Recording_Match"] == "supported"
    identity.apply_single_image_associations([row], {rid: review})
    assert row["Single_Image_Status"] == "reviewed"
    assert row["Image_Take_Match"] == row["Image_Issue_Status"] == "unverified"
    assert row["Image_Release_Match"] == "supported"
    with pytest.raises(ValueError, match="Stale partial review"):
        identity.apply_single_image_associations([recording(Matrix="changed")], reviews=reviews)
    for statuses in ({"recording": "unverified"}, {"recording": "supported", "take": "conflicting"}):
        with pytest.raises(ValueError, match="supported recording match"):
            identity.validate_association(row, {**review, "assessment": assessment(**statuses)})
    review["assessment"]["take"] = {"status": "supported", "evidence": ""}
    with pytest.raises(ValueError, match="missing evidence"):
        identity.validate_association(row, review)


@pytest.mark.parametrize("change", [
    {"reviewed": False}, {"evidence": ""}, {"reviewed_by": ""},
    {"reviewed_at": "not-a-date"}, {"issue_status": "probably original"},
    {"issue_evidence": ""}, {"physical_format": "45"}, {"image_role": "front"},
    {"canonical": {"Title": "Corazón"}},
    {"image_key": "https://example.org/scan.webp"},
    {"image_key": "../Singles/1940-1944/file.webp"},
    {"image_key": "SarliCarlosDi/Singles/../file.webp"},
    {"image_key": "SarliCarlosDi/Singles/1940-1944/file.webp?query"},
    {"image_key": "OtherArtist/Singles/1940-1944/file.webp"},
    {"image_key": "SarliCarlosDi/Singles/1950-1954/file.webp"},
])
def test_review_rejects_incomplete_unreviewed_or_unsafe_associations(change):
    with pytest.raises(ValueError):
        identity.validate_association(recording(), association(recording(), **change))


def test_stale_review_rejected_instead_of_attached_by_old_filename():
    old = recording()
    with pytest.raises(ValueError, match="Stale association"):
        identity.apply_single_image_associations([recording(Matrix="123-2")],
                                                {identity.recording_id(old): association(old)})


def test_missing_registry_is_optional_and_valid_registry_loads(tmp_path):
    path = tmp_path / "recording_image_associations.json"
    assert identity.load_associations(path) == {}
    entries = {identity.recording_id(recording()): association(recording())}
    path.write_text(json.dumps({"version": 1, "associations": entries}), encoding="utf-8")
    assert identity.load_associations(path) == entries


def test_negative_review_overrides_positive_and_legacy_associations(tmp_path):
    row = recording()
    identifier = identity.recording_id(row)
    hold = dict(association(row), image_sha256="a" * 64,
                evidence="Photographed catalogue differs from the canonical disc.")
    path = tmp_path / "recording_image_holds.json"
    assert identity.load_holds(path) == {}
    path.write_text(json.dumps({"version": 1, "holds": {identifier: hold}}), encoding="utf-8")
    holds = identity.load_holds(path)
    for positives in ({}, {identifier: association(row)}):
        rows = [recording()]
        assert identity.apply_single_image_associations(rows, positives, holds) == {"held_review": 1}
        assert rows[0]["Single_Image_Key"] == ""
        assert rows[0]["Image_Identity_Evidence"] == hold["evidence"]
        assert rows[0]["Image_Physical_Format"] == ""
    with pytest.raises(ValueError, match="Stale hold"):
        identity.apply_single_image_associations([recording(Matrix="changed")], {}, holds)
    hold["image_sha256"] = ""
    path.write_text(json.dumps({"version": 1, "holds": {identifier: hold}}), encoding="utf-8")
    with pytest.raises(ValueError, match="image hash"):
        identity.load_holds(path)


def test_build_writes_stable_recording_associations_and_keeps_album_policy(tmp_path):
    csv_dir = tmp_path / "csv_files"
    csv_dir.mkdir()
    rows = [recording(), recording(Matrix="123-2"), recording(Title="Other song")]
    source = csv_dir / "artist.csv"
    output = tmp_path / "compiled.csv"
    def write_source(records):
        with source.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(records)
    def compiled():
        build.build(csv_dir, output, tmp_path / "lp_matches")
        with output.open(encoding="utf-8-sig", newline="") as stream:
            return {row["Recording_ID"]: row for row in csv.DictReader(stream)}
    write_source(rows)
    first = compiled()
    write_source(reversed(rows))
    assert compiled() == first
    assert first[identity.recording_id(rows[0])]["Single_Image_Status"] == "held_conflict"
    assert first[identity.recording_id(rows[2])]["Single_Image_Status"] == "legacy_unverified"
    registry = {"version": 1, "associations": {identity.recording_id(rows[0]): association(rows[0])}}
    (tmp_path / "recording_image_associations.json").write_text(json.dumps(registry), encoding="utf-8")
    assert compiled()[identity.recording_id(rows[0])]["Single_Image_Status"] == "reviewed"
    hold = dict(association(rows[0]), image_sha256="a" * 64)
    (tmp_path / "recording_image_holds.json").write_text(
        json.dumps({"version": 1, "holds": {identity.recording_id(rows[0]): hold}}), encoding="utf-8")
    assert compiled()[identity.recording_id(rows[0])]["Single_Image_Status"] == "held_review"


def test_viewer_uses_explicit_association_and_never_guesses_held_keys():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js needed to exercise viewer JavaScript")
    html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    code = html[html.index("  const IMAGE_BASE ="):html.index("  // Set of single keys")]
    row = recording()
    rows = [row, dict(row, Single_Image_Status="held_conflict", Single_Image_Key=""),
            dict(row, Single_Image_Status="reviewed", Single_Image_Key=association(row)["image_key"]),
            dict(row, Single_Image_Status="unknown", Single_Image_Key=identity.legacy_single_key(row)),
            dict(row, Single_Image_Status="legacy_unverified", Single_Image_Key=""),
            dict(row, Single_Image_Key=identity.legacy_single_key(row)),
            dict(row, Single_Image_Status="awaiting_image", Single_Image_Key=identity.legacy_single_key(row)),
            dict(row, Single_Image_Status="held_alternative", Single_Image_Key=identity.legacy_single_key(row))]
    script = "function stripAccents(s) { return s.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); }\n"
    script += code + "\nconsole.log(JSON.stringify(" + json.dumps(rows) + ".map(imageUrl)));"
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
    values = json.loads(result.stdout)
    assert values == [build.IMAGE_BASE + "/" + identity.legacy_single_key(row), None,
                      build.IMAGE_BASE + "/" + association(row)["image_key"], None, None, None, None, None]


def test_viewer_discloses_recording_take_and_release_confidence_separately():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js needed to exercise viewer JavaScript")
    html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    code = html[html.index("  function imageList(row)"):html.index("  // --- Date parsing ---")]
    script = "var singlesManifest=null,EP_PREFERRED={}; function imageUrl(){return 'https://example.org/label.webp'}; function parseLpImages(){return []};\n"
    script += code + "\nconsole.log(JSON.stringify(imageList(" + json.dumps(dict(
        Single_Image_Status="reviewed", Image_Physical_Format="78", Image_Recording_Match="supported",
        Image_Take_Match="unverified", Image_Release_Match="supported", Image_Issue_Status="unverified")) + ")));"
    values = json.loads(subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, encoding="utf-8").stdout)
    assert "recording matched" in values[0]["type"]
    assert "take unverified" in values[0]["type"]
    assert "release matched" in values[0]["type"]
    assert "original issue unverified" in values[0]["type"]
