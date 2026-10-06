import csv

import pytest

from check_data_quality import CANONICAL_COLUMNS, build_report, looks_like_valid_date, scan_file


@pytest.mark.parametrize(
    "value",
    ["1943", "1943-07", "1943-07-12", "7/12/1943", "2000-02-29"],
)
def test_supported_dates(value):
    assert looks_like_valid_date(value)


@pytest.mark.parametrize(
    "value",
    ["", "1943-00", "1943-13", "1943-02-30", "7/32/1943", "1900-02-29", "1943-7", "1943-07-12x"],
)
def test_invalid_dates(value):
    assert not looks_like_valid_date(value)


def test_current_source_schema_and_dates_do_not_raise_false_issues(tmp_path):
    path = tmp_path / "artist.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_COLUMNS)
        writer.writeheader()
        for value in ("1943", "1943-07", "1943-07-12", "7/12/1943"):
            writer.writerow({
                "Bandleader": "Artist", "Orchestra": "Orchestra",
                "Date": value, "Title": f"Title {value}", "Genre": "Tango",
            })

    report = build_report({path.name: scan_file(path)})
    assert report["totals"]["files_with_schema_mismatch"] == 0
    assert report["totals"]["total_bad_dates"] == 0


def test_unexpected_column_and_invalid_date_are_reported(tmp_path):
    path = tmp_path / "artist.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_COLUMNS + ["Unexpected"])
        writer.writeheader()
        writer.writerow({
            "Bandleader": "Artist", "Orchestra": "Orchestra",
            "Date": "1943-02-30", "Title": "Title", "Genre": "Tango",
        })

    report = build_report({path.name: scan_file(path)})
    assert report["totals"]["files_with_schema_mismatch"] == 1
    assert report["totals"]["total_bad_dates"] == 1
    assert report["files"][path.name]["extra_columns"] == ["Unexpected"]
