"""纯 Python 的 MP4 / MOV 容器改写 —— 不需要 ffmpeg 的兜底档。

只动"容器账本"，不碰编码数据：
  · moov/mvhd、moov/trak/mdia/mdhd 的 creation_time / modification_time（1904 纪元）
  · moov/udta/meta/ilst 里的 iTunes 风格标签（©too / ©nam / ©cmt / desc / ©day / ©ART）
  · 缺少 udta/meta/ilst 时按规范新建一套

因为只替换 moov 子树，mdat 一个字节都不读进内存，几十 GB 的文件也是流式拷贝。

产出特性：结构合法（播放器、ffprobe 都能正常解析）、文件哈希必变、元数据全变、
画面与时长完全不变。适合"只想换掉文件特征码、机器上又没有 ffmpeg"的场景。
"""
from __future__ import annotations

import datetime as _dt
import os
import random
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# 只处理 box 结构的容器
SUPPORTED_EXTS = (".mp4", ".m4v", ".mov", ".3gp", ".m4a")

# MP4 时间从 1904-01-01 UTC 起算
MP4_EPOCH = _dt.datetime(1904, 1, 1, tzinfo=_dt.timezone.utc)

# 需要递归下钻的容器（body 里直接就是子 box）
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta",
              b"ilst", b"edts", b"dinf", b"mvex", b"tref", b"mfra", b"stsd"}
# fullbox 容器：body 前 4 字节是 version+flags，子 box 从 +4 开始
FULL_CONTAINERS = {b"meta": 4}

# iTunes 标签名（0xA9 = ©）
T_ENCODER = b"\xa9too"
T_TITLE = b"\xa9nam"
T_COMMENT = b"\xa9cmt"
T_DESC = b"desc"
T_DAY = b"\xa9day"
T_ARTIST = b"\xa9ART"

TAG_LABELS = {
    T_ENCODER: "encoder", T_TITLE: "title", T_COMMENT: "comment",
    T_DESC: "description", T_DAY: "date", T_ARTIST: "artist",
}


class MP4BoxError(RuntimeError):
    """容器解析或改写失败（通常是文件不是标准 MP4/MOV，或结构损坏）。"""


def supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTS


# ------------------------------------------------------------------ 基础解析
@dataclass
class BoxRef:
    type: bytes
    offset: int      # 相对所在 buffer
    size: int        # 含 header
    header: int      # 8 或 16


def iter_boxes(buf: bytes, start: int = 0, end: int | None = None):
    """在内存 buffer 上顺序遍历同级 box。"""
    end = len(buf) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", buf[pos:pos + 4])[0]
        typ = buf[pos + 4:pos + 8]
        header = 8
        if size == 1:                       # 64 位长度
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:                     # 一直延续到结尾
            size = end - pos
        if size < header or pos + size > end:
            break
        yield BoxRef(typ, pos, size, header)
        pos += size


def find_box(buf: bytes, typ: bytes, start: int = 0, end: int | None = None):
    for b in iter_boxes(buf, start, end):
        if b.type == typ:
            return b
    return None


def _pack(typ: bytes, body: bytes) -> bytes:
    total = 8 + len(body)
    if total > 0xFFFFFFFF:
        return struct.pack(">I", 1) + typ + struct.pack(">Q", 16 + len(body)) + body
    return struct.pack(">I", total) + typ + body


# ------------------------------------------------------------------ 顶层扫描
@dataclass
class TopBox:
    type: bytes
    offset: int      # 相对文件
    size: int
    header: int


def scan_top_level(path: str | Path) -> list[TopBox]:
    """只读 box 头，不碰 mdat 内容。"""
    path = Path(path)
    total = path.stat().st_size
    out: list[TopBox] = []
    with open(path, "rb") as f:
        pos = 0
        while pos + 8 <= total:
            f.seek(pos)
            head = f.read(16)
            if len(head) < 8:
                break
            size = struct.unpack(">I", head[0:4])[0]
            typ = head[4:8]
            header = 8
            if size == 1 and len(head) >= 16:
                size = struct.unpack(">Q", head[8:16])[0]
                header = 16
            elif size == 0:
                size = total - pos
            if size < header or pos + size > total:
                break
            out.append(TopBox(typ, pos, size, header))
            pos += size
    return out


