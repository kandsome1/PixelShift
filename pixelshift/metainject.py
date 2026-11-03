""""懒人模式"：只注入元数据，不动一个像素、不重新编码。

和 images.py 的区别：
    images.process_image   → 解码 → 改像素 → 重编码（体积会变，耗 CPU）
    metainject.process     → 只在容器里插入/替换元数据块，压缩数据原样拷贝
                             （体积变化 ±几百字节，毫秒级）

各格式的做法（全部是格式内的合法结构，不是尾部裸追加）：
    JPEG  SOI 之后插入 APP1(Exif) + COM 注释段，扫描数据 FFDA 之后原样不动
    PNG   IEND 之前插入 tEXt / iTXt 文本块，IDAT 一个字节不改
    GIF   在 trailer(0x3B) 之前插入 Comment Extension 块
    WEBP  RIFF 内插入/替换 EXIF chunk，VP8/VP8L 数据不动

其他格式（BMP / TIFF / PPM 等没有通用文本元数据区）由调用方回退到重编码。
"""
from __future__ import annotations

import random
import struct
import tempfile
import zlib
from pathlib import Path

from . import random_fp
from .config import ImageOptions
from .images import _build_exif

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
PNG_SIG = b"\x89PNG\r\n\x1a\n"

# JPEG 里可以被我们重写的段：APP0(JFIF 保留)、APP1(EXIF/XMP)、APP13(IPTC)、COM
JPEG_STRIP = {0xE1, 0xE2, 0xED, 0xFE}


class MetaInjectUnsupported(RuntimeError):
    """该格式没有可安全插入的元数据区，调用方应回退到重编码。"""


# ------------------------------------------------------------------ 工具
def _detect(data: bytes) -> str:
    if data[:2] == JPEG_SOI:
        return "jpeg"
    if data[:8] == PNG_SIG:
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _pick_meta(opt: ImageOptions, rng: random.Random) -> dict:
    return random_fp.image_metadata(opt.meta_profile, rng)


