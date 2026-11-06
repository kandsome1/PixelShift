"""图片管线：解码 → 像素微扰 → 重编码 → 元数据重构。

目标有两层：
  1) 文件层特征码（MD5/SHA1）必变 —— 通过重编码 + 元数据重构保证；
  2) 视觉层指纹（pHash / aHash / 平台查重）尽量变 —— 通过像素扰动、缩放往返、
     色调微调实现，且强度可调，轻度模式下肉眼不可见。
"""
from __future__ import annotations

import os
import random
import tempfile
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from . import random_fp
from .config import ImageOptions

# 保存映射
_FMT_SAVE = {
    "JPEG": ("JPEG", ".jpg"),
    "PNG": ("PNG", ".png"),
    "WEBP": ("WEBP", ".webp"),
    "BMP": ("BMP", ".bmp"),
    "TIFF": ("TIFF", ".tiff"),
    "GIF": ("GIF", ".gif"),
    "PPM": ("PPM", ".ppm"),
}
_ALIAS = {"JPG": "JPEG", "TIF": "TIFF", "MPO": "JPEG"}

# 高精度 / 非 sRGB 模式：一律不做会改变数值语义的像素级操作
# （16bit 灰度、CMYK 印刷稿、浮点图，一旦转 RGB 就永久损失精度）
PRECISE_MODES = {"I", "I;16", "I;16B", "I;16L", "I;16N", "F",
                 "CMYK", "YCbCr", "LAB", "HSV"}

# 各保存格式能原样接受的 PIL 模式；不在此集合里的才做转换
FORMAT_MODES = {
    "JPEG": {"L", "RGB", "CMYK"},
    "PNG": {"1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA"},
    "TIFF": {"1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I", "I;16", "F", "YCbCr"},
    "WEBP": {"RGB", "RGBA"},
    "BMP": {"1", "L", "P", "RGB", "RGBA"},
    "GIF": {"P", "L"},
    "PPM": {"RGB", "L"},
}

# EXIF IFD0 标签
T_DESC = 270
T_MAKE = 271
T_MODEL = 272
T_SOFTWARE = 305
T_DATETIME = 306
T_ARTIST = 315
T_COPYRIGHT = 33432


class ImageError(RuntimeError):
    pass


# ------------------------------------------------------------------ 工具
def resolve_save_format(opt: ImageOptions, src_fmt: str, animated: bool) -> tuple[str, str]:
    """返回 (PIL 保存格式, 目标扩展名)。"""
    fmt = src_fmt.upper()
    fmt = _ALIAS.get(fmt, fmt)
    if opt.out_format == "keep":
        if animated:
            return "GIF", ".gif"
        if fmt in _FMT_SAVE:
            return _FMT_SAVE[fmt]
        return "PNG", ".png"
    want = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP",
            "bmp": "BMP", "tiff": "TIFF"}[opt.out_format]
    return _FMT_SAVE[want]


def _flatten(img: Image.Image) -> Image.Image:
    """把带 alpha 的图铺到白底上，或把异常模式转成 RGB。"""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if img.mode in ("RGB", "L"):
        return img
    return img.convert("RGB")


def _build_exif(meta: dict) -> bytes:
    """构造一份全新的、看起来合理的 EXIF。"""
    exif = Image.Exif()
    exif[T_DESC] = meta["description"]
    exif[T_MAKE] = meta["make"]
    exif[T_MODEL] = meta["model"]
    exif[T_SOFTWARE] = meta["software"]
    exif[T_DATETIME] = meta["datetime"]
    exif[T_ARTIST] = meta["artist"]
    exif[T_COPYRIGHT] = meta["copyright"]
    # ExifIFD 子目录（不同 Pillow 版本行为不一，失败就只保留 IFD0）
    try:
        ifd = exif.get_ifd(0x8769)
        ifd[36867] = meta["datetime_original"]
        ifd[36868] = meta["datetime_digitized"]
        ifd[37510] = b"ASCII\x00\x00\x00" + meta["user_comment"].encode("ascii", "ignore")
    except Exception:
        pass
    try:
        return exif.tobytes()
    except Exception:
        return b""


