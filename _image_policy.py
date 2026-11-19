"""User-directed image scope shared by acquisition and assignment pipelines."""

import re

# Gardel and Firpo recordings are represented only by 78 rpm record-label scans.
# Later reissue/compilation sleeves must not substitute for these recordings.
LABEL_ONLY_ARTISTS = frozenset({"Gardel", "Firpo"})


def album_art_allowed(artist_key: str, recording_date: str | None = None) -> bool:
    """Artist discovery is allowed; dated assignments require 1950 or later.

    None asks only about artist-wide discovery. Unknown dates retain the
    existing explicit-undated matching behavior; callers still establish
    recording identity and dates before approving new associations.
    """
    artist = re.sub(r'[^a-z]', '', (artist_key or '').casefold())
    if artist in {'gardel', 'carlosgardel', 'firpo', 'robertofirpo'}:
        return False
    if recording_date is None:
        return True
    years = re.findall(r'(?<!\d)((?:18|19|20)\d{2})(?!\d)', recording_date)
    return not years or min(map(int, years)) >= 1950


def discogs_format_allowed(artist_key: str, formats) -> bool:
    """Label-only artists require positive physical 78rpm format evidence."""
    if album_art_allowed(artist_key):
        return True
    return any(fmt.get('name') in ('Shellac', 'Vinyl') and
               any(re.fullmatch(r'78\s*rpm', str(value).strip(), re.I)
                   for value in (fmt.get('descriptions') or []))
               for fmt in (formats or []) if isinstance(fmt, dict))