# ------------------------------------------------------------------ 时间戳
def _mp4_time(dt: _dt.datetime) -> int:
    return max(0, int((dt - MP4_EPOCH).total_seconds()))


def _random_time(rng: random.Random) -> _dt.datetime:
    start = _dt.datetime(2008, 1, 1, tzinfo=_dt.timezone.utc)
    end = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=2)
    span = int((end - start).total_seconds())
    return start + _dt.timedelta(seconds=rng.randint(0, max(1, span)))


def _patch_time_field(payload: bytes, times: int) -> bytes:
    """把 mvhd / mdhd 里的 creation_time + modification_time 换成新值。"""
    if not payload:
        return payload
    ver = payload[0]
    try:
        if ver == 1:
            if len(payload) < 20:
                return payload
            return (payload[:4] + struct.pack(">Q", times)
                    + struct.pack(">Q", times) + payload[20:])
        if len(payload) < 12:
            return payload
        t32 = min(times, 0xFFFFFFFF)
        return payload[:4] + struct.pack(">I", t32) + struct.pack(">I", t32) + payload[12:]
    except struct.error:
        return payload


def read_duration(moov: bytes) -> float:
    box = find_box(moov, b"mvhd")
    if not box:
        return 0.0
    p = moov[box.offset + box.header: box.offset + box.size]
    try:
        if p[0] == 1:
            scale = struct.unpack(">I", p[20:24])[0]
            dur = struct.unpack(">Q", p[24:32])[0]
        else:
            scale = struct.unpack(">I", p[12:16])[0]
            dur = struct.unpack(">I", p[16:20])[0]
        return dur / scale if scale else 0.0
    except (struct.error, IndexError):
        return 0.0


# ------------------------------------------------------------------ ilst 构造
def _build_data_box(text: str) -> bytes:
    body = struct.pack(">I", 1) + struct.pack(">I", 0) + text.encode("utf-8")
    return _pack(b"data", body)


def _build_hdlr() -> bytes:
    body = (b"\x00" * 4            # version + flags
            + b"\x00" * 4          # pre_defined
            + b"mdir"              # handler_type
            + b"appl"              # reserved[0]：manufacturer
            + b"\x00" * 8          # reserved[1..2]
            + b"\x00")             # name（空串 + 终止符）
    return _pack(b"hdlr", body)


def _build_ilst(tags: dict[bytes, str]) -> bytes:
    parts = [_pack(tag, _build_data_box(val)) for tag, val in tags.items()]
    return _pack(b"ilst", b"".join(parts))


def _build_meta(tags: dict[bytes, str]) -> bytes:
    body = b"\x00" * 4 + _build_hdlr() + _build_ilst(tags)
    return _pack(b"meta", body)


def _build_udta(tags: dict[bytes, str]) -> bytes:
    return _pack(b"udta", _build_meta(tags))


# ------------------------------------------------------------------ 改写 ilst
def _rewrite_ilst(buf: bytes, start: int, end: int, tags: dict[bytes, str]) -> bytes:
    parts: list[bytes] = []
    written: set[bytes] = set()
    for b in iter_boxes(buf, start, end):
        if b.type in tags:
            parts.append(_pack(b.type, _build_data_box(tags[b.type])))
            written.add(b.type)
        else:
            parts.append(buf[b.offset:b.offset + b.size])
    for tag, val in tags.items():
        if tag not in written:
            parts.append(_pack(tag, _build_data_box(val)))
    return b"".join(parts)


def _rewrite_meta(buf: bytes, start: int, end: int, tags: dict[bytes, str]) -> bytes:
    """返回完整的 meta box（含 version/flags），不是 payload。"""
    parts: list[bytes] = []
    found_ilst = False
    for b in iter_boxes(buf, start, end):
        if b.type == b"ilst":
            found_ilst = True
            body = _rewrite_ilst(buf, b.offset + b.header, b.offset + b.size, tags)
            parts.append(_pack(b"ilst", body))
        else:
            parts.append(buf[b.offset:b.offset + b.size])
    if not found_ilst:
        parts.append(_build_ilst(tags))
    body = b"\x00" * 4 + b"".join(parts)
    return _pack(b"meta", body)


