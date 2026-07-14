#!/usr/bin/env python3
"""Finalize one artist after its vision batches complete.

Merges scratchpad batch verdicts -> verdicts/<Artist>_verdicts.json, then:
apply (report + quarantine), purge --apply (delete suspect keys from R2),
sync (upload new/changed), and a targeted re-upload of recropped files.

Usage: python finalize_artist.py <Artist> <vision_dir_for_artist>
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def run(label: str, *cmd: str) -> int:
    print(f"-- {label}: {' '.join(cmd[1:])}")
    r = subprocess.run(list(cmd), cwd=str(REPO))
    if r.returncode:
        print(f"   FAILED rc={r.returncode}", file=sys.stderr)
    return r.returncode


def main() -> int:
    artist, vdir = sys.argv[1], Path(sys.argv[2])
    batches = sorted(vdir.glob("batch_*.json"))
    ins = [b for b in batches if not b.stem.endswith("_verdicts")]
    outs = {b: b.with_name(b.stem + "_verdicts.json") for b in ins}
    missing = [o.name for o in outs.values() if not o.exists()]
    if missing:
        print(f"error: verdicts missing for {artist}: {missing}", file=sys.stderr)
        return 2

    merged: dict = {}
    expect = 0
    for inb, outb in outs.items():
        expect += len(json.loads(inb.read_text(encoding="utf-8")))
        merged.update(json.loads(outb.read_text(encoding="utf-8")))
    verdicts_path = REPO / "verdicts" / f"{artist}_verdicts.json"
    verdicts_path.parent.mkdir(exist_ok=True)
    verdicts_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    print(f"merged {len(merged)} verdicts (from {expect} worklist rows) -> {verdicts_path.name}")

    py = sys.executable
    if run("apply", py, "verify_singles.py", "apply", artist, str(verdicts_path)):
        return 1
    if run("purge", py, "verify_singles.py", "purge", artist, "--apply"):
        return 1
    if run("sync", py, "sync_artist_images.py", artist):
        return 1
    crop_report = REPO / "images" / artist / "_crop_report.csv"
    if crop_report.exists():
        run("recrop refresh", py, "upload_files.py", "--from-crop-report", artist)
    print(f"== {artist} finalized ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
