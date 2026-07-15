#!/usr/bin/env python3
"""Detect whether a 78rpm record image is a full-disc photo or a label-only scan.

A 10" shellac disc carries a ~3.25" paper label, so on a *full-disc* photo the
label circle radius is only ~0.30-0.40 of the disc radius and the disc itself
nearly fills the frame -- the label is a bright/coloured island surrounded by a
wide ring of dark shellac. On a *label-only* scan the label fills the frame
(radius >= ~0.42 of the min dimension) and often touches the edges.

``classify_and_detect`` finds a roughly-centred label circle from a "labelness"
map (bright OR saturated pixels), then corroborates ``full_disc`` by checking the
band just outside the candidate label is dark shellac. It is deliberately
conservative: when signals conflict it returns ``unknown`` and never crops.
"""
from __future__ import annotations

import sys

import cv2
import numpy as np
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

WORK_DIM = 800          # detection resolution (long edge)
CROP_MARGIN = 0.04      # extra margin around the label circle when cropping

# --- tuned on a 64-image ground-truth sample (DAHR full-disc; DiSarli/TangoInfo/
# --- Discogs label-only). Goal: zero false 'full_disc' on true label-only scans.
_LABEL_ONLY_RFRAC = 0.42    # label circle >= this fraction of min-dim -> fills frame
_FULL_MAX_RFRAC = 0.40      # candidate label must be smaller than this to be a disc
_FULL_MIN_RFRAC = 0.13      # ... and larger than this (smaller = logo/noise)
_DARK_V = 85                # shellac is dark: V below this counts as shellac
_RING_DARK_FRAC = 0.60      # >= this fraction of the outer band must be dark shellac
_INNER_OVER_RING = 28       # label interior must be this much brighter than the band
_MAX_CENTER_OFF = 0.16      # label centre offset from frame centre (fraction of min-dim)
_FLAT_DARK_V = 70           # whole frame this dark & low-contrast -> unknown (dark label)
_BLOWN_FRAC = 0.55          # interior this fraction blown-white -> specular highlight
_NSECTORS = 12              # angular sectors of the outer band to check
_MIN_DARK_SECTORS = 10      # shellac surrounds the label ~360 deg: this many must be dark

Verdict = str  # 'full_disc' | 'label_only' | 'unknown'
BBox = tuple[int, int, int, int]  # (left, top, right, bottom) in original pixels


def _to_bgr(img) -> np.ndarray:
    """Accept a PIL image or an RGB/BGR numpy array; return an 8-bit BGR array."""
    if isinstance(img, Image.Image):
        return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    arr = np.asarray(img)
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    return np.ascontiguousarray(arr[:, :, ::-1]) if arr.shape[2] == 3 else arr


def _downscale(bgr: np.ndarray) -> tuple[np.ndarray, float]:
    """Downscale to WORK_DIM long edge; return (work, scale) where orig = work/scale."""
    h, w = bgr.shape[:2]
    s = WORK_DIM / max(h, w)
    if s >= 1.0:
        return bgr, 1.0
    return cv2.resize(bgr, (max(1, round(w * s)), max(1, round(h * s))),
                      interpolation=cv2.INTER_AREA), s


def _central_component(mask: np.ndarray) -> np.ndarray | None:
    """Return the labelled-mask component covering (or nearest to) the frame centre."""
    n, lbl, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None
    h, w = mask.shape
    cy, cx = h / 2, w / 2
    at_center = lbl[int(cy), int(cx)]
    if at_center != 0:
        return (lbl == at_center).astype(np.uint8) * 255
    # centre pixel is dark (spindle hole / dark label centre): pick the largest
    # component whose centroid sits near the frame centre.
    best, best_area = None, 0
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        off = np.hypot(cents[i][0] - cx, cents[i][1] - cy) / min(h, w)
        if off < 0.22 and area > best_area:
            best, best_area = i, area
    return (lbl == best).astype(np.uint8) * 255 if best else None


def _ring_dark_fraction(v: np.ndarray, cx: float, cy: float,
                        r_in: float, r_out: float) -> tuple[float, float]:
    """(dark_fraction, mean_V) of the annulus [r_in, r_out] around (cx, cy)."""
    h, w = v.shape
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.hypot(xx - cx, yy - cy)
    m = (rr >= r_in) & (rr <= r_out)
    if m.sum() < 30:
        return 0.0, 255.0
    band = v[m]
    return float((band < _DARK_V).mean()), float(band.mean())