def _rewrite_udta(buf: bytes, start: int, end: int, tags: dict[bytes, str]) -> bytes:
    """返回完整的 udta box。"""
    parts: list[bytes] = []
    found_meta = False
    for b in iter_boxes(buf, start, end):
        if b.type == b"meta":
            found_meta = True
            inner_start = b.offset + b.header
            # meta 是 fullbox：跳过 version/flags
            if b.size >= b.header + 4:
                inner_start += 4
            parts.append(_rewrite_meta(buf, inner_start, b.offset + b.size, tags))
        else:
            parts.append(buf[b.offset:b.offset + b.size])
    if not found_meta:
        parts.append(_build_meta(tags))
    return _pack(b"udta", b"".join(parts))


# ------------------------------------------------------------------ moov 重写
def _rewrite_trak(buf: bytes, start: int, end: int, times: int) -> bytes:
    parts: list[bytes] = []
    for b in iter_boxes(buf, start, end):
        if b.type == b"mdia":
            inner: list[bytes] = []
            for c in iter_boxes(buf, b.offset + b.header, b.offset + b.size):
                if c.type == b"mdhd":
                    payload = buf[c.offset + c.header: c.offset + c.size]
                    inner.append(_pack(b"mdhd", _patch_time_field(payload, times)))
                else:
                    inner.append(buf[c.offset:c.offset + c.size])
            parts.append(_pack(b"mdia", b"".join(inner)))
        else:
            parts.append(buf[b.offset:b.offset + b.size])
    return b"".join(parts)


def rewrite_moov(payload: bytes, tags: dict[bytes, str], times: int,
                 patch_mdhd: bool = True) -> bytes:
    """改写 moov 的**payload**（不含 8/16 字节 header），返回新的 payload。"""
    if len(payload) < 8:
        raise MP4BoxError("moov 数据不完整")
    parts: list[bytes] = []
    wrote_udta = False
    wrote_mvhd = False
    for b in iter_boxes(payload, 0, len(payload)):
        if b.type == b"mvhd":
            inner = payload[b.offset + b.header: b.offset + b.size]
            parts.append(_pack(b"mvhd", _patch_time_field(inner, times)))
            wrote_mvhd = True
        elif b.type == b"trak":
            body = (_rewrite_trak(payload, b.offset + b.header, b.offset + b.size, times)
                    if patch_mdhd else payload[b.offset + b.header: b.offset + b.size])
            parts.append(_pack(b"trak", body))
        elif b.type == b"udta":
            parts.append(_rewrite_udta(payload, b.offset + b.header,
                                       b.offset + b.size, tags))
            wrote_udta = True
        else:
            parts.append(payload[b.offset:b.offset + b.size])
    if not wrote_udta:
        parts.append(_build_udta(tags))
    if not wrote_mvhd:
        raise MP4BoxError("moov 里找不到 mvhd，可能不是标准 MP4/MOV")
    return b"".join(parts)


# ------------------------------------------------------------------ 读回标签
def read_tags(path: str | Path) -> dict[str, str]:
    """把 ilst 里的标签读回来（用于自检 / 测试）。"""
    out: dict[str, str] = {}
    try:
        boxes = scan_top_level(path)
        moov_ref = next((b for b in boxes if b.type == b"moov"), None)
        if not moov_ref:
            return out
        with open(path, "rb") as f:
            f.seek(moov_ref.offset + moov_ref.header)
            moov = f.read(moov_ref.size - moov_ref.header)
    except OSError:
        return out

    udta = find_box(moov, b"udta")
    if not udta:
        return out
    meta = find_box(moov, b"meta", udta.offset + udta.header, udta.offset + udta.size)
    if not meta:
        return out
    inner = meta.offset + meta.header + 4
    ilst = find_box(moov, b"ilst", inner, meta.offset + meta.size)
    if not ilst:
        return out
    for tag in iter_boxes(moov, ilst.offset + ilst.header, ilst.offset + ilst.size):
        data = find_box(moov, b"data", tag.offset + tag.header, tag.offset + tag.size)
        if not data:
            continue
        payload = moov[data.offset + data.header: data.offset + data.size]
        if len(payload) <= 8:
            continue
        try:
            text = payload[8:].decode("utf-8", "replace")
        except Exception:                       # noqa: BLE001
            continue
        label = TAG_LABELS.get(tag.type, tag.type.decode("latin-1", "replace"))
        out[label] = text
    return out


