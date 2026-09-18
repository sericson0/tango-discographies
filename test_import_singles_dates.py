"""Date-precision handling in import_singles, and its parity with index.html.

The discography records a recording date at whatever precision is known:
YYYY, YYYY-MM or YYYY-MM-DD. That segment is BOTH the filename key and the
lookup key, and the browser client rebuilds the same string to fetch the
image from R2 -- so Python and JavaScript have to agree character for
character. When they disagree the failure is silent and total: the file
sits on R2 and the client asks for a URL that does not exist.

Month precision was unrepresentable until 2026-08-05. iso_date returned
None for '1952-05', so emit skipped those rows ("no usable date/title") and
the client's imageUrl returned null -- 277 rows across csv_files (109
Pugliese, 84 Di Sarli, 22 De Angelis) could never be given a single.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import import_singles as im

REPO = Path(__file__).resolve().parent


# --- iso_date ------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1952-05-14", "1952-05-14"),
    ("1952-05", "1952-05"),
    ("1952-5", "1952-05"),        # zero-padded, as the slash form already was
    ("1952", "1952"),
    ("5/14/1952", "1952-05-14"),
    ("", None),
    ("unknown", None),
    ("195", None),
])
def test_iso_date_precisions(raw, expected):
    assert im.iso_date(raw) == expected


def test_month_precision_is_not_downgraded_to_year():
    """Truncating to the year would silently merge a YYYY-MM row with a
    bare-year row of the same title, giving two recordings one filename."""
    assert im.iso_date("1952-05") != im.iso_date("1952")


# --- FNAME_RE ------------------------------------------------------------

@pytest.mark.parametrize("name,date", [
    ("1952-05-14_Pimienta_Di-Sarli.webp", "1952-05-14"),
    ("1952-05_Pimienta_Di-Sarli.webp", "1952-05"),
    ("1952_Pimienta_Di-Sarli.webp", "1952"),
    ("1952-05_Pimienta_Di-Sarli_2.webp", "1952-05"),
])
def test_fname_re_round_trips_every_precision(name, date):
    m = im.FNAME_RE.match(name)
    assert m, f"filename not parseable: {name}"
    assert m.group("date") == date
    assert m.group("suffix") == "Di-Sarli"


def test_emit_parity_a_month_date_produces_a_parseable_name():
    """The guard emit.py applies before copying: a name import_singles
    cannot parse would be a dead file in the funnel."""
    iso = im.iso_date("1952-09")
    name = f"{iso}_{im.title_segment('Déjame Hablar')}_Di-Sarli.webp"
    assert name == "1952-09_Dejame-Hablar_Di-Sarli.webp"
    assert im.FNAME_RE.match(name)


def test_year_bucket_handles_month_precision():
    assert im.year_bucket("1952-05") == "1950-1954"
    assert im.year_bucket("1952-05-14") == "1950-1954"
    assert im.year_bucket("1952") == "1950-1954"


# --- Python <-> client parity -------------------------------------------

def _client_iso_branches() -> str:
    """The date-normalizing branches of imageUrl() in index.html."""
    html = (REPO / "index.html").read_text(encoding="utf-8")
    start = html.index("function imageUrl(row)")
    return html[start:html.index("var bucket = yearBucket(iso);", start)]


def test_client_accepts_month_precision_dates():
    """index.html must normalize YYYY-MM, or a month-dated single is
    uploaded to R2 and then never requested by the page."""
    src = _client_iso_branches()
    assert re.search(r"\^\(\\d\{4\}\)-\(\\d\{1,2\}\)\$", src), (
        "index.html imageUrl() has no YYYY-MM branch; it would return null "
        "for month-precision dates and the image would never be fetched")


def test_client_and_python_agree_on_every_precision():
    """Both sides must produce the SAME date segment. Checked structurally:
    the client pads the month and keeps the year, exactly as iso_date does."""
    src = _client_iso_branches()
    # year passthrough
    assert re.search(r"/\^\\d\{4\}\$/\.test\(d\)", src)
    # month precision padded, not truncated to the year
    assert "m[1] + '-' + m[2].padStart(2, '0');" in src, (
        "client must keep the padded month; truncating to the year would "
        "disagree with import_singles.iso_date and break the URL")
