#!/usr/bin/env python3
"""Migrate Singles image storage from Grouping-named buckets to 5-year buckets.

Historically each single's image lived under a folder named after the CSV
``Grouping`` (e.g. ``28-30 Sextet``, ``50-54 TK Figari``):

    local:  images/<LocalArtist>/Singles/<oldbucket>/<iso>_<Title>_<Suffix>.webp
    R2 key: <BandleaderFolder>/Singles/<oldbucket>/<iso>_<Title>_<Suffix>.webp

The web client now derives the folder segment from the recording's *year*
instead (a deterministic 5-year bucket, e.g. 1928 -> ``1925-1929``). This script
moves every existing single into ``<yearbucket>`` where ``<yearbucket>`` comes
from the ISO date already embedded as the FIRST ``_``-delimited token of the
filename. Everything else (bandleader folder, title segment, artist suffix,
filename) stays byte-identical, so the migrated key is exactly what the client
now requests.

Three independent, individually-gated phases:

  --local          reorganize the local images/ tree (shutil.move)
  --r2-copy        server-side copy each old key to its new key (LEAVES old key)
  --r2-purge-old   delete old keys, but ONLY after verifying the new key exists

Every phase is DRY-RUN unless ``--apply`` is passed. With no phase flag at all,
all three run in dry-run and print a combined report. Every action (dry-run
included, prefixed ``DRYRUN-``) is appended to ``_singles_migration_manifest.csv``.

CLI:
  python _migrate_singles_to_year_buckets.py [artist] [--all] \
      [--local] [--r2-copy] [--r2-purge-old] [--apply]
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from _artist_map import ARTIST_DISPLAY
from build import bandleader_folder  # Last+First, accents/punctuation stripped (client parity)

# Force UTF-8 stdout/stderr on Windows so accented bucket/file names print cleanly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent
IMAGES = REPO / "images"
MANIFEST = REPO / "_singles_migration_manifest.csv"
MANIFEST_HEADER = ["timestamp", "phase", "artist", "action", "source", "dest", "note"]

YEAR_BUCKET_RE = re.compile(r"^\d{4}-\d{4}$")
RASTER_EXTS = {".jpg", ".jpeg", ".png"}


# ---------- canonical helpers (identical to spec / import_singles.year_bucket) ----------
def year_bucket(s: str) -> str | None:
    """5-year bucket for a date/year string: 1928 -> '1925-1929'. None if no year."""
    m = re.match(r"(\d{4})", s or "")
    if not m:
        return None
    b = (int(m.group(1)) // 5) * 5
    return f"{b}-{b+4}"


def bucket_of_filename(name: str) -> str | None:
    """Year bucket derived from the first '_'-delimited token of the file stem."""
    token = Path(name).stem.split("_", 1)[0]
    return year_bucket(token)


def is_year_bucket(name: str) -> bool:
    return bool(YEAR_BUCKET_RE.match(name))


# ---------- grouping-folder scope (only migrate real, currently-served buckets) ----------
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def grouping_folder(g: str) -> str:
    """Normalize a CSV Grouping to its on-disk folder name -- the exact form the
    website/import use: accents stripped, '(),.+' dropped, whitespace collapsed,
    case preserved."""
    return re.sub(r"\s+", " ", re.sub(r"[(),.+]", "", strip_accents(g or ""))).strip()


def valid_grouping_folders(display: str) -> set[str]:
    """Authoritative set of real bucket folders for an artist = the normalized
    non-empty Grouping values in csv_files/<display>.csv. Anything on disk/R2 that
    isn't in this set (tango_info, DAHR, 78rpm dumps, orphan/renamed folders) was
    never served and must NOT be migrated."""
    out: set[str] = set()
    path = REPO / "csv_files" / f"{display}.csv"
    if not path.exists():
        return out
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            g = (row.get("Grouping") or "").strip()
            if g:
                gf = grouping_folder(g)
                if gf:
                    out.add(gf)
    return out


def folder_disposition(bucket: str, valid_set: set[str]) -> str:
    """Classify a Singles bucket folder:
      'quarantine'   -> '_'-prefixed or 'Incorrect' (matches sync_artist_images.py)
      'year'         -> already a YYYY-YYYY bucket (idempotent / already migrated)
      'migrate'      -> normalized name is a real CSV grouping
      'non-grouping' -> source-dump / orphan folder the client never served
    An empty valid_set makes every non-year, non-quarantine folder 'non-grouping'."""
    if bucket.startswith("_") or bucket == "Incorrect":
        return "quarantine"
    if is_year_bucket(bucket):
        return "year"
    if grouping_folder(bucket) in valid_set:
        return "migrate"
    return "non-grouping"


# ---------- manifest ----------
class Manifest:
    """Append-only CSV log of every action (dry-run rows get a DRYRUN- prefix)."""

    def __init__(self, path: Path, apply: bool):
        self.apply = apply
        new = not path.exists()
        self._fh = path.open("a", encoding="utf-8", newline="")
        self._w = csv.writer(self._fh)
        if new:
            self._w.writerow(MANIFEST_HEADER)

    def log(self, phase: str, artist: str, action: str, source: str = "", dest: str = "", note: str = "") -> None:
        act = action if self.apply else f"DRYRUN-{action}"
        self._w.writerow([datetime.now(timezone.utc).isoformat(), phase, artist, act, source, dest, note])

    def close(self) -> None:
        self._fh.close()


# ---------- reporting counters ----------
class LocalCounts:
    __slots__ = ("moved", "collisions", "already", "no_date", "rmdirs", "rasters", "skipq", "skipng")

    def __init__(self):
        self.moved = self.collisions = self.already = self.no_date = 0
        self.rmdirs = self.rasters = self.skipq = self.skipng = 0

    def add(self, other: "LocalCounts") -> None:
        for f in self.__slots__:
            setattr(self, f, getattr(self, f) + getattr(other, f))


class R2Counts:
    __slots__ = ("copied", "overwritten", "skipped_exists", "no_date", "rasters",
                 "would_purge", "purge_skipped", "anomalies", "skipq", "skipng")

    def __init__(self):
        self.copied = self.overwritten = self.skipped_exists = self.no_date = 0
        self.rasters = self.would_purge = self.purge_skipped = self.anomalies = 0
        self.skipq = self.skipng = 0

    def add(self, other: "R2Counts") -> None:
        for f in self.__slots__:
            setattr(self, f, getattr(self, f) + getattr(other, f))


# =====================================================================================
# Phase 1: local tree
# =====================================================================================
def migrate_local(local_artist: str, valid_set: set[str], mani: Manifest, apply: bool,
                  samples: list[tuple[str, str]], collisions_out: list[str],
                  skipped_ng_folders: list[str]) -> LocalCounts:
    c = LocalCounts()
    singles = IMAGES / local_artist / "Singles"
    if not singles.is_dir():
        return c

    for bucket in sorted(os.listdir(singles)):
        bpath = singles / bucket
        if not bpath.is_dir():
            continue

        webp_here = [f for f in os.listdir(bpath) if (bpath / f).is_file() and f.lower().endswith(".webp")]
        disp = folder_disposition(bucket, valid_set)
        rel_dir = bpath.relative_to(REPO).as_posix()
        if disp == "year":
            c.already += len(webp_here)  # already in place
            continue
        if disp == "quarantine":
            c.skipq += len(webp_here)
            mani.log("local", local_artist, "skip-quarantine", rel_dir, "",
                     f"quarantine folder '{bucket}' ({len(webp_here)} webp)")
            continue
        if disp == "non-grouping":
            c.skipng += len(webp_here)
            skipped_ng_folders.append(f"{local_artist}/Singles/{bucket}  ({len(webp_here)} webp)")
            mani.log("local", local_artist, "skip-non-grouping", rel_dir, "",
                     f"'{bucket}' not a CSV grouping ({len(webp_here)} webp)")
            continue

        # disp == "migrate": this is a real, currently-served grouping folder.
        entries_before = os.listdir(bpath)
        removed: set[str] = set()  # names that leave this old bucket (moved or deleted)

        for name in sorted(entries_before):
            fpath = bpath / name
            if not fpath.is_file():
                continue
            if name.startswith("_"):
                continue
            ext = fpath.suffix.lower()
            if ext != ".webp":
                if ext in RASTER_EXTS:
                    c.rasters += 1
                    rel = fpath.relative_to(REPO).as_posix()
                    mani.log("local", local_artist, "raster-original", rel, "", f"unhandled {ext} original")
                continue

            target = bucket_of_filename(name)
            if target is None:
                c.no_date += 1
                rel = fpath.relative_to(REPO).as_posix()
                mani.log("local", local_artist, "no-date", rel, "",
                         f"unparseable date token '{Path(name).stem.split('_', 1)[0]}'")
                continue

            if target == bucket:
                # Impossible for a non-year oldbucket, but keep the spec's guard.
                c.already += 1
                continue

            dest_dir = singles / target
            dest = dest_dir / name
            src_rel = fpath.relative_to(REPO).as_posix()
            dest_rel = dest.relative_to(REPO).as_posix()

            if dest.exists():
                # COLLISION: keep the larger by byte size; never silently overwrite.
                src_size = fpath.stat().st_size
                dst_size = dest.stat().st_size
                c.collisions += 1
                if src_size > dst_size:
                    kept_bytes, drop_bytes, kept_which = src_size, dst_size, "incoming"
                    if apply:
                        dest.unlink()
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(fpath), str(dest))
                else:
                    kept_bytes, drop_bytes, kept_which = dst_size, src_size, "existing"
                    if apply:
                        fpath.unlink()
                removed.add(name)  # src leaves the old bucket either way
                note = f"kept {kept_which} ({kept_bytes}b), dropped {drop_bytes}b"
                mani.log("local", local_artist, f"collision-kept-{kept_bytes}", src_rel, dest_rel, note)
                collisions_out.append(f"{src_rel}  ->  {dest_rel}  [{note}]")
                continue

            # Normal move.
            if apply:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(fpath), str(dest))
            removed.add(name)
            c.moved += 1
            mani.log("local", local_artist, "move", src_rel, dest_rel, f"{bucket} -> {target}")
            if len(samples) < 200:
                samples.append((src_rel, dest_rel))

        # Remove the old bucket dir if nothing remains in it.
        remaining = [e for e in entries_before if e not in removed]
        if not remaining:
            c.rmdirs += 1
            rel = bpath.relative_to(REPO).as_posix()
            mani.log("local", local_artist, "rmdir-empty", rel, "", f"old bucket '{bucket}' emptied")
            if apply:
                try:
                    os.rmdir(bpath)
                except OSError as e:
                    mani.log("local", local_artist, "rmdir-failed", rel, "", str(e))

    return c


# =====================================================================================
# R2 enumeration shared by copy + purge
# =====================================================================================
def _list_singles(client, bucket_name: str, prefix: str) -> dict[str, int]:
    """Return {key: size} for every object under prefix (paginated)."""
    out: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = obj["Size"]
    return out


def _iter_old_webp(existing: dict[str, int], prefix: str, valid_set: set[str], mani: Manifest,
                   phase: str, artist: str, counts: R2Counts, skipped_ng_folders: list[str]):
    """Yield (oldkey, size, newkey, leaf) for migratable old-bucket .webp keys.

    Migrates ONLY real CSV-grouping folders. Quarantine ('_'/Incorrect) and
    non-grouping (source-dump / orphan) folders are skipped and logged once each;
    year buckets are already-migrated no-ops; anomalies are reported.
    """
    logged_skip: set[str] = set()
    for oldkey in sorted(existing):
        size = existing[oldkey]
        rel = oldkey[len(prefix):]
        parts = rel.split("/")
        if len(parts) != 2:
            # Not <bucket>/<leaf> -- e.g. a file directly under Singles/ or nested deeper.
            counts.anomalies += 1
            mani.log(phase, artist, "anomaly-shape", oldkey, "", f"{len(parts)} segment(s) after Singles/")
            continue
        bucket, leaf = parts
        disp = folder_disposition(bucket, valid_set)
        if disp == "year":
            continue  # already migrated
        if disp == "quarantine":
            if leaf.lower().endswith(".webp"):
                counts.skipq += 1
            if bucket not in logged_skip:
                logged_skip.add(bucket)
                mani.log(phase, artist, "skip-quarantine", f"{prefix}{bucket}/", "", "quarantine folder")
            continue
        if disp == "non-grouping":
            if leaf.lower().endswith(".webp"):
                counts.skipng += 1
            if bucket not in logged_skip:
                logged_skip.add(bucket)
                skipped_ng_folders.append(f"{artist}/Singles/{bucket}")
                mani.log(phase, artist, "skip-non-grouping", f"{prefix}{bucket}/", "", "not a CSV grouping")
            continue
        # disp == "migrate": real grouping folder.
        if leaf.startswith("_"):
            continue
        if not leaf.lower().endswith(".webp"):
            if Path(leaf).suffix.lower() in RASTER_EXTS:
                counts.rasters += 1
                mani.log(phase, artist, "r2-raster", oldkey, "", "unhandled raster on R2")
            continue
        newbucket = bucket_of_filename(leaf)
        if newbucket is None:
            counts.no_date += 1
            mani.log(phase, artist, "no-date-skip", oldkey, "", "unparseable date token")
            continue
        newkey = f"{prefix}{newbucket}/{leaf}"
        yield oldkey, size, newkey, leaf


# =====================================================================================
# Phase 2: R2 server-side copy (leaves old keys in place)
# =====================================================================================
def migrate_r2_copy(local_artist: str, valid_set: set[str], client, bucket_name: str,
                    mani: Manifest, apply: bool, existing: dict[str, int], produced: set[str],
                    samples: list[tuple[str, str]], skipped_ng_folders: list[str]) -> R2Counts:
    from botocore.exceptions import ClientError  # noqa: F401 (surface import errors early)

    display = ARTIST_DISPLAY[local_artist]
    prefix = f"{bandleader_folder(display)}/Singles/"
    c = R2Counts()

    for oldkey, size, newkey, leaf in _iter_old_webp(existing, prefix, valid_set, mani,
                                                     "r2-copy", local_artist, c, skipped_ng_folders):
        # The full-prefix listing is an authoritative snapshot of what exists, so
        # membership in `existing` (kept up to date with in-run copies) is equivalent
        # to a per-key HEAD but far cheaper. `produced` covers within-run duplicates.
        existing_size = existing.get(newkey)
        if existing_size is None and newkey in produced:
            existing_size = -1  # produced this run; force collision handling, unknown size treated as small
        if existing_size is not None:
            # COLLISION: keep the larger. Overwrite (copy) only if incoming is larger.
            if size > existing_size:
                c.overwritten += 1
                mani.log("r2-copy", local_artist, "copy-overwrite", oldkey, newkey,
                         f"incoming {size}b > existing {existing_size}b")
                if apply:
                    client.copy_object(Bucket=bucket_name,
                                       CopySource={"Bucket": bucket_name, "Key": oldkey},
                                       Key=newkey, ContentType="image/webp", MetadataDirective="COPY")
                existing[newkey] = size
                produced.add(newkey)
            else:
                c.skipped_exists += 1
                mani.log("r2-copy", local_artist, "skip-exists", oldkey, newkey,
                         f"existing {existing_size}b >= incoming {size}b")
            continue

        # Fresh copy.
        c.copied += 1
        mani.log("r2-copy", local_artist, "copy", oldkey, newkey, "new key")
        if apply:
            client.copy_object(Bucket=bucket_name,
                               CopySource={"Bucket": bucket_name, "Key": oldkey},
                               Key=newkey, ContentType="image/webp", MetadataDirective="COPY")
        existing[newkey] = size
        produced.add(newkey)
        if len(samples) < 200:
            samples.append((oldkey, newkey))

    return c


# =====================================================================================
# Phase 3: R2 purge old keys (only after new key confirmed present)
# =====================================================================================
def _r2_head(client, bucket_name: str, key: str):
    """head_object -> response dict, or None on 404/NotFound."""
    from botocore.exceptions import ClientError
    try:
        return client.head_object(Bucket=bucket_name, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return None
        raise


def migrate_r2_purge(local_artist: str, valid_set: set[str], client, bucket_name: str,
                     mani: Manifest, apply: bool, existing: dict[str, int], produced: set[str],
                     skipped_ng_folders: list[str]) -> R2Counts:
    display = ARTIST_DISPLAY[local_artist]
    prefix = f"{bandleader_folder(display)}/Singles/"
    c = R2Counts()

    for oldkey, size, newkey, leaf in _iter_old_webp(existing, prefix, valid_set, mani,
                                                     "r2-purge", local_artist, c, skipped_ng_folders):
        # VERIFY the migrated copy is present before deleting the original.
        # Apply mode HEADs the new key authoritatively; dry-run trusts the listing
        # snapshot plus any keys a preceding --r2-copy phase produced this run.
        if apply:
            present = _r2_head(client, bucket_name, newkey) is not None
        else:
            present = newkey in existing or newkey in produced

        if not present:
            c.purge_skipped += 1
            mani.log("r2-purge", local_artist, "purge-skip", oldkey, newkey,
                     "new key MISSING; NOT deleting old key")
            continue

        c.would_purge += 1
        mani.log("r2-purge", local_artist, "purge-old", oldkey, newkey, "new key verified present")
        if apply:
            client.delete_object(Bucket=bucket_name, Key=oldkey)

    return c


# =====================================================================================
# driver
# =====================================================================================
def resolve_local_targets(artist: str | None, do_all: bool) -> list[str]:
    """Local folders under images/ that are in ARTIST_DISPLAY."""
    if artist and not do_all:
        return [artist]
    if not IMAGES.is_dir():
        return []
    return [d.name for d in sorted(IMAGES.iterdir())
            if d.is_dir() and d.name in ARTIST_DISPLAY]


def resolve_r2_targets(artist: str | None, do_all: bool) -> list[str]:
    if artist and not do_all:
        return [artist]
    return sorted(ARTIST_DISPLAY)


def _valid_set_or_warn(local_artist: str, phase: str) -> set[str]:
    """Real grouping-folder set for an artist; warn (and never migrate blindly)
    if the CSV is missing or has no groupings."""
    display = ARTIST_DISPLAY[local_artist]
    vs = valid_grouping_folders(display)
    if not vs:
        print(f"  WARNING [{phase}] {local_artist}: no groupings in csv_files/{display}.csv "
              f"-> ALL non-year folders skipped (nothing migrated for this artist).", file=sys.stderr)
    return vs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("artist", nargs="?", help="local images/<folder> name, e.g. Canaro")
    ap.add_argument("--all", action="store_true", help="iterate every ARTIST_DISPLAY artist")
    ap.add_argument("--local", action="store_true", help="reorganize the local images/ tree")
    ap.add_argument("--r2-copy", action="store_true", help="create new year-bucket keys on R2 (leaves old keys)")
    ap.add_argument("--r2-purge-old", action="store_true", help="delete old keys whose new key is confirmed present")
    ap.add_argument("--apply", action="store_true", help="actually mutate (default is dry-run)")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.artist and args.artist not in ARTIST_DISPLAY:
        print(f"error: unknown artist '{args.artist}'. Known: {', '.join(sorted(ARTIST_DISPLAY))}", file=sys.stderr)
        return 2

    phase_flags = (args.local, args.r2_copy, args.r2_purge_old)
    combined = not any(phase_flags)
    # No phase flag -> run all three in dry-run (never mutate implicitly).
    apply = args.apply and not combined
    do_local = args.local or combined
    do_copy = args.r2_copy or combined
    do_purge = args.r2_purge_old or combined

    mani = Manifest(MANIFEST, apply)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"== singles year-bucket migration [{mode}] ==")
    if combined:
        print("(no phase flag given -> all three phases, forced dry-run)")

    try:
        # ---- local ----
        if do_local:
            targets = resolve_local_targets(args.artist, args.all)
            print(f"\n-- LOCAL phase over {len(targets)} artist folder(s) --")
            total = LocalCounts()
            samples: list[tuple[str, str]] = []
            collisions_out: list[str] = []
            skipped_ng: list[str] = []
            for a in targets:
                valid_set = _valid_set_or_warn(a, "local")
                c = migrate_local(a, valid_set, mani, apply, samples, collisions_out, skipped_ng)
                if any((c.moved, c.collisions, c.no_date, c.rmdirs, c.rasters, c.skipq, c.skipng)):
                    print(f"  {a:16s} moved={c.moved} collisions={c.collisions} "
                          f"already={c.already} no-date={c.no_date} rmdir={c.rmdirs} raster={c.rasters} "
                          f"skip-quar={c.skipq} skip-nongrp={c.skipng}")
                total.add(c)
            print(f"  LOCAL TOTAL: moved={total.moved} collisions={total.collisions} "
                  f"already-migrated={total.already} no-date={total.no_date} "
                  f"rmdir-empty={total.rmdirs} raster-originals={total.rasters} "
                  f"skip-quarantine={total.skipq} skip-non-grouping={total.skipng}")
            print(f"\n  sample old -> new moves ({min(8, len(samples))} of {len(samples)}):")
            for src, dst in samples[:8]:
                print(f"    {src}\n      -> {dst}")
            print(f"\n  collisions ({len(collisions_out)}):")
            for line in collisions_out:
                print(f"    {line}")
            print(f"\n  skip-non-grouping folders ({len(skipped_ng)}):")
            for line in skipped_ng:
                print(f"    {line}")

        # ---- R2 phases ----
        if do_copy or do_purge:
            try:
                import _r2
                cfg = _r2.load_env()
            except Exception as e:
                print(f"\n-- R2 phases SKIPPED: {type(e).__name__}: {e} --")
            else:
                client = _r2.make_client(cfg)
                r2_targets = resolve_r2_targets(args.artist, args.all)

                if do_copy:
                    print(f"\n-- R2-COPY phase over {len(r2_targets)} artist(s) --")
                    total = R2Counts()
                    samples = []
                    skipped_ng = []
                    for a in r2_targets:
                        valid_set = _valid_set_or_warn(a, "r2-copy")
                        prefix = f"{bandleader_folder(ARTIST_DISPLAY[a])}/Singles/"
                        existing = _list_singles(client, cfg.bucket, prefix)
                        produced: set[str] = set()
                        c = migrate_r2_copy(a, valid_set, client, cfg.bucket, mani, apply,
                                            existing, produced, samples, skipped_ng)
                        if any((c.copied, c.overwritten, c.skipped_exists, c.no_date, c.rasters,
                                c.anomalies, c.skipq, c.skipng)):
                            print(f"  {a:16s} copied={c.copied} overwrite={c.overwritten} "
                                  f"skip-exists={c.skipped_exists} no-date={c.no_date} "
                                  f"raster={c.rasters} anomaly={c.anomalies} "
                                  f"skip-quar={c.skipq} skip-nongrp={c.skipng}")
                        total.add(c)
                    print(f"  R2-COPY TOTAL: copied={total.copied} overwrite={total.overwritten} "
                          f"skipped-exists={total.skipped_exists} collisions={total.overwritten + total.skipped_exists} "
                          f"no-date={total.no_date} raster={total.rasters} anomalies={total.anomalies} "
                          f"skip-quarantine={total.skipq} skip-non-grouping={total.skipng}")
                    print(f"\n  sample old -> new R2 keys ({min(8, len(samples))} of {len(samples)}):")
                    for src, dst in samples[:8]:
                        print(f"    {src}\n      -> {dst}")
                    print(f"\n  skip-non-grouping folders ({len(skipped_ng)}):")
                    for line in skipped_ng:
                        print(f"    {line}")

                if do_purge:
                    print(f"\n-- R2-PURGE-OLD phase over {len(r2_targets)} artist(s) --")
                    total = R2Counts()
                    skipped_ng = []
                    for a in r2_targets:
                        valid_set = _valid_set_or_warn(a, "r2-purge")
                        prefix = f"{bandleader_folder(ARTIST_DISPLAY[a])}/Singles/"
                        existing = _list_singles(client, cfg.bucket, prefix)
                        produced = set()
                        c = migrate_r2_purge(a, valid_set, client, cfg.bucket, mani, apply,
                                             existing, produced, skipped_ng)
                        if c.would_purge or c.purge_skipped:
                            print(f"  {a:16s} would-purge={c.would_purge} purge-skip={c.purge_skipped}")
                        total.add(c)
                    print(f"  R2-PURGE TOTAL: would-purge={total.would_purge} "
                          f"purge-skipped(missing new key)={total.purge_skipped} "
                          f"skip-quarantine={total.skipq} skip-non-grouping={total.skipng}")
    finally:
        mani.close()

    print(f"\nmanifest appended -> {MANIFEST.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
