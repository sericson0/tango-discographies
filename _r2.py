"""Cloudflare R2 helpers: env config, S3 client, HEAD check, upload with retry."""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError
from dotenv import load_dotenv

UA = "Mozilla/5.0 (compatible; tango-sync/1.0)"


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    public_base: str

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def load_env() -> R2Config:
    load_dotenv()
    missing = [k for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_BASE") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing env vars: {', '.join(missing)} (see .env.example)")
    return R2Config(
        account_id=os.environ["R2_ACCOUNT_ID"],
        access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        bucket=os.environ["R2_BUCKET"],
        public_base=os.environ["R2_PUBLIC_BASE"].rstrip("/"),
    )


def make_client(cfg: R2Config):
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        config=Config(retries={"max_attempts": 1}, signature_version="s3v4"),
        region_name="auto",
    )


def key_for_local(local: Path, artist_root: Path, bandleader_folder_name: str) -> str:
    """Map images/<Artist>/<rest> -> <bandleaderFolder>/<rest>."""
    rel = local.relative_to(artist_root).as_posix()
    return f"{bandleader_folder_name}/{rel}"


def public_url(public_base: str, key: str) -> str:
    """Build a public R2 URL, percent-encoding the key path (e.g. spaces -> %20)."""
    return f"{public_base.rstrip('/')}/{urllib.parse.quote(key, safe='/')}"


# Sentinel returned by head_exists when the remote state could not be determined
# (transient 5xx / network error). Callers must treat this as "unknown" and must NOT
# re-upload or delete the local original based on it.
HEAD_UNKNOWN = None


def head_exists(url: str) -> bool | None:
    """Probe whether an object exists on R2.

    Returns True (200 -> exists), False (404 -> missing), or HEAD_UNKNOWN/None for any
    other outcome (non-404 HTTP error, timeout, connection error). The unknown sentinel
    lets callers skip-and-warn rather than treat a transient failure as 'missing' and
    needlessly re-upload (or, worse, delete) data.
    """
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return HEAD_UNKNOWN
    except Exception:
        return HEAD_UNKNOWN


def upload_file(client, bucket: str, key: str, path: Path, sleeper: Callable[[float], None] | None = None) -> None:
    import time
    if sleeper is None:
        sleeper = time.sleep
    delays = [1.0, 2.0]
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            import io
            data = path.read_bytes()
            client.put_object(Bucket=bucket, Key=key, Body=io.BytesIO(data), ContentType="image/webp")
            return
        except (EndpointConnectionError, BotoCoreError) as e:
            last_err = e
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status and 400 <= int(status) < 500 and code not in ("RequestTimeout",):
                raise  # auth/permanent error: don't retry
            last_err = e
        if attempt < 2:
            sleeper(delays[attempt])
    assert last_err is not None
    raise last_err
