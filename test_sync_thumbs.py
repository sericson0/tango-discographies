"""sync_thumbs: served-key classification, thumb/orphan planning, the
thumbs/-only deletion guard, manifest format, rendering and Cache-Control."""
from __future__ import annotations

import io
import threading
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

import _r2
import sync_thumbs as st

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)

SINGLE = "TroiloAnibal/Singles/1940-1944/1942-09-01_Lejos-De-Buenos-Aires_Troilo.webp"
SINGLE2 = "CanaroFrancisco/Singles/1925-1929/1927-01-01_A-Media-Luz_Canaro.webp"
LP = "TroiloAnibal/LPs/Troilo 1941/cover.webp"
EP = "TroiloAnibal/EPs/Some EP/back.webp"


def _webp(size=(800, 600), mode="RGB", color=(200, 30, 30)) -> bytes:
    if mode == "RGBA":
        img = Image.new("RGBA", size, color + (0,))
        img.putpixel((0, 0), (1, 2, 3, 255))
    else:
        img = Image.new(mode, size, color if mode == "RGB" else 128)
    out = io.BytesIO()
    img.save(out, format="WEBP", lossless=(mode == "RGBA"))
    return out.getvalue()


class _Body:
    def __init__(self, data: bytes):
        self._d = data

    def read(self) -> bytes:
        return self._d


class FakeS3:
    """In-memory stand-in for the boto3 S3 client calls sync_thumbs makes."""

    def __init__(self, objects: dict[str, tuple[bytes, datetime]] | None = None, page=2):
        self.objs: dict[str, dict] = {}
        for k, (data, lm) in (objects or {}).items():
            self.objs[k] = {"Body": data, "LastModified": lm, "CacheControl": None,
                            "ContentType": "image/webp", "Metadata": {}}
        self.page = page
        self.clock = T0 + timedelta(days=30)
        self.lock = threading.Lock()
        self.calls: list[tuple[str, str]] = []

    def _tick(self):
        self.clock += timedelta(seconds=1)
        return self.clock

    def list_objects_v2(self, Bucket, ContinuationToken=None):
        keys = sorted(self.objs)
        start = int(ContinuationToken or 0)
        chunk = keys[start:start + self.page]
        resp = {"Contents": [{"Key": k, "LastModified": self.objs[k]["LastModified"]}
                             for k in chunk]}
        if start + self.page < len(keys):
            resp["IsTruncated"] = True
            resp["NextContinuationToken"] = str(start + self.page)
        return resp

    def get_object(self, Bucket, Key):
        return {"Body": _Body(self.objs[Key]["Body"])}

    def head_object(self, Bucket, Key):
        o = self.objs[Key]
        out = {"ContentType": o["ContentType"], "Metadata": o["Metadata"]}
        if o["CacheControl"]:
            out["CacheControl"] = o["CacheControl"]
        return out

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl=None):
        with self.lock:
            self.calls.append(("put", Key))
            self.objs[Key] = {"Body": Body, "LastModified": self._tick(),
                              "CacheControl": CacheControl, "ContentType": ContentType,
                              "Metadata": {}}

    def copy_object(self, Bucket, Key, CopySource, MetadataDirective, Metadata,
                    ContentType, CacheControl):
        assert CopySource == {"Bucket": Bucket, "Key": Key}
        assert MetadataDirective == "REPLACE"
        with self.lock:
            self.calls.append(("copy", Key))
            o = self.objs[Key]
            o.update(LastModified=self._tick(), CacheControl=CacheControl,
                     ContentType=ContentType, Metadata=Metadata)

    def delete_object(self, Bucket, Key):
        with self.lock:
            self.calls.append(("delete", Key))
            self.objs.pop(Key, None)


# --- classification ------------------------------------------------------------

@pytest.mark.parametrize("key", [SINGLE, SINGLE2, "X/Singles/2000-2004/a b.webp"])
def test_served_singles(key):
    assert st.is_served_single(key) and st.is_served(key) and not st.is_served_lp(key)


@pytest.mark.parametrize("key", [LP, EP, "X/LPs/a.webp", "X/EPs/deep/er/a.webp"])
def test_served_lp_ep(key):
    assert st.is_served_lp(key) and st.is_served(key) and not st.is_served_single(key)


@pytest.mark.parametrize("key", [
    "TroiloAnibal/Singles/DAHR/1942-09-01_X_Troilo.webp",         # staging
    "TroiloAnibal/Singles/tango_info/1942-09-01_X_Troilo.webp",
    "TroiloAnibal/Singles/43-46 Pre Yumba/1946_X.webp",           # old Grouping
    "TroiloAnibal/Singles/1940-1944/sub/1942_X.webp",             # nested
    "TroiloAnibal/Singles/1940-1944/1942_X.jpg",
    "TroiloAnibal/Singles/1940-1944/1942_X.WEBP",
    "TroiloAnibal/Singles/1940-1944/_suspect/1942_X.webp",
    "Singles/1940-1944/1942_X.webp",                              # no folder
    "TroiloAnibal/LPs/cover.jpg",
    "thumbs/" + SINGLE, "thumbs/" + LP,
    "TroiloAnibal/Singles/194O-1944/x.webp",
])
def test_not_served(key):
    assert not st.is_served(key)