def _atomic_write(dst: Path, payload: bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mij_", suffix=dst.suffix, dir=str(dst.parent))
    import os
    os.close(fd)
    tmp = Path(name)
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ------------------------------------------------------------------ JPEG
def _jpeg_parse(data: bytes):
    """返回 (头部段列表, SOS 起点)。段 = (marker, raw_bytes)。"""
    if data[:2] != JPEG_SOI:
        raise MetaInjectUnsupported("不是 JPEG")
    pos = 2
    segs: list[tuple[int, bytes]] = []
    sos_at = len(data)
    while pos + 1 < len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker == 0xD8:
            pos += 2
            continue
        if marker == 0xD9:                       # EOI
            sos_at = pos
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            segs.append((marker, data[pos:pos + 2]))
            pos += 2
            continue
        if marker == 0xDA:                       # SOS：之后是压缩数据
            sos_at = pos
            break
        if pos + 4 > len(data):
            sos_at = pos
            break
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2 or pos + 2 + length > len(data):
            sos_at = pos
            break
        segs.append((marker, data[pos:pos + 2 + length]))
        pos += 2 + length
    return segs, sos_at


def _jpeg_segment(marker: int, payload: bytes) -> bytes:
    if len(payload) + 2 > 0xFFFF:
        payload = payload[:0xFFFD]
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _inject_jpeg(data: bytes, meta: dict, keep_original: bool) -> bytes:
    segs, sos_at = _jpeg_parse(data)
    if sos_at <= 2 or sos_at > len(data):
        raise MetaInjectUnsupported("JPEG 结构异常")

    parts = [JPEG_SOI]
    for marker, raw in segs:
        if keep_original and marker in JPEG_STRIP:
            parts.append(raw)                    # 保留原元数据
            continue
        if marker in JPEG_STRIP:
            continue                             # 丢弃旧 EXIF/注释
        parts.append(raw)

    # 新 EXIF（APP1）
    exif = _build_exif(meta)
    if exif:
        parts.append(_jpeg_segment(0xE1, b"Exif\x00\x00" + exif))
    # 新注释（COM）
    comment = f"{meta['comment']} {meta['user_comment']}".encode("ascii", "ignore")
    parts.append(_jpeg_segment(0xFE, comment))

    parts.append(data[sos_at:])                  # 扫描数据到 EOI，原样
    return b"".join(parts)


# ------------------------------------------------------------------ PNG
def _png_chunks(data: bytes):
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            break
        yield ctype, pos, end
        pos = end
        if ctype == b"IEND":
            break


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def _inject_png(data: bytes, meta: dict, keep_original: bool) -> bytes:
    if data[:8] != PNG_SIG:
        raise MetaInjectUnsupported("不是 PNG")
    chunks = list(_png_chunks(data))
    if not chunks or chunks[-1][0] != b"IEND":
        raise MetaInjectUnsupported("PNG 缺少 IEND")

    drop = set() if keep_original else {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}
    head_end = data.find(b"IDAT") - 4 if b"IDAT" in data else chunks[-1][1]

    out = bytearray(PNG_SIG)
    # 先原样拷贝 IHDR 起见的所有 chunk，跳过要丢弃的文本块
    for ctype, start, end in chunks:
        if ctype == b"IEND":
            break
        if ctype in drop:
            continue
        out += data[start:end]

    texts = {
        "Software": meta["software"] or "PixelShift",
        "Comment": meta["comment"],
        "Description": meta["description"],
        "Author": meta["artist"] or "unknown",
        "Fingerprint": meta["user_comment"],
        "Creation Time": meta["datetime"].replace(":", "-", 2),
    }
    for key, val in texts.items():
        payload = key.encode("latin-1", "ignore") + b"\x00" + val.encode("latin-1", "ignore")
        out += _png_chunk(b"tEXt", payload)

    # IEND
    out += data[chunks[-1][1]:chunks[-1][2]]
    return bytes(out)


# ------------------------------------------------------------------ GIF
def _inject_gif(data: bytes, meta: dict, keep_original: bool) -> bytes:
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise MetaInjectUnsupported("不是 GIF")
    if not data.endswith(b"\x3b"):
        idx = data.rfind(b"\x3b")
        if idx < 0:
            raise MetaInjectUnsupported("GIF 缺少 trailer")
    else:
        idx = len(data) - 1

    text = f"{meta['comment']} {meta['user_comment']}".encode("ascii", "ignore")[:255]
    comment = b"\x21\xfe" + bytes([len(text)]) + text + b"\x00"
    return data[:idx] + comment + data[idx:]


# ------------------------------------------------------------------ WEBP
def _webp_chunks(data: bytes):
    pos = 12
    total = len(data)
    while pos + 8 <= total:
        fourcc = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        end = pos + 8 + size + (size & 1)
        if end > total:
            break
        yield fourcc, pos, end
        pos = end


def _inject_webp(data: bytes, meta: dict, keep_original: bool) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise MetaInjectUnsupported("不是 WEBP")
    parts = [b"WEBP"]
    replaced = False
    for fourcc, start, end in _webp_chunks(data):
        if fourcc in (b"EXIF", b"XMP "):
            if keep_original:
                parts.append(data[start:end])
            continue
        parts.append(data[start:end])

    exif = _build_exif(meta)
    if exif:
        payload = exif
        parts.append(b"EXIF" + struct.pack("<I", len(payload)) + payload
                     + (b"\x00" if len(payload) & 1 else b""))
        replaced = True

    body = b"".join(parts)
    out = b"RIFF" + struct.pack("<I", len(body)) + body
    return out


# ------------------------------------------------------------------ 主流程
_HANDLERS = {"jpeg": _inject_jpeg, "png": _inject_png, "gif": _inject_gif,
             "webp": _inject_webp}


def supported(src: str | Path) -> bool:
    try:
        with open(src, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    return bool(_detect(head))


def process(src: str | Path, dst: str | Path, opt: ImageOptions,
            seed: int | None = None) -> dict:
    """只注入元数据。体积几乎不变，像素与压缩数据原样保留。"""
    src, dst = Path(src), Path(dst)
    rng = random.Random(seed)
    raw = src.read_bytes()
    kind = _detect(raw[:16])
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise MetaInjectUnsupported(
            f"{src.suffix or '未知'} 没有可安全插入的元数据区")

    meta = _pick_meta(opt, rng)
    new_data = handler(raw, meta, keep_original=not opt.strip_metadata)
    _atomic_write(dst, new_data)

    from .hasher import digest_file
    from PIL import Image as _Image
    try:
        with _Image.open(dst) as im:
            width, height = im.size
            img_format = im.format
            frames = getattr(im, "n_frames", 1)
    except Exception:                                # noqa: BLE001
        width = height = 0
        img_format = kind.upper()
        frames = 1

    old = digest_file(src)
    new = digest_file(dst)
    return {
        "src": str(src), "dst": str(dst), "kind": "image",
        "src_format": kind.upper(), "out_format": img_format,
        "width": width, "height": height, "frames": frames,
        "old_size": old.size, "new_size": new.size,
        "old_md5": old.md5, "new_md5": new.md5,
        "changed": old.md5 != new.md5,
        "delta": new.size - old.size,
        "engine_mode": "metaonly",
        "metadata_written": True,
        "note": "仅注入元数据（未重编码、未改像素）",
    }
