#!/usr/bin/env python3
"""Export Composer/Author variant groups to text files."""

from __future__ import annotations

import json
from pathlib import Path


def write_groups(groups: list[dict], out_path: Path) -> None:
    lines = []
    for i, group in enumerate(groups, start=1):
        lines.append(f"{i}. {group['canonical']} <= {', '.join(group['variants'])}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    report_path = Path("cleaned_v2_normalization_report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    composer_groups = report["variant_groups"]["Composer"]
    author_groups = report["variant_groups"]["Author"]

    write_groups(composer_groups, Path("composer_groups.txt"))
    write_groups(author_groups, Path("author_groups.txt"))

    print(f"composer_groups {len(composer_groups)}")
    print(f"author_groups {len(author_groups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
