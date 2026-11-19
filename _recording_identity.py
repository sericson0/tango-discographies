"""Recording identities and explicit, fail-closed single-label associations.

IDs are hashes of canonical identity fields, independent of CSV row order and
image selection. An identity correction intentionally produces a new ID.

Optional recording_image_associations.json schema::

    {"version": 1, "associations": {"rec_v1_<sha256>": {
      "reviewed": true, "canonical": {<all IDENTITY_FIELDS>},
      "image_key": "Artist/Singles/bucket/file.webp",
      "reviewed_by": "reviewer", "reviewed_at": "2026-11-18T12:00:00Z",
      "evidence": "Source URL and catalogue/matrix/side evidence",
      "issue_status": "original", "issue_evidence": "Dated release/catalogue source",
      "physical_format": "78", "image_role": "label"
    }}}

Use canonical_identity(row) to prepare the full snapshot. No review is inferred
from a filename or recording date. Missing metadata remains unverified. A stale
or malformed registry is rejected; exact duplicate identities may share a key.
These associations select illustrations, not permission to publish new bytes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from _image_policy import album_art_allowed

IDENTITY_FIELDS = ("Bandleader", "Orchestra", "Date", "Title", "Singer",
                   "Label", "Master", "Matrix", "Disc")
ASSOCIATION_OUTPUT_FIELDS = (
    "Recording_ID", "Single_Image_Key", "Single_Image_Status",
    "Image_Issue_Status", "Image_Physical_Format", "Image_Role", "Image_Identity_Evidence",
    "Image_Issue_Evidence",
    "Single_Image_Conflict_Fields", "Single_Image_Conflict_Kind",
    "Image_Recording_Match", "Image_Take_Match", "Image_Release_Match",
    "Image_Take_Evidence", "Image_Release_Evidence",
)

MATCH_DIMENSIONS = ("recording", "take", "release")


def conflict_details(group: list[dict]) -> tuple[list[str], str]:
    snapshots = [canonical_identity(row) for row in group]
    fields = [field for field in IDENTITY_FIELDS
              if len({row[field] for row in snapshots}) > 1]
    # This is a research category, never an assertion that releases share a take.
    kind = "release_metadata" if fields and set(fields) <= {"Label", "Master", "Disc"} else "recording_or_take" if fields else ""
    return fields, kind


def _validate_assessment(entry: dict) -> None:
    if "assessment" not in entry:
        return
    assessment = entry["assessment"]
    if not isinstance(assessment, dict) or set(assessment) != set(MATCH_DIMENSIONS):
        raise ValueError("Assessment requires separate recording, take and release dimensions")
    for dimension in MATCH_DIMENSIONS:
        value = assessment[dimension]
        if (not isinstance(value, dict) or value.get("status") not in ("supported", "unverified", "conflicting")
                or not isinstance(value.get("evidence"), str) or not value["evidence"].strip()):
            raise ValueError(f"Invalid {dimension} match assessment or missing evidence")


def match_fields(entry: dict | None, *, selected: bool = False) -> dict:
    assessment = (entry or {}).get("assessment", {})
    return {
        "Image_Recording_Match": assessment.get("recording", {}).get("status", "supported" if selected else "unverified"),
        "Image_Take_Match": assessment.get("take", {}).get("status", "unverified"),
        "Image_Release_Match": assessment.get("release", {}).get("status", "unverified"),
        "Image_Take_Evidence": assessment.get("take", {}).get("evidence", ""),
        "Image_Release_Evidence": assessment.get("release", {}).get("evidence", ""),
    }


def normalize_date(value: str) -> str:
    """Normalize date spelling without inventing missing month/day precision."""
    value = (value or "").strip()
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if match:
        return f"{match[3]}-{int(match[1]):02d}-{int(match[2]):02d}"
    match = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", value)
    if match:
        return f"{match[1]}-{int(match[2]):02d}" + (f"-{int(match[3]):02d}" if match[3] else "")
    return value


def canonical_identity(row: dict) -> dict[str, str]:
    result = {field: unicodedata.normalize("NFC", str(row.get(field) or "").strip())
              for field in IDENTITY_FIELDS}
    result["Date"] = normalize_date(result["Date"])
    return result


def recording_id(row: dict) -> str:
    payload = json.dumps(canonical_identity(row), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "rec_v1_" + hashlib.sha256(payload).hexdigest()


def _strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value)
                   if unicodedata.category(c) != "Mn")


def legacy_single_key(row: dict) -> str | None:
    """The existing viewer/import filename convention, without the host prefix."""
    date = normalize_date(row.get("Date", ""))
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", date):
        return None
    parts = _strip_accents(row.get("Bandleader", "") or "").split()
    if not parts or not (row.get("Title") or "").strip():
        return None
    folder = re.sub(r"[^\w]", "", parts[-1] + "".join(parts[:-1]))
    start = next((i for i in range(1, len(parts))
                  if parts[i].lower() in ("de", "di", "del", "la", "las", "los")), len(parts) - 1)
    suffix = re.sub(r"[^A-Za-z0-9]+", "-", "-".join(parts[start:])).strip("-")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", _strip_accents(row["Title"])).strip("-")
    title = "-".join(word[:1].upper() + word[1:] for word in slug.split("-"))
    if not folder or not suffix or not title:
        return None
    bucket = int(date[:4]) // 5 * 5
    return f"{folder}/Singles/{bucket}-{bucket + 4}/{date}_{title}_{suffix}.webp"


def find_legacy_key_conflicts(rows: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for row in rows:
        key = legacy_single_key(row)
        if key:
            groups[key].append(row)
    return {key: group for key, group in groups.items()
            if len({recording_id(row) for row in group}) > 1}


def _valid_image_key(value) -> bool:
    # Keys are local object keys, never arbitrary URLs or traversal paths.
    return (isinstance(value, str) and bool(re.fullmatch(r"[^/\\?#%\x00-\x1f]+/Singles/[^/\\?#%\x00-\x1f]+/[^/\\?#%\x00-\x1f]+\.webp", value))
            and all(part not in (".", "..") for part in value.split("/")))


def _validate_association(identifier: str, entry: dict) -> None:
    if not isinstance(entry, dict) or entry.get("reviewed") is not True:
        raise ValueError(f"Association is not explicitly reviewed: {identifier}")
    _validate_assessment(entry)
    if "image_sha256" in entry and not re.fullmatch(r"[0-9a-f]{64}", str(entry["image_sha256"])):
        raise ValueError(f"Invalid reviewed image hash: {identifier}")
    snapshot = entry.get("canonical")
    if (not isinstance(snapshot, dict) or set(snapshot) != set(IDENTITY_FIELDS)
            or any(not isinstance(value, str) for value in snapshot.values())
            or recording_id(snapshot) != identifier):
        raise ValueError(f"Incomplete or mismatched canonical association snapshot: {identifier}")
    if not _valid_image_key(entry.get("image_key")):
        raise ValueError(f"Invalid single image key: {identifier}")
    canonical_key = legacy_single_key(snapshot)
    if not canonical_key or entry["image_key"].rsplit("/", 1)[0] != canonical_key.rsplit("/", 1)[0]:
        raise ValueError(f"Association image key has wrong artist or era routing: {identifier}")
    for field in ("reviewed_by", "reviewed_at", "evidence"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            raise ValueError(f"Missing association {field}: {identifier}")
    try:
        datetime.fromisoformat(entry["reviewed_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid association review timestamp: {identifier}") from exc
    if entry.get("issue_status", "unverified") not in ("original", "reissue", "foreign_pressing", "unverified"):
        raise ValueError(f"Invalid association issue status: {identifier}")
    if entry.get("issue_status") == "original" and (
            not isinstance(entry.get("issue_evidence"), str) or not entry["issue_evidence"].strip()):
        raise ValueError(f"Original issue requires separate release evidence: {identifier}")
    if "issue_evidence" in entry and not isinstance(entry["issue_evidence"], str):
        raise ValueError(f"Invalid association issue evidence: {identifier}")
    for field in ("physical_format", "image_role"):
        if field in entry and not isinstance(entry[field], str):
            raise ValueError(f"Invalid association {field}: {identifier}")
    if not album_art_allowed(snapshot["Bandleader"], snapshot["Date"]):
        if entry.get("physical_format") not in ("78", "78 rpm") or entry.get("image_role") != "label":
            raise ValueError(f"Recording policy requires a verified 78 rpm label: {identifier}")


def load_associations(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("associations"), dict):
        raise ValueError(f"Invalid recording image association registry: {path}")
    for identifier, entry in data["associations"].items():
        _validate_association(identifier, entry)
        _validate_selection(entry)
    return data["associations"]


def validate_association(row: dict, entry: dict) -> None:
    """Public validation for a proposed review against a current canonical row."""
    identifier = recording_id(row)
    _validate_association(identifier, entry)
    _validate_selection(entry)
    if canonical_identity(entry["canonical"]) != canonical_identity(row):
        raise ValueError(f"Association canonical snapshot changed: {identifier}")


def _validate_selection(entry: dict) -> None:
    if "assessment" in entry:
        assessment = entry["assessment"]
        if assessment["recording"]["status"] != "supported" or any(
                assessment[d]["status"] == "conflicting" for d in MATCH_DIMENSIONS):
            raise ValueError("An image selection requires a supported recording match and no conflicting assessment")


def load_reviews(path: Path) -> dict:
    """Partial findings do not select an image or clear any hold."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("reviews"), dict):
        raise ValueError(f"Invalid partial image review registry: {path}")
    for identifier, entry in data["reviews"].items():
        _validate_association(identifier, entry)
        if "assessment" not in entry or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("image_sha256", ""))):
            raise ValueError(f"Partial review requires assessments and image hash: {identifier}")
    return data["reviews"]


