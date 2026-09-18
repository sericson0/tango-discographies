"""Tests for _crop_label.classify_and_detect.

Two layers:

* synthetic images / masks, which run anywhere and pin the specific defect this
  module regressed on -- a disc photographed on a light backdrop, where the
  backdrop is a RING whose centroid is the frame centre;
* a ground-truth corpus of real images already on disk. Those paths live outside
  the repo (the sibling ``parse-tango-discographies`` harvest cache) or are large
  binaries, so every case is skipped when its file is absent and the suite still
  runs on another machine.

The corpus was labelled by eye, not by running the detector. The contract it
encodes, in order of importance:

1. a true label-only scan must NEVER come back 'full_disc' -- a wrong crop is
   worse than no crop, and these are the maintainer's delivered images;
2. a true full-disc photo must not be asserted 'label_only' (that is the bug:
   it silently delivers a 78 label occupying ~15% of the frame);
3. whenever 'full_disc' is returned the box must actually look like a label.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from _crop_label import (
    _LABEL_ONLY_RFRAC,
    _binarise,
    _central_component,
    _disk_mask,
    _downscale,
    _label_circle,
    _texture_label_radius,
    _to_bgr,
    classify_and_detect,
    crop_to_label,
)

REPO = Path(__file__).resolve().parent
HARVEST = REPO.parent / "parse-tango-discographies" / "harvest_data" / "cache"
EBAY = HARVEST / "ebay" / "juan-darienzo" / "records"
POPSIKE = HARVEST / "popsike" / "juan-darienzo" / "records"
SINGLES = REPO / "images"

# The image the maintainer flagged: an RCA Victor 78 (blue label 63-0040-A) lying
# on a white backdrop. Otsu splits backdrop-from-disc, so the label never enters
# the primary mask and only the backdrop ring survives -- which used to be read as
# a frame-filling label and returned 'label_only'.
REPRODUCER = EBAY / "126555936725__photo_01.webp"


# --------------------------------------------------------------------------
# synthetic fixtures
# --------------------------------------------------------------------------

def _disc_photo(size: int = 800, disc_r: int = 370, label_r: int = 120,
                backdrop: int = 255, label_bgr=(160, 60, 40)) -> Image.Image:
    """A 78 on a backdrop: dark shellac annulus, coloured label, printed detail."""
    rng = np.random.default_rng(7)
    img = np.full((size, size, 3), backdrop, np.uint8)
    yy, xx = np.mgrid[0:size, 0:size]
    rr = np.hypot(xx - size / 2, yy - size / 2)
    shellac = rr <= disc_r
    img[shellac] = (25 + 12 * np.sin(rr[shellac] / 3.0)).astype(np.uint8)[:, None]
    label = rr <= label_r
    img[label] = np.array(label_bgr, np.uint8)[::-1]
    # printed detail, so the label is not mistaken for a blown-out reflection
    for row in range(-label_r + 20, label_r - 20, 14):
        band = label & (np.abs(yy - size / 2 - row) < 3) & (np.abs(xx - size / 2) < label_r * 0.6)
        img[band] = 235
    img[label] = np.clip(img[label] + rng.integers(-6, 6, img[label].shape), 0, 255)
    return Image.fromarray(img)


def _label_scan(size: int = 600) -> Image.Image:
    """A label-only scan: the label fills the frame and runs off every edge.

    Printing is drawn as short dashes rather than full-width bars so the paper
    stays one connected region, as it is on a real scan.
    """
    rng = np.random.default_rng(11)
    img = np.full((size, size, 3), 200, np.uint8)
    img[:, :, 0] = 90  # coloured paper
    yy, xx = np.mgrid[0:size, 0:size]
    for row in range(70, size - 70, 40):
        for col in range(90, size - 90, 60):
            img[(np.abs(yy - row) < 4) & (np.abs(xx - col) < 18)] = 30
    # spindle hole punched through the centre. Kept to ~0.025 of the frame: the
    # detector blurs by 0.012 of min-dim before thresholding, so a hole drawn much
    # larger than a real one swallows the whole centre probe.
    hole = np.hypot(xx - size / 2, yy - size / 2) <= size * 0.025
    img[hole] = 20
    return Image.fromarray(np.clip(img + rng.integers(-5, 5, img.shape), 0, 255).astype(np.uint8))


# --------------------------------------------------------------------------
# unit tests: centre ownership
# --------------------------------------------------------------------------

def test_annulus_is_not_the_central_component():
    """A ring's centroid is dead-centre, but it owns none of the centre.

    This is the whole bug: the bright backdrop around a disc is a ring, and the
    old 'largest component whose centroid is near the centre' fallback handed it
    back as the label. Its enclosing circle spans the frame -> 'label_only'.
    """
    size = 400
    yy, xx = np.mgrid[0:size, 0:size]
    rr = np.hypot(xx - size / 2, yy - size / 2)
    ring = (((rr > 150) & (rr < 190)).astype(np.uint8)) * 255
    assert _central_component(ring) is None


def test_spindle_hole_still_yields_the_label():
    """The reason the centre pixel may be off: a dark hole punched in the label."""
    size = 400
    yy, xx = np.mgrid[0:size, 0:size]
    rr = np.hypot(xx - size / 2, yy - size / 2)
    disk = ((rr <= 150) & (rr >= size * 0.045)).astype(np.uint8) * 255
    comp = _central_component(disk)
    assert comp is not None
    assert comp.sum() > 0


def test_reject_border_drops_frame_spanning_components():
    size = 300
    full = np.full((size, size), 255, np.uint8)
    assert _central_component(full, reject_border=False) is not None
    assert _central_component(full, reject_border=True) is None


def test_binarise_returns_a_clean_binary_mask():
    m = np.zeros((200, 200), np.float32)
    m[60:140, 60:140] = 240
    mask = _binarise(m, 55.0)
    assert set(np.unique(mask)).issubset({0, 255})
    assert mask[100, 100] == 255


def test_disk_mask_is_centred():
    d = _disk_mask(100, 100, 50, 50, 10)
    assert d[50, 50] and not d[50, 90]


# --------------------------------------------------------------------------
# synthetic end-to-end
# --------------------------------------------------------------------------

def test_disc_on_white_backdrop_is_not_label_only():
    verdict, bbox = classify_and_detect(_disc_photo(backdrop=255))
    assert verdict != "label_only"
    if verdict == "full_disc":
        assert bbox is not None
        w = bbox[2] - bbox[0]
        assert 0.20 * 800 < w < 0.45 * 800


def test_disc_on_dark_backdrop_still_works():
    verdict, _ = classify_and_detect(_disc_photo(backdrop=15))
    assert verdict != "label_only"


def test_label_scan_stays_label_only():
    verdict, bbox = classify_and_detect(_label_scan())
    assert verdict == "label_only"
    assert bbox is None


def test_flat_dark_frame_is_unknown():
    img = Image.fromarray(np.full((400, 400, 3), 30, np.uint8))
    assert classify_and_detect(img) == ("unknown", None)


def test_crop_to_label_crops():
    img = Image.new("RGB", (100, 80))
    assert crop_to_label(img, (10, 20, 60, 70)).size == (50, 50)


def test_accepts_numpy_and_grayscale():
    arr = np.asarray(_label_scan().convert("RGB"))
    assert classify_and_detect(arr)[0] in {"full_disc", "label_only", "unknown"}
    assert classify_and_detect(arr[:, :, 0])[0] in {"full_disc", "label_only", "unknown"}


# --------------------------------------------------------------------------
# ground truth: real images on disk
# --------------------------------------------------------------------------

# True label-only scans. popsike listings are pre-cropped label photographs; the
# ebay entries are the seller's own label close-ups; the images/ entries are the
# maintainer's delivered singles. None of these may ever be cropped.
LABEL_ONLY: list[Path] = [POPSIKE / f"{n}.webp" for n in (
    "120758538311__photo_01", "120812747184__photo_01", "121788658780__photo_01",
    "122219155088__photo_01", "122270547274__photo_01", "122322134090__photo_01",
    "122366944730__photo_01", "122746438667__photo_01", "123721218958__photo_01",
    "124267566592__photo_01", "161958712982__photo_01", "193264152201__photo_01",
    "193264152201__photo_03", "193264229479__photo_01", "193264229479__photo_03",
    "224719972964__photo_01", "224719972964__photo_03", "270838753949__photo_01",
    "271741558636__photo_01", "271741560311__photo_01", "271824963390__photo_01",
    "272361546899__photo_01", "272541461288__photo_01", "274608879439__photo_03",
    "281498312948__photo_01", "301420714767__photo_01", "301809306788__photo_01",
    "311133484361__photo_01", "311319204516__photo_01", "311478893248__photo_01",
    "311517805761__photo_01", "311736622121__photo_01", "311765095988__photo_01",
    "311932025577__photo_01", "312228834967__photo_01", "312231840296__photo_01",
    "312298784176__photo_01", "312633401263__photo_01", "313010343472__photo_01",
    "313069256415__photo_01", "331111723896__photo_01", "371236260635__photo_01",
    "371854238147__photo_01", "371854246766__photo_01", "371854253534__photo_01",
    "371854257485__photo_01",
)] + [EBAY / f"{n}.webp" for n in (
    "125999393010__photo_02", "126795557912__photo_02", "126795557912__photo_04",
    "126832543964__photo_02", "126832543964__photo_04",
)] + [SINGLES / p for p in (
    "DArienzo/Singles/1925-1929/1928_Chorra_D-Arienzo.webp",
    "DArienzo/Singles/1935-1939/1937-09-22_Milonga-Vieja-Milonga_D-Arienzo.webp",
    "DArienzo/Singles/1935-1939/1939-09-27_De-Antano_D-Arienzo.webp",
    "DArienzo/Singles/1940-1944/1941-12-15_El-Calabozo_D-Arienzo.webp",
    "DArienzo/Singles/1940-1944/1944-09-21_El-Romantico_D-Arienzo.webp",
    "DArienzo/Singles/1945-1949/1947-08-08_Carton-Junao_D-Arienzo.webp",
    "DArienzo/Singles/1950-1954/1950-09-28_Un-Tango-Para-Mi-Vieja_D-Arienzo.webp",
    "DArienzo/Singles/1950-1954/1954-09-01_Sentimiento-De-Calavera_D-Arienzo.webp",
    "DiSarli/Singles/1925-1929/1928-11-26_La-Guitarrita_Di-Sarli.webp",
    "DiSarli/Singles/1930-1934/1930-09-03_Chau-Pinela_Di-Sarli.webp",
    "DiSarli/Singles/1940-1944/1941-03-06_La-Cachila_Di-Sarli.webp",
    "DiSarli/Singles/1940-1944/1942-12-21_Estampa-Federal_Di-Sarli.webp",
    "DiSarli/Singles/1940-1944/1944-07-20_Motivo-Sentimental_Di-Sarli.webp",
    "DiSarli/Singles/1945-1949/1946-12-05_La-Vida-Me-Engano_Di-Sarli.webp",
    "DiSarli/Singles/1950-1954/1952-12-12_Marianito_Di-Sarli.webp",
    "DiSarli/Singles/1950-1954/1954-12-07_No-Mataras_Di-Sarli.webp",
    # a label scan with an unusually wide shellac margin -- geometrically it looks
    # like a small central label ringed by dark shellac, i.e. the closest a true
    # label-only scan gets to a full-disc photo. Keeps the retry path honest.
    "DiSarli/Singles/DAHR/1929-10-28_Che-Bacana_Di-Sarli.webp",
)]

# True full-disc photographs: the whole 10" disc (or nearly) is in frame and the
# label is a small central island. These should crop; 'unknown' is an acceptable
# miss, 'label_only' is the bug.
FULL_DISC: list[Path] = [EBAY / f"{n}.webp" for n in (
    "125999392070__photo_01", "125999393010__photo_01", "126555936721__photo_01",
    "126555936721__photo_02", "126555936724__photo_01", "126555936724__photo_02",
    "126555936725__photo_01", "126555936725__photo_02", "126555936726__photo_01",
    "126555936726__photo_02", "126555936729__photo_01", "126555936729__photo_02",
    "126795557888__photo_01", "126795557888__photo_02", "126795557912__photo_01",
    "126832543588__photo_01", "126832543588__photo_02", "126832543964__photo_03",
    "127969848967__photo_01", "127969848967__photo_02",
)] + [POPSIKE / f"{n}.webp" for n in (
    "193264152201__photo_02", "193264152201__photo_04", "193264229479__photo_02",
    "193264229479__photo_04", "274608879439__photo_02", "274608879439__photo_04",
    "274608879439__photo_05", "313010343472__photo_02", "313010343472__photo_03",
    "313010343472__photo_04", "314684812522__photo_01",
)]


# FIXED 2026-08-05 by the texture-collapse path. This disc runs off the top and
# left edges and sits on a bright sleeve, so every COLOUR-based map read the
# label as frame-filling and returned label_only. Printed detail does not care
# how much of the frame the label spans -- the profile still collapses onto the
# shellac -- so the label is now found and cropped. The corpus therefore has no
# known full-disc miss left; the set is kept (empty) so a future regression can
# be recorded here rather than by deleting the ground truth.
_KNOWN_FULL_DISC_MISS: set[Path] = set()


def _id(p: Path) -> str:
    return f"{p.parent.name}/{p.name}"


def _needs(p: Path):
    return pytest.mark.skipif(not p.exists(), reason=f"ground-truth image absent: {p}")


def _full_disc_marks(p: Path):
    marks = [_needs(p)]
    if p in _KNOWN_FULL_DISC_MISS:
        marks.append(pytest.mark.xfail(reason="disc overflows the frame; reads as label_only",
                                       strict=True))
    return marks


def _assert_plausible_label_box(path: Path, bbox) -> None:
    """A returned crop must look like a label: centred, and a sane share of frame."""
    with Image.open(path) as im:
        w, h = im.size
    left, top, right, bottom = bbox
    assert 0 <= left < right <= w and 0 <= top < bottom <= h
    side = min(right - left, bottom - top)
    assert 0.08 * min(w, h) <= side <= 0.75 * min(w, h), f"implausible crop size {bbox}"
    off = np.hypot((left + right) / 2 - w / 2, (top + bottom) / 2 - h / 2) / min(w, h)
    assert off <= 0.20, f"crop is not centred on the label: {bbox}"


@pytest.mark.parametrize("path", [pytest.param(p, id=_id(p), marks=_needs(p))
                                  for p in LABEL_ONLY])
def test_label_only_is_never_cropped(path: Path):
    verdict, bbox = classify_and_detect(Image.open(path))
    assert verdict != "full_disc", f"false full_disc on a label-only scan: {path}"
    assert bbox is None


@pytest.mark.parametrize("path", [pytest.param(p, id=_id(p), marks=_full_disc_marks(p))
                                  for p in FULL_DISC])
def test_full_disc_is_never_called_label_only(path: Path):
    verdict, bbox = classify_and_detect(Image.open(path))
    assert verdict in {"full_disc", "unknown"}, (
        f"full-disc photo reported as {verdict}; it would be delivered uncropped: {path}")
    if verdict == "full_disc":
        _assert_plausible_label_box(path, bbox)


# One popsike full-disc photo (274608879439__photo_04) has the disc running off
# the top and bottom edges, so the label really does span a large share of the
# frame and still reads as label_only. Tolerated, but capped.
_MAX_FULL_DISC_CALLED_LABEL_ONLY = 1


@pytest.mark.skipif(not EBAY.exists() or not POPSIKE.exists(),
                    reason="ground-truth corpus absent")
def test_full_disc_detection_rate():
    """Accuracy floor, so the fix cannot silently decay into 'always unknown'."""
    present = [p for p in FULL_DISC if p.exists()]
    if len(present) < 20:
        pytest.skip("too little of the corpus present to judge the rate")
    verdicts = [classify_and_detect(Image.open(p))[0] for p in present]
    detected = verdicts.count("full_disc")
    # 16/31 at the time of the fix (up from 11/31); the rest are held back by the
    # deliberately conservative corroboration guards and come back 'unknown'.
    assert detected >= 15, f"full-disc detection regressed: {detected}/{len(present)}"
    assert verdicts.count("label_only") <= _MAX_FULL_DISC_CALLED_LABEL_ONLY


@pytest.mark.skipif(not REPRODUCER.exists(), reason="reproducer image absent")
def test_reproducer_crops_to_the_blue_label():
    """The flagged RCA Victor 78 on a white backdrop: was 'label_only', no crop."""
    verdict, bbox = classify_and_detect(Image.open(REPRODUCER))
    assert verdict == "full_disc"
    _assert_plausible_label_box(REPRODUCER, bbox)
    # the label is roughly a third of this 1600px frame, centred
    side = bbox[2] - bbox[0]
    assert 0.22 * 1600 <= side <= 0.42 * 1600, f"crop {bbox} is not label-sized"


# --------------------------------------------------------------------------
# texture-collapse path: the dark-label case no colour map can see
# --------------------------------------------------------------------------

def _dark_label_disc(size: int = 800, disc_r: int = 370, label_r: int = 120
                     ) -> Image.Image:
    """A disc whose label is as dark and desaturated as the shellac.

    This is the Odeon brown / RCA near-black case. ``max(V, S)`` cannot separate
    label from shellac here by construction -- the only difference is that the
    label carries print and the shellac does not.
    """
    rng = np.random.default_rng(3)
    img = np.full((size, size, 3), 240, np.uint8)          # light backdrop
    yy, xx = np.mgrid[0:size, 0:size]
    rr = np.hypot(xx - size / 2, yy - size / 2)
    shellac = rr <= disc_r
    img[shellac] = 28
    label = rr <= label_r
    img[label] = 34                                        # ~same V, ~same S
    for row in range(-label_r + 18, label_r - 18, 12):
        band = (label & (np.abs(yy - size / 2 - row) < 3)
                & (np.abs(xx - size / 2) < label_r * 0.55))
        img[band] = 200                                    # light print
    img[shellac] = np.clip(
        img[shellac] + rng.integers(-3, 3, img[shellac].shape), 0, 255)
    return Image.fromarray(img)


def test_dark_label_disc_is_cropped():
    """The defect this path exists for: label and shellac share a colour, so
    every colour map returns nothing and the disc was delivered uncropped."""
    verdict, bbox = classify_and_detect(_dark_label_disc())
    assert verdict == "full_disc", "dark label on dark shellac must still crop"
    left, top, right, bottom = bbox
    # the box must bracket the 120px label, not the 370px disc
    assert 230 <= (right - left) <= 330
    assert abs((left + right) / 2 - 400) < 40 and abs((top + bottom) / 2 - 400) < 40


def test_dark_label_is_invisible_to_the_colour_maps():
    """Pins WHY the texture path is needed: if this ever starts finding a
    central component, the premise above has changed and the tuning should be
    revisited rather than silently relied on."""
    import cv2
    work, _ = _downscale(_to_bgr(_dark_label_disc()))
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    V = hsv[:, :, 2].astype(np.float32)
    S = hsv[:, :, 1].astype(np.float32)
    mind = float(min(V.shape))
    Lb = cv2.GaussianBlur(np.maximum(V, S), (0, 0), max(1.0, mind * 0.012))
    comp = _central_component(_binarise(Lb, 55.0))
    circle = _label_circle(comp) if comp is not None else None
    assert circle is None or circle[2] / mind > _LABEL_ONLY_RFRAC, (
        "colour map now localises a dark label; texture path assumptions changed")


def test_texture_radius_finds_the_label_edge():
    work, _ = _downscale(_to_bgr(_dark_label_disc(label_r=120)))
    r = _texture_label_radius(work)
    assert r is not None
    assert 0.13 <= r <= 0.20, f"label radius should be ~120/800=0.15, got {r}"


def test_texture_path_ignores_a_label_only_scan():
    """Print covers the whole frame, so the profile never collapses."""
    work, _ = _downscale(_to_bgr(_label_scan()))
    assert _texture_label_radius(work) is None


def test_texture_path_rejects_a_wide_margin_close_up():
    """The ring-width guard: a label already spanning ~0.28 of the frame has no
    room for a disc's ring, and re-cropping a usable scan is the risky trade."""
    # label_r/size = 240/800 = 0.30, ring only reaches the frame edge
    work, _ = _downscale(_to_bgr(_dark_label_disc(label_r=240, disc_r=395)))
    assert _texture_label_radius(work) is None


