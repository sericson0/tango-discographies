"""Raster -> WebP conversion. Returns (src_path, webp_path) pairs for caller to delete after upload."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, UnidentifiedImageError

JPEG_EXTS = {".jpg", ".jpeg", ".JPG", ".JPEG"}
PNG_EXTS = {".png", ".PNG"}
RASTER_EXTS = JPEG_EXTS | PNG_EXTS


def convert_tree(root: Path, quality: int = 85, lossless: bool = False) -> list[tuple[Path, Path]]:
    """Walk root, convert every JPEG/PNG to a sibling .webp (lossless when requested). Returns list of pairs."""
    pending: list[tuple[Path, Path]] = []
    for src in sorted(root.rglob("*")):
        if src.suffix not in RASTER_EXTS:
            continue
        webp = src.with_suffix(".webp")
        if webp.exists():
            continue
        try:
            with Image.open(src) as img:
                # PNGs may carry alpha (RGBA/LA/paletted-with-transparency). WebP supports
                # alpha, so keep it: normalize to RGBA when transparency is present, otherwise
                # RGB. Lossless preserves it exactly; lossy keeps a high-quality alpha channel.
                has_alpha = img.mode in ("RGBA", "LA", "PA") or (
                    img.mode == "P" and "transparency" in img.info
                )
                if has_alpha:
                    img = img.convert("RGBA")
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                if lossless:
                    img.save(webp, "WEBP", lossless=True)
                else:
                    img.save(webp, "WEBP", quality=quality)
        except (UnidentifiedImageError, OSError) as e:
            print(f"warn: failed to convert {src}: {e}", file=sys.stderr)
            continue
        pending.append((src, webp))
    return pending
