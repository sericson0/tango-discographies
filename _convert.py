"""JPEG -> WebP conversion. Returns (jpeg_path, webp_path) pairs for caller to delete after upload."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, UnidentifiedImageError

JPEG_EXTS = {".jpg", ".jpeg", ".JPG", ".JPEG"}


def convert_tree(root: Path, quality: int = 85) -> list[tuple[Path, Path]]:
    """Walk root, convert every JPEG to a sibling .webp. Returns list of pairs."""
    pending: list[tuple[Path, Path]] = []
    for jpeg in sorted(root.rglob("*")):
        if jpeg.suffix not in JPEG_EXTS:
            continue
        webp = jpeg.with_suffix(".webp")
        if webp.exists():
            continue
        try:
            with Image.open(jpeg) as img:
                img.save(webp, "WEBP", quality=quality)
        except (UnidentifiedImageError, OSError) as e:
            print(f"warn: failed to convert {jpeg}: {e}", file=sys.stderr)
            continue
        pending.append((jpeg, webp))
    return pending