def test_texture_path_ignores_a_blank_frame():
    """No print anywhere -> peak below _TEX_MIN_PEAK -> no verdict, rather than
    a ratio test applied to sensor noise."""
    flat = Image.fromarray(np.full((600, 600, 3), 40, np.uint8))
    work, _ = _downscale(_to_bgr(flat))
    assert _texture_label_radius(work) is None


def test_cropping_converges_and_never_eats_the_label():
    """Re-running the tool must reach a fixed point, and fast.

    Not the same as "one crop is always final": one corpus disc is framed
    loosely on the first pass because a glare band inflates the candidate
    circle, and a second pass legitimately tightens onto the label. That is
    convergence, not damage. What must never happen is an image that keeps
    qualifying as a full disc, because each pass re-encodes and clips a little
    more print off a label that was already framed correctly -- the failure
    _MIN_RING_SPAN exists to stop.

    So: bound the iterations, and require the final crop to still be a
    plausible label rather than a shard of one.
    """
    present = [p for p in FULL_DISC if p.exists()]
    if len(present) < 20:
        pytest.skip("too little of the corpus present to judge")
    MAX_PASSES = 3
    for path in present:
        img = Image.open(path).convert("RGB")
        start = min(img.size)
        for _ in range(MAX_PASSES):
            verdict, bbox = classify_and_detect(img)
            if verdict != "full_disc" or bbox is None:
                break
            img = crop_to_label(img, bbox)
        else:
            pytest.fail(f"still cropping after {MAX_PASSES} passes: {path}")
        # a runaway would shrink towards nothing; a label is a large fraction
        # of the disc it was cut from
        assert min(img.size) > start * 0.10, f"crop collapsed on {path}"


def test_narrow_ring_is_not_a_full_disc():
    """A label spanning most of the frame leaves no room for a shellac ring;
    the only 'dark band' left is the image corners."""
    size = 400
    yy, xx = np.mgrid[0:size, 0:size]
    rr = np.hypot(xx - size / 2, yy - size / 2)
    img = np.full((size, size, 3), 20, np.uint8)      # dark corners
    label = rr <= size * 0.44                          # label fills the frame
    img[label] = (200, 190, 180)
    for row in range(-140, 140, 12):
        band = label & (np.abs(yy - size / 2 - row) < 3) & (np.abs(xx - size / 2) < 110)
        img[band] = 30
    assert classify_and_detect(Image.fromarray(img))[0] != "full_disc"