def apply_repo_image_associations(rows: list[dict], root: Path, associations_path: Path | None = None) -> dict:
    manifest = root / "singles_manifest.txt"
    available = {line.strip() for line in manifest.read_text(encoding="utf-8-sig").splitlines()
                 if line.strip() and not line.startswith("#")} if manifest.exists() else None
    return apply_single_image_associations(rows,
        load_associations(associations_path or root / "recording_image_associations.json"),
        load_holds(root / "recording_image_holds.json"), available,
        load_reviews(root / "recording_image_reviews.json"))


def load_holds(path: Path) -> dict:
    """Load explicit negative reviews; these remain held until deliberately resolved."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("holds"), dict):
        raise ValueError(f"Invalid recording image hold registry: {path}")
    for identifier, entry in data["holds"].items():
        _validate_hold(identifier, entry)
    return data["holds"]


def _validate_hold(identifier: str, entry: dict) -> None:
    if not isinstance(entry, dict) or entry.get("reviewed") is not True:
        raise ValueError(f"Invalid recording image hold: {identifier}")
    # Reuse exact identity, routing and reviewer validation, without asserting
    # the rejected photograph's issue or physical format is correct.
    candidate = {**entry, "reviewed": True, "issue_status": "unverified",
                 "physical_format": "78", "image_role": "label"}
    _validate_association(identifier, candidate)
    if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("image_sha256", ""))):
        raise ValueError(f"Missing reviewed image hash for hold: {identifier}")


def apply_single_image_associations(rows: list[dict], associations: dict | None = None,
                                    holds: dict | None = None, available_keys: set[str] | None = None,
                                    reviews: dict | None = None) -> dict:
    """Annotate rows; unknown/stale reviews fail closed instead of reusing a key."""
    associations = associations or {}
    holds = holds or {}
    reviews = reviews or {}
    current = {recording_id(row) for row in rows}
    for identifier, entry in holds.items():
        _validate_hold(identifier, entry)
        if identifier not in current:
            raise ValueError(f"Stale hold absent from current canonical recordings: {identifier}")
    for identifier, entry in associations.items():
        _validate_association(identifier, entry)
        _validate_selection(entry)
        if identifier not in current:
            raise ValueError(f"Stale association absent from current canonical recordings: {identifier}")
    for identifier, entry in reviews.items():
        _validate_association(identifier, entry)
        if identifier not in current:
            raise ValueError(f"Stale partial review absent from current canonical recordings: {identifier}")
    conflicts = find_legacy_key_conflicts(rows)
    details = {key: conflict_details(group) for key, group in conflicts.items()}
    selected_keys = {entry["image_key"] for identifier, entry in associations.items() if identifier not in holds}
    counts = Counter()
    for row in rows:
        identifier = recording_id(row)
        key = legacy_single_key(row)
        entry = associations.get(identifier)
        hold = holds.get(identifier)
        review = reviews.get(identifier)
        fields, kind = details.get(key, ([], ""))
        row.update(Single_Image_Conflict_Fields=";".join(fields), Single_Image_Conflict_Kind=kind,
                   **match_fields(hold or entry or review, selected=bool(entry) and not hold))
        if hold:
            row.update(Recording_ID=identifier, Single_Image_Key="", Single_Image_Status="held_review",
                       Image_Issue_Status="unverified", Image_Physical_Format="", Image_Role="",
                       Image_Identity_Evidence=hold["evidence"], Image_Issue_Evidence="")
            counts["held_review"] += 1
            continue
        status = "reviewed" if entry else "held_conflict" if key in conflicts else "legacy_unverified" if key else "unavailable"
        if not entry and key in conflicts and available_keys is not None:
            if key not in available_keys:
                status = "awaiting_image"
            elif key in selected_keys:
                status = "held_alternative"
        row.update(Recording_ID=identifier,
                   Single_Image_Key=entry["image_key"] if entry else key if status == "legacy_unverified" else "",
                   Single_Image_Status=status,
                   Image_Issue_Status=entry.get("issue_status", "unverified") if entry else "unverified",
                   Image_Physical_Format=entry.get("physical_format", "") if entry else "",
                   Image_Role=entry.get("image_role", "") if entry else "",
                   Image_Identity_Evidence=entry["evidence"] if entry else review["evidence"] if review else "",
                   Image_Issue_Evidence=entry.get("issue_evidence", "") if entry else "")
        counts[status] += 1
    return dict(counts)