def _dark_sector_count(v: np.ndarray, cx: float, cy: float,
                       r_in: float, r_out: float) -> int:
    """How many of _NSECTORS angular wedges of the band are mostly dark shellac.

    A real disc's shellac ring is dark in every direction; a composite image
    (e.g. an LP cover with bright title text) leaves bright wedges and fails.
    """
    h, w = v.shape
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.hypot(xx - cx, yy - cy)
    ang = np.arctan2(yy - cy, xx - cx) + np.pi
    band = (rr >= r_in) & (rr <= r_out)
    dark = 0
    for s in range(_NSECTORS):
        a0 = s * 2 * np.pi / _NSECTORS
        a1 = (s + 1) * 2 * np.pi / _NSECTORS
        m = band & (ang >= a0) & (ang < a1)
        if m.sum() < 10 or (v[m] < _DARK_V).mean() >= 0.5:
            dark += 1
    return dark


def classify_and_detect(img) -> tuple[Verdict, BBox | None]:
    """Classify a record image and, for 'full_disc', return the label crop box.

    Returns ``(verdict, bbox)`` where ``verdict`` is 'full_disc', 'label_only' or
    'unknown', and ``bbox`` is ``(left, top, right, bottom)`` in original-image
    pixels (a square around the label with ~4% margin) only for 'full_disc';
    ``None`` otherwise.
    """
    bgr = _to_bgr(img)
    H, W = bgr.shape[:2]
    work, scale = _downscale(bgr)
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    V = hsv[:, :, 2].astype(np.float32)
    S = hsv[:, :, 1].astype(np.float32)
    h, w = V.shape
    mind = float(min(h, w))

    # labelness: a label pixel is bright (paper/cream) OR saturated (coloured ink);
    # shellac is dark AND desaturated, so it stays low here.
    L = np.maximum(V, S)
    Lb = cv2.GaussianBlur(L, (0, 0), max(1.0, mind * 0.012))

    # whole frame dark & low-contrast -> a dark label we can't localise; bail out.
    if float(np.median(Lb)) < _FLAT_DARK_V and float(Lb.std()) < 28:
        return "unknown", None

    otsu, _ = cv2.threshold(Lb.astype(np.uint8), 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = max(float(otsu), 55.0)
    mask = (Lb > thr).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

    comp = _central_component(mask)
    if comp is None:
        return "unknown", None
    cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return "unknown", None
    cnt = max(cnts, key=cv2.contourArea)
    (cx, cy), r = cv2.minEnclosingCircle(cnt)
    if r < 4:
        return "unknown", None
    r_frac = r / mind
    fill = cv2.contourArea(cnt) / (np.pi * r * r + 1e-6)

    # label fills the frame -> already a label-only scan.
    if r_frac >= _LABEL_ONLY_RFRAC:
        return "label_only", None

    # too small / too big / not roughly round to be a trustworthy label island.
    center_off = np.hypot(cx - w / 2, cy - h / 2) / mind
    if not (_FULL_MIN_RFRAC <= r_frac <= _FULL_MAX_RFRAC):
        return "unknown", None
    if fill < 0.62 or center_off > _MAX_CENTER_OFF:
        return "unknown", None

    # corroborate full_disc: a wide DARK shellac band must sit just outside the
    # candidate label, and the label interior must be clearly brighter than it.
    r_out = min(r * 2.2, 0.48 * mind)
    if r_out <= r * 1.15:
        return "unknown", None
    dark_frac, ring_v = _ring_dark_fraction(V, cx, cy, r * 1.15, r_out)
    disk = _disk_mask(h, w, cx, cy, r * 0.8)
    inner_v = float(V[disk].mean())
    if dark_frac < _RING_DARK_FRAC or (inner_v - ring_v) < _INNER_OVER_RING:
        return "unknown", None

    # reject a blown-out specular highlight masquerading as a label: a real label
    # carries printed detail, a reflection is a near-uniform white blob.
    blown = float(((V[disk] >= 245) & (S[disk] <= 25)).mean())
    if blown > _BLOWN_FRAC or (inner_v > 205 and float(V[disk].std()) < 12):
        return "unknown", None

    # the dark shellac band must wrap the label on (nearly) all sides.
    if _dark_sector_count(V, cx, cy, r * 1.15, r_out) < _MIN_DARK_SECTORS:
        return "unknown", None

    # square crop around the label at full resolution.
    half = r * (1.0 + CROP_MARGIN)
    left = int(round((cx - half) / scale))
    top = int(round((cy - half) / scale))
    right = int(round((cx + half) / scale))
    bottom = int(round((cy + half) / scale))
    left, top = max(0, left), max(0, top)
    right, bottom = min(W, right), min(H, bottom)
    if right - left < 8 or bottom - top < 8:
        return "unknown", None
    return "full_disc", (left, top, right, bottom)


def _disk_mask(h: int, w: int, cx: float, cy: float, r: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    return np.hypot(xx - cx, yy - cy) <= r


def crop_to_label(img: Image.Image, bbox) -> Image.Image:
    """Crop a PIL image to the given ``(left, top, right, bottom)`` box."""
    return img.crop(bbox)
