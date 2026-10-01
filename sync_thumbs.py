#!/usr/bin/env python3
"""Sync R2 thumbnails, Cache-Control and singles_manifest.txt with the bucket.

The site (index.html) shows only what the bucket really holds:

  * singles_manifest.txt (repo root) lists every SERVED single key, one per
    line, sorted. The page shows a single's label image only when its key is
    in this file -- it no longer guesses URLs (~75% of guesses were 404s).
  * every served single and LP/EP image has a thumbnail at
    ``thumbs/<original key>`` (long edge <= 240 px, WEBP q78) for the table;
    the full-size original is only fetched by the detail popup.

Served keys (anything else under Singles/ -- DAHR/, tango_info/, old Grouping
folders -- is ignored, never touched):

    single:  <Folder>/Singles/<YYYY-YYYY>/<file>.webp
    LP/EP:   <Folder>/LPs/<...>.webp   or   <Folder>/EPs/<...>.webp

RUN THIS AFTER ANY BUCKET CHANGE -- import_singles.py --upload,
sync_artist_images.py, upload_files.py, verify_singles.py purge --apply
(finalize_artist.py runs it as its last step) -- then COMMIT
singles_manifest.txt: a single missing from the manifest is invisible on the
site, and a purged one stays listed (as a broken image) until the manifest is
regenerated.

Usage:
    python sync_thumbs.py                      # dry run: counts only, no writes
    python sync_thumbs.py --apply              # write thumbs, delete orphan
                                               # thumbs, write the manifest
    python sync_thumbs.py --manifest-only      # just write singles_manifest.txt
    python sync_thumbs.py --set-cache-control --apply
                                               # one-off: rewrite served
                                               # originals' Cache-Control in
                                               # place, then sync thumbs

A thumb is (re)generated when it is missing or older than its original. A
``thumbs/`` key whose original is no longer served is an orphan and deleted.
Deletion is hard-guarded to ``thumbs/`` keys; the only write outside
``thumbs/`` is --set-cache-control's in-place copy (identical bytes, new
headers) of served originals.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps

import _r2

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "singles_manifest.txt"

# Contract with index.html -- keep in sync with the page's own regexes.
SINGLE_RE = re.compile(r"^[^/]+/Singles/[0-9]{4}-[0-9]{4}/[^/]+\.webp$")
LP_EP_RE = re.compile(r"^[^/]+/(LPs|EPs)/.+\.webp$")
THUMB_PREFIX = "thumbs/"
THUMB_MAX_PX = 240
THUMB_QUALITY = 78
THUMB_METHOD = 6
CONTENT_TYPE = "image/webp"
DEFAULT_WORKERS = 8

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# --- classification ------------------------------------------------------------

def is_served_single(key: str) -> bool:
    return not key.startswith(THUMB_PREFIX) and bool(SINGLE_RE.match(key))


def is_served_lp(key: str) -> bool:
    return not key.startswith(THUMB_PREFIX) and bool(LP_EP_RE.match(key))


def is_served(key: str) -> bool:
    return is_served_single(key) or is_served_lp(key)


def thumb_key(key: str) -> str:
    return THUMB_PREFIX + key


def lookalikes(keys: Iterable[str]) -> list[str]:
    """Keys that look like served images but miss the served regexes.

    Diagnostics only (e.g. ``.WEBP`` / ``.jpg`` in a year bucket, a subfolder
    under a year bucket, a non-webp under LPs/): the page will not show them.
    """
    year_dir = re.compile(r"^[^/]+/Singles/[0-9]{4}-[0-9]{4}/")
    lp_dir = re.compile(r"^[^/]+/(LPs|EPs)/")
    out = []
    for k in keys:
        if k.startswith(THUMB_PREFIX) or is_served(k):
            continue
        if year_dir.match(k) or lp_dir.match(k):
            out.append(k)
    return sorted(out)


# --- bucket listing --------------------------------------------------------------

def list_bucket(client, bucket: str) -> dict[str, datetime]:
    """{key: LastModified} for the whole bucket (paginated list_objects_v2)."""
    out: dict[str, datetime] = {}
    kwargs = {"Bucket": bucket}
    while True:
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            out[obj["Key"]] = obj["LastModified"]
        if not resp.get("IsTruncated"):
            return out
        kwargs["ContinuationToken"] = resp["NextContinuationToken"]


# --- planning ----------------------------------------------------------------------

def plan_thumbs(listing: dict[str, datetime]) -> tuple[list[str], list[str], list[str]]:
    """-> (originals needing a NEW thumb, originals whose thumb is STALE,
    orphan thumb keys to delete). All sorted."""
    create, refresh = [], []
    for key, mtime in listing.items():
        if not is_served(key):
            continue
        t = listing.get(thumb_key(key))
        if t is None:
            create.append(key)
        elif t < mtime:
            refresh.append(key)
    orphans = []
    for k in listing:
        if k.startswith(THUMB_PREFIX):
            orig = k[len(THUMB_PREFIX):]
            if not (is_served(orig) and orig in listing):
                orphans.append(k)
    return sorted(create), sorted(refresh), sorted(orphans)


def manifest_text(listing: Iterable[str]) -> str:
    keys = sorted(k for k in listing if is_served_single(k))
    return "".join(k + "\n" for k in keys)


def write_manifest(listing: Iterable[str], path: Path = MANIFEST_PATH) -> int:
    """Write the singles manifest atomically (UTF-8, LF). Returns line count.

    Refuses to write an empty manifest: that would hide every single on the
    site, and only ever happens when the listing itself went wrong.
    """
    text = manifest_text(listing)
    n = text.count("\n")
    if n == 0:
        raise RuntimeError("refusing to write an empty singles manifest")
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return n


def manifest_diff(listing: Iterable[str], path: Path = MANIFEST_PATH) -> tuple[int, int]:
    """(added, removed) lines vs the manifest currently on disk."""
    new = set(manifest_text(listing).splitlines())
    old = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
    return len(new - old), len(old - new)


# --- thumbnail rendering --------------------------------------------------------------

def _has_alpha(img: Image.Image) -> bool:
    return (img.mode in ("RGBA", "LA", "PA", "RGBa", "La")
            or (img.mode == "P" and "transparency" in img.info))


def render_thumb(data: bytes, max_px: int = THUMB_MAX_PX) -> bytes:
    """Original image bytes -> thumbnail WEBP bytes (long edge <= max_px, never
    upscaled; alpha kept when the source has it, else RGB)."""
    with Image.open(io.BytesIO(data)) as src:
        src.seek(0)                      # first frame of an animated image
        img = ImageOps.exif_transpose(src)
        img = img.convert("RGBA" if _has_alpha(img) else "RGB")
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > max_px:
        scale = max_px / long_edge
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        img = img.resize(size, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=THUMB_QUALITY, method=THUMB_METHOD)
    return out.getvalue()


# --- guarded R2 writes ------------------------------------------------------------------

def _require_thumb_key(key: str) -> None:
    if not key.startswith(THUMB_PREFIX) or len(key) <= len(THUMB_PREFIX):
        raise ValueError(f"refusing to write/delete non-thumbnail key: {key!r}")


def put_thumb(client, bucket: str, key: str, body: bytes) -> None:
    _require_thumb_key(key)
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=CONTENT_TYPE,
                      CacheControl=_r2.CACHE_CONTROL)


def delete_thumb(client, bucket: str, key: str) -> None:
    """Delete ONE thumbnail. Hard guard: anything outside thumbs/ raises."""
    _require_thumb_key(key)
    client.delete_object(Bucket=bucket, Key=key)


def delete_thumbs(client, bucket: str, keys: list[str], workers: int = DEFAULT_WORKERS):
    """Delete thumbnail keys. Every key is checked BEFORE any request: a single
    non-``thumbs/`` key raises and nothing is deleted. -> [(key, error)]."""
    for k in keys:
        _require_thumb_key(k)
    _, fails = run_pool("delete orphans", keys,
                        lambda k: _retry(lambda: delete_thumb(client, bucket, k)), workers)
    return fails


def needs_cache_control(client, bucket: str, key: str) -> tuple[bool, dict]:
    head = client.head_object(Bucket=bucket, Key=key)
    return head.get("CacheControl") != _r2.CACHE_CONTROL, head.get("Metadata") or {}


def set_cache_control(client, bucket: str, key: str, metadata: dict | None = None) -> None:
    """In-place copy of a served original with new headers (same bytes)."""
    if not is_served(key):
        raise ValueError(f"refusing to rewrite a non-served key: {key!r}")
    client.copy_object(Bucket=bucket, Key=key, CopySource={"Bucket": bucket, "Key": key},
                       MetadataDirective="REPLACE", Metadata=metadata or {},
                       ContentType=CONTENT_TYPE, CacheControl=_r2.CACHE_CONTROL)


# --- threaded runner ------------------------------------------------------------------------

def _retry(fn: Callable[[], object], attempts: int = 3, sleeper=time.sleep):
    """Retry transient R2/network errors; anything else (a bad image) raises."""
    for i in range(attempts):
        try:
            return fn()
        except (BotoCoreError, ClientError):
            if i == attempts - 1:
                raise
            sleeper(1.0 * (i + 1))


def run_pool(label: str, items: list[str], fn: Callable[[str], object], workers: int,
             every: int = 100) -> tuple[dict[str, object], list[tuple[str, str]]]:
    """Run fn(item) on a thread pool with progress. One failure never aborts the
    run: -> ({item: result}, [(item, error)])."""
    results: dict[str, object] = {}
    failures: list[tuple[str, str]] = []
    if not items:
        return results, failures
    total = len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fn, it): it for it in items}
        for n, fut in enumerate(as_completed(futs), 1):
            it = futs[fut]
            try:
                results[it] = fut.result()
            except Exception as e:  # keep going; report at the end
                failures.append((it, f"{type(e).__name__}: {e}"))
                print(f"  fail: {it}: {e}", file=sys.stderr)
            if n % every == 0 or n == total:
                print(f"  {label}: {n}/{total} (failed {len(failures)})", flush=True)
    return results, failures


def make_thumb(client, bucket: str, key: str) -> int:
    def go():
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        thumb = render_thumb(body)
        put_thumb(client, bucket, thumb_key(key), thumb)
        return len(thumb)
    return _retry(go)


def check_cache_control(client, bucket: str, keys: list[str], workers: int):
    """-> ({key: metadata} of keys needing new headers, failures)."""
    res, fails = run_pool("cache-control check", keys,
                          lambda k: _retry(lambda: needs_cache_control(client, bucket, k)),
                          workers, every=500)
    return {k: md for k, (need, md) in res.items() if need}, fails


# --- main --------------------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true",
                   help="perform R2 writes and write singles_manifest.txt (default: dry run)")
    p.add_argument("--manifest-only", action="store_true",
                   help="only write singles_manifest.txt from a bucket listing (no R2 writes)")
    p.add_argument("--set-cache-control", action="store_true",
                   help="one-off: rewrite served originals' Cache-Control in place before thumbs")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    return p.parse_args(argv)


def _show(label: str, keys: list[str], limit: int = 5) -> None:
    for k in keys[:limit]:
        print(f"    {label}: {k}")
    if len(keys) > limit:
        print(f"    ... and {len(keys) - limit} more")


def main(argv: list[str] | None = None, client=None, bucket: str | None = None,
         manifest_path: Path = MANIFEST_PATH) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.manifest_only and args.set_cache_control:
        print("error: --manifest-only makes no R2 writes; drop --set-cache-control",
              file=sys.stderr)
        return 2
    if client is None:
        cfg = _r2.load_env()
        client, bucket = _r2.make_client(cfg), cfg.bucket

    print(f"== Listing bucket {bucket} ==")
    listing = list_bucket(client, bucket)
    print(f"  {len(listing)} objects")

    if args.manifest_only:
        n = write_manifest(listing, manifest_path)
        print(f"wrote {n} keys to {manifest_path.name}")
        return 0

    singles = sorted(k for k in listing if is_served_single(k))
    lps = sorted(k for k in listing if is_served_lp(k))
    served = singles + lps
    odd = lookalikes(listing)
    print(f"  served singles: {len(singles)}")
    print(f"  served LP/EP:   {len(lps)}")
    print(f"  thumbs/ keys:   {sum(1 for k in listing if k.startswith(THUMB_PREFIX))}")
    if odd:
        print(f"  {len(odd)} key(s) look served but miss the served patterns (not shown on site):")
        _show("odd", odd)

    failures: list[tuple[str, str]] = []

    # ---- Cache-Control on originals (checked in a dry run; fixed with --set-cache-control)
    if not args.apply or args.set_cache_control:
        print("== Cache-Control check (HEAD served originals) ==")
        need_cc, fails = check_cache_control(client, bucket, served, args.workers)
        failures += fails
        print(f"  originals needing cache-control: {len(need_cc)}")
        if args.apply and need_cc:
            print("== Rewriting Cache-Control in place ==")
            _, fails = run_pool("cache-control", sorted(need_cc),
                                lambda k: _retry(lambda: set_cache_control(
                                    client, bucket, k, need_cc[k])), args.workers)
            failures += fails
            # in-place copies bump LastModified: plan thumbs against the new times
            print("== Re-listing bucket ==")
            listing = list_bucket(client, bucket)
            print(f"  {len(listing)} objects")

    # ---- Thumbnails
    create, refresh, orphans = plan_thumbs(listing)
    print("== Thumbnails ==")
    print(f"  thumbs to create:  {len(create)}")
    print(f"  thumbs to refresh: {len(refresh)}")
    print(f"  orphans to delete: {len(orphans)}")
    _show("orphan", orphans)
    if args.apply:
        sizes, fails = run_pool("thumbs", create + refresh,
                                lambda k: make_thumb(client, bucket, k), args.workers)
        failures += fails
        if sizes:
            avg = sum(sizes.values()) / len(sizes)
            print(f"  wrote {len(sizes)} thumbs (avg {avg / 1024:.1f} KB)")
        if orphans:
            errs = delete_thumbs(client, bucket, orphans, args.workers)
            failures += errs
            print(f"  deleted {len(orphans) - len(errs)} orphan thumbs")

    # ---- Manifest
    added, removed = manifest_diff(listing, manifest_path)
    if args.apply:
        n = write_manifest(listing, manifest_path)
        print(f"== Manifest == wrote {n} keys to {manifest_path.name} "
              f"(+{added} / -{removed}); commit it")
    else:
        print(f"== Manifest == would write {len(singles)} keys to {manifest_path.name} "
              f"(+{added} / -{removed})")

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for k, e in failures:
            print(f"  {k}: {e}", file=sys.stderr)
        return 1
    if not args.apply:
        print("\n(dry run -- pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