def _grain_bytes(img: Image.Image, opt: ImageOptions, rng: random.Random) -> Image.Image:
    """逐字节扰动：随机挑一小撮像素通道做 ±delta，肉眼不可见但破坏统计特征。"""
    if opt.pixel_noise <= 0:
        return img
    # 只处理 8bit 单通道/三通道；其余模式直接返回，绝不转 RGB 降精度
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        return img
    buf = bytearray(img.tobytes())
    total = len(buf)
    count = int(total * (opt.pixel_noise / 100.0) * 0.0035)
    # 通道权重：蓝通道最不敏感，少动绿通道
    if img.mode.startswith("RGB"):
        chans = 3 if img.mode == "RGB" else 4
        weights = [1, 2, 3] if chans == 3 else [1, 2, 3, 4]
        hot = [i for i in range(chans) for _ in range(weights[i])]
    else:
        chans, hot = 1, [0]
    delta = max(1, min(6, opt.noise_delta))
    rnd = rng.randrange
    for _ in range(count):
        idx = rnd(total)
        base = idx - (idx % chans)
        idx = base + rng.choice(hot)
        if idx >= total:
            continue
        v = buf[idx] + rng.choice((-delta, -1, 1, delta))
        buf[idx] = 255 if v > 255 else (0 if v < 0 else v)
    return Image.frombytes(img.mode, img.size, bytes(buf))