# ------------------------------------------------------------------ 主流程
@dataclass
class MetaboxResult:
    src: Path
    dst: Path
    duration: float = 0.0
    duration_after: float = 0.0
    tags: dict = field(default_factory=dict)
    note: str = ""


def _copy_range(src: Path, out, start: int, length: int, chunk: int = 4 << 20) -> None:
    with open(src, "rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            block = f.read(min(chunk, left))
            if not block:
                break
            out.write(block)
            left -= len(block)


def process(src: str | Path, dst: str | Path, seed: int | None = None,
            patch_mdhd: bool = True) -> dict:
    """纯 Python 改写容器元数据。返回与 videos.process_video 同构的信息字典。"""
    src, dst = Path(src), Path(dst)
    if not supported(src):
        raise MP4BoxError(f"不支持该容器：{src.suffix}（只支持 MP4/MOV/M4V/3GP）")

    rng = random.Random(seed)
    total = src.stat().st_size
    if total < 1024:
        raise MP4BoxError("文件太小，不是有效的 MP4")

    boxes = scan_top_level(src)
    moov_ref = next((b for b in boxes if b.type == b"moov"), None)
    if moov_ref is None:
        raise MP4BoxError("找不到 moov box，可能不是标准 MP4/MOV（或需要 ffmpeg 修复）")
    if moov_ref.size > 200 << 20:
        raise MP4BoxError("moov 超过 200 MB，结构异常，已跳过")

    with open(src, "rb") as f:
        f.seek(moov_ref.offset)
        moov_box = f.read(moov_ref.size)
    if len(moov_box) != moov_ref.size:
        raise MP4BoxError("moov 读取不完整")
    moov_payload = moov_box[moov_ref.header:]

    from . import random_fp
    meta = random_fp.video_metadata(rng)
    when = _random_time(rng)
    times = _mp4_time(when)
    tags = {
        T_ENCODER: meta["encoder"],
        T_TITLE: meta["title"],
        T_COMMENT: meta["comment"],
        T_DESC: random_fp.rand_comment(rng),
        T_DAY: when.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    new_payload = rewrite_moov(moov_payload, tags, times, patch_mdhd=patch_mdhd)
    new_moov = _pack(b"moov", new_payload)
    dur_before = read_duration(moov_payload)
    dur_after = read_duration(new_payload)

    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".mbx_", suffix=dst.suffix, dir=str(dst.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with open(tmp, "wb") as out:
            _copy_range(src, out, 0, moov_ref.offset)
            out.write(new_moov)
            tail_start = moov_ref.offset + moov_ref.size
            _copy_range(src, out, tail_start, total - tail_start)
        if tmp.stat().st_size <= 0:
            raise MP4BoxError("输出文件为空")
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    from .hasher import digest_file
    old = digest_file(src)
    new = digest_file(dst)
    info = {
        "src": str(src), "dst": str(dst), "kind": "video",
        "engine_mode": "metabox",
        "duration": round(dur_before, 2), "out_duration": round(dur_after, 2),
        "width": 0, "height": 0, "vcodec": "", "acodec": "", "fps": 0.0,
        "has_audio": False, "has_sub": False,
        "old_size": old.size, "new_size": new.size,
        "old_md5": old.md5, "new_md5": new.md5,
        "changed": old.md5 != new.md5,
        "tags": {k.decode("latin-1", "replace"): v for k, v in tags.items()},
        "note": "仅改容器元数据（未用 ffmpeg，画面与时长不变）",
    }
    return info