def test_lookalikes_flags_near_misses_only():
    keys = [SINGLE, LP, "A/Singles/1940-1944/x.jpg", "A/LPs/b.png",
            "A/Singles/DAHR/x.webp", "thumbs/A/Singles/1940-1944/x.jpg"]
    assert st.lookalikes(keys) == ["A/LPs/b.png", "A/Singles/1940-1944/x.jpg"]


# --- planning ------------------------------------------------------------------------

def test_plan_missing_stale_fresh_orphan():
    old, new = T0, T0 + timedelta(hours=1)
    listing = {
        SINGLE: new, "thumbs/" + SINGLE: old,          # stale -> refresh
        SINGLE2: old, "thumbs/" + SINGLE2: new,        # fresh
        LP: old,                                       # missing -> create
        EP: old, "thumbs/" + EP: old,                  # same time -> fresh
        "thumbs/Gone/Singles/1940-1944/x.webp": old,   # original deleted -> orphan
        "A/Singles/DAHR/y.webp": old,
        "thumbs/A/Singles/DAHR/y.webp": old,           # original not served -> orphan
        "A/Singles/DAHR/z.webp": old,                  # unserved, no thumb: ignored
    }
    create, refresh, orphans = st.plan_thumbs(listing)
    assert create == [LP]
    assert refresh == [SINGLE]
    assert orphans == ["thumbs/A/Singles/DAHR/y.webp", "thumbs/Gone/Singles/1940-1944/x.webp"]


def test_list_bucket_paginates():
    objs = {f"A/Singles/1940-1944/{i}.webp": (b"", T0) for i in range(7)}
    assert st.list_bucket(FakeS3(objs, page=3), "b").keys() == objs.keys()


# --- deletion guard -----------------------------------------------------------------------

@pytest.mark.parametrize("bad", [SINGLE, LP, "thumbs", "thumbs/", "Thumbs/x.webp",
                                 "x/thumbs/y.webp", ""])
def test_delete_guard_refuses_non_thumb_keys(bad):
    s3 = FakeS3({SINGLE: (b"x", T0), LP: (b"x", T0)})
    with pytest.raises(ValueError):
        st.delete_thumb(s3, "b", bad)
    assert s3.calls == []


def test_delete_thumbs_checks_every_key_before_any_request():
    s3 = FakeS3({"thumbs/" + SINGLE: (b"x", T0), SINGLE: (b"x", T0)})
    with pytest.raises(ValueError):
        st.delete_thumbs(s3, "b", ["thumbs/" + SINGLE, SINGLE])
    assert s3.calls == [] and SINGLE in s3.objs and "thumbs/" + SINGLE in s3.objs


def test_put_thumb_refuses_non_thumb_key():
    s3 = FakeS3()
    with pytest.raises(ValueError):
        st.put_thumb(s3, "b", SINGLE, b"x")
    assert s3.calls == []


def test_set_cache_control_refuses_unserved_key():
    s3 = FakeS3({"A/Singles/DAHR/y.webp": (b"x", T0)})
    with pytest.raises(ValueError):
        st.set_cache_control(s3, "b", "A/Singles/DAHR/y.webp")
    assert s3.calls == []


# --- manifest ---------------------------------------------------------------------------

def test_manifest_format(tmp_path):
    listing = {SINGLE2: T0, LP: T0, SINGLE: T0, "thumbs/" + SINGLE: T0,
               "A/Singles/DAHR/y.webp": T0, "Ñandú/Singles/1950-1954/Ñ.webp": T0}
    path = tmp_path / "singles_manifest.txt"
    assert st.write_manifest(listing, path) == 3
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    lines = raw.decode("utf-8").split("\n")[:-1]
    assert lines == sorted([SINGLE, SINGLE2, "Ñandú/Singles/1950-1954/Ñ.webp"])