def _resize_roundtrip(img: Image.Image, amount: int) -> Image.Image:
    if amount <= 0:
        return img
    w, h = img.size
    k = max(1, min(amount, w // 16, h // 16))
    if w - k < 16 or h - k < 16:
        return img
    small = img.resize((w - k, h - k), Image.LANCZOS)
    return small.resize((w, h), Image.BICUBIC)


def _crop_box(size: tuple[int, int], pct: float, rng: random.Random):
    """算出裁切框；独立出来是为了让多帧（GIF）共用同一套参数，避免闪烁。"""
    w, h = size
    pct = min(8.0, max(0.2, pct))
    k = pct / 200.0
    if w * k < 2 or h * k < 2:
        return None
    dx = rng.uniform(-0.25, 0.25) * k * w
    dy = rng.uniform(-0.25, 0.25) * k * h
    box = (int(round(k * w + dx)), int(round(k * h + dy)),
           int(round(w - k * w + dx)), int(round(h - k * h + dy)))
    box = (max(0, box[0]), max(0, box[1]),
           min(w, max(box[0] + 8, box[2])), min(h, max(box[1] + 8, box[3])))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return None
    return box


def _crop_zoom(img: Image.Image, pct: float, rng: random.Random,
               box=None) -> Image.Image:
    """居中裁掉 pct% 再放大回原尺寸：内容整体放大约 1/(1-pct)，感知哈希几乎必变。"""
    if pct <= 0:
        return img
    box = box or _crop_box(img.size, pct, rng)
    if not box:
        return img
    return img.crop(box).resize(img.size, Image.LANCZOS)


_ROTATE_MAP = {
    "right90": Image.Transpose.ROTATE_270,
    "left90": Image.Transpose.ROTATE_90,
    "180": Image.Transpose.ROTATE_180,
}


def _rotate(img: Image.Image, mode: str) -> Image.Image:
    tr = _ROTATE_MAP.get(mode)
    return img.transpose(tr) if tr is not None else img


def _color_jitter(img: Image.Image, strength: int, rng: random.Random) -> Image.Image:
    if strength <= 0:
        return img
    f = strength / 100.0
    b = 1.0 + rng.uniform(-0.020, 0.020) * f
    c = 1.0 + rng.uniform(-0.020, 0.020) * f
    s = 1.0 + rng.uniform(-0.030, 0.030) * f
    g = 1.0 + rng.uniform(-0.015, 0.015) * f
    img = ImageEnhance.Brightness(img).enhance(b)
    img = ImageEnhance.Contrast(img).enhance(c)
    try:
        img = ImageEnhance.Color(img).enhance(s)
    except Exception:
        pass
    if abs(g - 1.0) > 1e-4:
        lut = [max(0, min(255, int(((i / 255.0) ** g) * 255))) for i in range(256)]
        if img.mode == "RGB":
            img = img.point(lut * 3)
        elif img.mode == "L":
            img = img.point(lut)
    return img


def _save_kwargs(out_fmt: str, opt: ImageOptions, exif_bytes: bytes,
                 comment: str, icc: bytes | None) -> dict:
    kw: dict = {}
    if out_fmt == "JPEG":
        kw["quality"] = max(60, min(100, opt.jpeg_quality))
        kw["optimize"] = True
        sub = opt.jpeg_subsampling
        if sub == "4:2:0":
            kw["subsampling"] = 2
        elif sub == "4:2:2":
            kw["subsampling"] = 1
        elif sub == "4:4:4":
            kw["subsampling"] = 0
        if exif_bytes:
            kw["exif"] = exif_bytes
        if comment:
            kw["comment"] = comment.encode("utf-8", "ignore")
    elif out_fmt == "PNG":
        kw["compress_level"] = (opt.png_level if opt.png_level >= 0 else 9)
        kw["optimize"] = False
    elif out_fmt == "WEBP":
        kw["quality"] = max(50, min(100, opt.webp_quality))
        kw["method"] = 6
        if exif_bytes:
            kw["exif"] = exif_bytes
    elif out_fmt == "TIFF":
        if exif_bytes:
            kw["exif"] = exif_bytes
    if icc and out_fmt in ("JPEG", "PNG", "WEBP", "TIFF"):
        kw["icc_profile"] = icc
    return kw


def _png_meta(info_png, meta: dict) -> None:
    from PIL.PngImagePlugin import PngInfo
    info_png.add_text("Software", meta["software"] or "PixelShift 1.0")
    info_png.add_text("Comment", meta["comment"])
    info_png.add_text("Description", meta["description"])
    info_png.add_text("Creation Time", meta["datetime"].replace(":", "-", 2))
    info_png.add_text("Author", meta["artist"] or "unknown")
    info_png.add_text("Fingerprint", meta["user_comment"])


# ------------------------------------------------------------------ 主流程
def process_image(src: str | Path, dst: str | Path, opt: ImageOptions,
                  seed: int | None = None) -> dict:
    """处理单张图片，返回统计信息。dst 由调用方决定（含正确扩展名）。"""
    src, dst = Path(src), Path(dst)
    rng = random.Random(seed)
    info: dict = {"src": str(src), "dst": str(dst), "kind": "image"}

    with Image.open(src) as im:
        im.load()
        src_fmt = (im.format or "").upper()
        animated = bool(getattr(im, "is_animated", False)) and getattr(im, "n_frames", 1) > 1
        out_fmt, ext = resolve_save_format(opt, src_fmt, animated)
        info.update(src_format=src_fmt, out_format=out_fmt, animated=animated,
                    width=im.size[0], height=im.size[1])

        icc = im.info.get("icc_profile") if opt.keep_icc else None

        # ---- 动画 GIF：逐帧重编码，多帧共用一套变形参数，避免闪烁
        if animated and out_fmt == "GIF":
            size = im.size
            box = _crop_box(size, opt.crop_percent, rng) if opt.crop_percent > 0 else None
            fixed = random.Random(seed if seed is not None else 20240101)
            frames, durs = [], []
            for i in range(getattr(im, "n_frames", 1)):
                im.seek(i)
                frame = im.convert("RGBA")
                if opt.rotate != "none":
                    frame = _rotate(frame, opt.rotate)
                if opt.flip == "auto":
                    frame = ImageOps.mirror(frame)
                rgb = _flatten(frame)
                if box:
                    rgb = _crop_zoom(rgb, opt.crop_percent, fixed, box)
                if opt.color_jitter > 0:
                    rgb = _color_jitter(rgb, opt.color_jitter,
                                        random.Random(fixed.random()))
                frames.append(rgb.convert("P", palette=Image.ADAPTIVE, colors=256))
                durs.append(im.info.get("duration", 100))
            meta = random_fp.image_metadata(opt.meta_profile, rng)
            kw = dict(save_all=True, append_images=frames[1:], loop=im.info.get("loop", 0),
                      duration=durs, optimize=False)
            if opt.inject_metadata:
                kw["comment"] = f"{meta['comment']} {meta['user_comment']}".encode()
            _atomic_save(lambda tmp: frames[0].save(tmp, "GIF", **kw), dst)
            info.update(frames=len(frames), note="动画 GIF 逐帧重编码")
            return _finish(src, dst, info, animated=True)

        # ---- 静态图（含 GIF 首帧）
        img = im if not animated else im.convert("RGB")
        work = img.copy()
        # 高精度 / 非 RGB 模式：不做会改变数值语义的像素操作，只改元数据
        src_mode = work.mode
        precise = src_mode in PRECISE_MODES
        if precise:
            info["precise_mode"] = src_mode

        # 1) 可选旋转 / 镜像（纯几何变换，对任何模式都安全）
        if opt.rotate != "none":
            work = _rotate(work, opt.rotate)
            info["rotate"] = opt.rotate
        if opt.flip == "auto" and rng.random() < 0.5:
            work = ImageOps.mirror(work)
            info["mirror"] = True

        # 2) 居中裁切 + 放大回原尺寸（强改感知指纹）
        if opt.crop_percent > 0 and not precise:
            work = _crop_zoom(work, opt.crop_percent, rng)
            info["crop_percent"] = opt.crop_percent

        # 3) 缩放往返（打断重采样指纹）
        if opt.micro_resize > 0 and not precise:
            work = _resize_roundtrip(work, rng.randint(1, opt.micro_resize))

        # 4) 色调微调
        if opt.color_jitter > 0 and not precise:
            work = _color_jitter(work, opt.color_jitter, rng)

        # 5) 像素噪声
        if opt.pixel_noise > 0 and not precise:
            work = _grain_bytes(work, opt, rng)

        # 6) 格式落定：只在目标格式不支持该模式时才转换，并记录原因
        accepted = FORMAT_MODES.get(out_fmt)
        if accepted and work.mode not in accepted:
            before = work.mode
            if out_fmt == "JPEG" and before in ("RGBA", "LA", "P"):
                work = _flatten(work)
                info["mode_note"] = f"{before} → RGB（铺白底）"
            else:
                work = work.convert("RGB")
                info["mode_note"] = f"{before} → RGB（{out_fmt} 不支持该模式）"
        elif work.mode == "P" and out_fmt in ("PNG", "WEBP", "TIFF"):
            work = work.convert("RGBA") if "transparency" in img.info else work.convert("RGB")
            info["mode_note"] = "P → RGBA/RGB（保留透明）"

        meta = random_fp.image_metadata(opt.meta_profile, rng)
        exif_bytes = _build_exif(meta) if opt.inject_metadata else b""
        comment = f"{meta['comment']} {meta['user_comment']}" if opt.inject_metadata else ""
        kwargs = _save_kwargs(out_fmt, opt, exif_bytes, comment, icc)

        if out_fmt == "PNG":
            from PIL.PngImagePlugin import PngInfo
            pnginfo = PngInfo()
            if opt.inject_metadata:
                _png_meta(pnginfo, meta)
            kwargs["pnginfo"] = pnginfo

        _atomic_save(lambda tmp: work.save(tmp, out_fmt, **kwargs), dst)

        info.update(width=work.size[0], height=work.size[1], mode=work.mode,
                    metadata_written=bool(opt.inject_metadata))
        del work, img
    return _finish(src, dst, info)


def _atomic_save(writer, dst: Path) -> None:
    """先写临时文件再替换，避免中断留下半张图。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".ps_", suffix=dst.suffix, dir=str(dst.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        writer(str(tmp))
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _finish(src: Path, dst: Path, info: dict, animated: bool = False) -> dict:
    from .hasher import digest_file
    old = digest_file(src)
    new = digest_file(dst)
    info.update(old_size=old.size, new_size=new.size, old_md5=old.md5, new_md5=new.md5,
                changed=old.md5 != new.md5)
    return info
