"""随机指纹素材：相机型号、软件名、日期、注释串、句柄名。"""
from __future__ import annotations

import datetime as _dt
import random
import string

# 一堆真实存在的设备 / 软件指纹，随机取用，让元数据看起来自然
_CAMERAS = [
    ("Canon", "Canon EOS 5D Mark IV"), ("Canon", "Canon EOS R6"),
    ("NIKON CORPORATION", "NIKON D850"), ("NIKON CORPORATION", "NIKON Z 6_2"),
    ("SONY", "ILCE-7M3"), ("SONY", "ILCE-6400"), ("SONY", "DSC-RX100M7"),
    ("FUJIFILM", "X-T4"), ("Panasonic", "DC-GH5"), ("OLYMPUS CORPORATION", "E-M1MarkIII"),
    ("Apple", "iPhone 14 Pro"), ("Apple", "iPhone 13"), ("Apple", "iPad Air (5th generation)"),
    ("samsung", "SM-G998B"), ("samsung", "SM-A5360"), ("Xiaomi", "2201123C"),
    ("HUAWEI", "ANA-AN00"), ("vivo", "V2145A"), ("OPPO", "PEGM00"),
    ("RICOH IMAGING COMPANY, LTD.", "RICOH GR III"),
]
_SOFTWARE = [
    "Adobe Lightroom 13.2 (Windows)", "Adobe Photoshop 25.3 (Windows)",
    "Capture One 23 Pro", "Luminar Neo 1.19", "darktable 4.6.0",
    "ACDSee Ultimate 2024", "GIMP 2.10.36", "Affinity Photo 2.4",
    "Snapseed 2.21", "VSCO 3.5.1", "Instagram 302.0", "Pixelmator Pro 3.5",
    "Windows 11 Photo 2024.11050", "Paint.NET 5.0.13",
]
_SCANNERS = [("EPSON", "EPSON Perfection V600"), ("Canon", "CanoScan LiDE 400"),
             ("Brother", "MFC-L3750CDW")]
_PLAIN = [("", ""), ("", ""), ("PixelShift", "PixelShift 1.0")]

_COMMENT_WORDS = [
    "golden hour", "street snap", "morning walk", "quiet valley", "soft light",
    "rainy window", "road trip", "harbor view", "green field", "city dusk",
    "slow morning", "blue hour", "warm room", "cloud break", "old town",
    "field notes", "shore line", "hill top", "fog line", "late afternoon",
]
_HANDLERS = ["VideoHandler", "Mainconcept MP4 Video Media Handler",
             "Apple Video Media Handler", "L-SMASH Video Media Handler",
             "ISO Media file produced by Google Inc.", "Bento4 Video Handler"]
_TITLES = ["clip", "take", "export", "render", "footage", "scene", "record",
           "sequence", "sample", "cut"]


def _rng(rng: random.Random | None = None) -> random.Random:
    return rng or random.Random()


def rand_ascii(n: int, rng: random.Random | None = None) -> str:
    r = _rng(rng)
    return "".join(r.choice(string.ascii_letters + string.digits) for _ in range(n))


def rand_hex(n: int, rng: random.Random | None = None) -> str:
    r = _rng(rng)
    return "".join(r.choice("0123456789abcdef") for _ in range(n))


def rand_date(rng: random.Random | None = None) -> str:
    """EXIF 风格时间：2005-至今随机，秒级随机。"""
    r = _rng(rng)
    start = _dt.datetime(2006, 1, 1)
    end = _dt.datetime.now() - _dt.timedelta(days=1)
    span = int((end - start).total_seconds())
    return (start + _dt.timedelta(seconds=r.randint(0, span))).strftime("%Y:%m:%d %H:%M:%S")


def rand_iso_time(rng: random.Random | None = None) -> str:
    """ISO8601 UTC，给 ffmpeg 的 creation_time 用。"""
    return rand_date(rng).replace(":", "-", 2).replace(" ", "T") + "Z"


def rand_comment(rng: random.Random | None = None) -> str:
    r = _rng(rng)
    return f"{r.choice(_COMMENT_WORDS)} {r.randint(1, 9999):04d}"


def profile_device(profile: str, rng: random.Random | None = None) -> tuple[str, str, str]:
    """返回 (make, model, software)。"""
    r = _rng(rng)
    if profile == "plain":
        return ("", "", "PixelShift 1.0")
    if profile == "scanner":
        make, model = r.choice(_SCANNERS)
        return (make, model, r.choice(_SOFTWARE))
    if profile == "phone":
        pool = [c for c in _CAMERAS if c[0] in ("Apple", "samsung", "Xiaomi", "HUAWEI", "vivo", "OPPO")]
        make, model = r.choice(pool)
        return (make, model, r.choice(_SOFTWARE[-6:]))
    if profile == "camera":
        pool = [c for c in _CAMERAS if c[0] not in ("Apple", "samsung", "Xiaomi", "HUAWEI", "vivo", "OPPO")]
        make, model = r.choice(pool)
        return (make, model, r.choice(_SOFTWARE[:8]))
    # auto
    make, model = r.choice(_CAMERAS)
    return (make, model, r.choice(_SOFTWARE))


def video_metadata(rng: random.Random | None = None) -> dict:
    r = _rng(rng)
    return {
        "comment": f"{r.choice(_COMMENT_WORDS)} {r.choice(_TITLES)} {rand_hex(6, r)}",
        "title": f"{r.choice(_TITLES)}_{rand_hex(4, r)}",
        "handler_name": r.choice(_HANDLERS),
        "creation_time": rand_iso_time(r),
        "encoder": f"Lavf{r.randint(58, 62)}.{r.randint(1, 60)}.100",
    }


def image_metadata(profile: str, rng: random.Random | None = None) -> dict:
    """图片注入用的元数据字典（键名与 PIL Exif tag 的映射在 images.py）。"""
    r = _rng(rng)
    make, model, software = profile_device(profile, r)
    return {
        "make": make,
        "model": model,
        "software": software,
        "datetime": rand_date(r),
        "datetime_original": rand_date(r),
        "datetime_digitized": rand_date(r),
        "description": rand_comment(r),
        "artist": r.choice(["", "", "K. Lin", "M. Duarte", "A. Novak", "S. Ito"]),
        "copyright": r.choice(["", "", "", "© " + str(r.randint(2008, 2025))]),
        "comment": f"{r.choice(_COMMENT_WORDS)} [{rand_hex(8, r)}]",
        "user_comment": rand_hex(16, r).upper(),
    }


def random_suffix(n: int = 4, rng: random.Random | None = None) -> str:
    r = _rng(rng)
    return "".join(r.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(n))