def test_manifest_refuses_empty(tmp_path):
    path = tmp_path / "m.txt"
    path.write_text("keep\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        st.write_manifest({LP: T0}, path)
    assert path.read_text(encoding="utf-8") == "keep\n"


def test_manifest_diff(tmp_path):
    path = tmp_path / "m.txt"
    path.write_text(SINGLE + "\nGone/Singles/1940-1944/x.webp\n", encoding="utf-8")
    assert st.manifest_diff({SINGLE: T0, SINGLE2: T0}, path) == (1, 1)


# --- rendering ---------------------------------------------------------------------------

def _open(b: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(b))
    assert img.format == "WEBP"
    return img


def test_render_downscales_long_edge():
    img = _open(st.render_thumb(_webp((800, 600))))
    assert img.size == (240, 180) and img.mode == "RGB"
    img = _open(st.render_thumb(_webp((300, 1200))))
    assert img.size == (60, 240)


def test_render_never_upscales():
    assert _open(st.render_thumb(_webp((120, 90)))).size == (120, 90)


def test_render_keeps_alpha():
    img = _open(st.render_thumb(_webp((500, 500), mode="RGBA")))
    assert img.mode == "RGBA" and img.size == (240, 240)


def test_render_greyscale_becomes_rgb():
    assert _open(st.render_thumb(_webp((400, 400), mode="L"))).mode == "RGB"


# --- end-to-end with the fake client ---------------------------------------------------------

def _bucket():
    return FakeS3({
        SINGLE: (_webp(), T0 + timedelta(hours=1)),
        "thumbs/" + SINGLE: (b"old", T0),                       # stale
        SINGLE2: (_webp(), T0),
        LP: (_webp((1000, 1000)), T0),
        "A/Singles/DAHR/y.webp": (_webp(), T0),
        "thumbs/A/Singles/DAHR/y.webp": (b"x", T0),             # orphan
    })


def test_dry_run_writes_nothing(tmp_path):
    s3 = _bucket()
    path = tmp_path / "m.txt"
    assert st.main([], client=s3, bucket="b", manifest_path=path) == 0
    assert s3.calls == [] and not path.exists()


def test_apply_syncs_thumbs_orphans_and_manifest(tmp_path):
    s3 = _bucket()
    path = tmp_path / "m.txt"
    assert st.main(["--apply", "--workers", "3"], client=s3, bucket="b",
                   manifest_path=path) == 0
    for k in (SINGLE, SINGLE2, LP):
        t = s3.objs["thumbs/" + k]
        assert t["CacheControl"] == _r2.CACHE_CONTROL and t["ContentType"] == "image/webp"
        assert max(_open(t["Body"]).size) == 240
    assert "thumbs/A/Singles/DAHR/y.webp" not in s3.objs
    assert "A/Singles/DAHR/y.webp" in s3.objs                   # unserved original untouched
    assert path.read_text(encoding="utf-8") == "\n".join(sorted([SINGLE, SINGLE2])) + "\n"
    # every write stayed inside thumbs/ (no --set-cache-control)
    assert all(k.startswith("thumbs/") for op, k in s3.calls)
    # a second run is a no-op
    s3.calls.clear()
    assert st.main(["--apply"], client=s3, bucket="b", manifest_path=path) == 0
    assert s3.calls == []


def test_one_failure_does_not_abort(tmp_path):
    s3 = _bucket()
    s3.objs[SINGLE2]["Body"] = b"not an image"
    path = tmp_path / "m.txt"
    assert st.main(["--apply"], client=s3, bucket="b", manifest_path=path) == 1
    assert "thumbs/" + LP in s3.objs and "thumbs/" + SINGLE2 not in s3.objs
    assert path.exists()


def test_manifest_only_makes_no_r2_writes(tmp_path):
    s3 = _bucket()
    path = tmp_path / "m.txt"
    assert st.main(["--manifest-only"], client=s3, bucket="b", manifest_path=path) == 0
    assert s3.calls == [] and path.read_text(encoding="utf-8").count("\n") == 2


# --- cache-control ----------------------------------------------------------------------------

def test_cache_control_planning():
    s3 = _bucket()
    s3.objs[SINGLE2]["CacheControl"] = _r2.CACHE_CONTROL
    s3.objs[LP]["Metadata"] = {"src": "x"}
    need, fails = st.check_cache_control(s3, "b", [SINGLE, SINGLE2, LP], workers=2)
    assert fails == [] and need == {SINGLE: {}, LP: {"src": "x"}}


def test_set_cache_control_then_thumbs_are_newer(tmp_path):
    s3 = _bucket()
    thumb_before = s3.objs["thumbs/" + SINGLE]
    s3.objs["thumbs/" + SINGLE2] = {"Body": b"t", "LastModified": T0 + timedelta(days=2),
                                    "CacheControl": None, "ContentType": "image/webp",
                                    "Metadata": {}}
    s3.objs[LP]["Metadata"] = {"src": "x"}
    data_before = {k: s3.objs[k]["Body"] for k in (SINGLE, SINGLE2, LP)}
    path = tmp_path / "m.txt"
    assert st.main(["--apply", "--set-cache-control"], client=s3, bucket="b",
                   manifest_path=path) == 0
    copies = sorted(k for op, k in s3.calls if op == "copy")
    assert copies == sorted([SINGLE, SINGLE2, LP])
    for k in (SINGLE, SINGLE2, LP):
        o = s3.objs[k]
        assert o["CacheControl"] == _r2.CACHE_CONTROL and o["Body"] == data_before[k]
        # thumbs are compared against the post-copy timestamps
        assert s3.objs["thumbs/" + k]["LastModified"] >= o["LastModified"]
    assert s3.objs[LP]["Metadata"] == {"src": "x"}
    assert s3.objs["thumbs/" + SINGLE] is not thumb_before
    # the unserved original was never rewritten
    assert ("copy", "A/Singles/DAHR/y.webp") not in s3.calls


def test_manifest_only_rejects_set_cache_control(tmp_path):
    assert st.main(["--manifest-only", "--set-cache-control"], client=_bucket(),
                   bucket="b", manifest_path=tmp_path / "m.txt") == 2
