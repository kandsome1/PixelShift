"""「懒人模式」自测：只注入元数据、不重编码、体积几乎不变。

    python tools/metaonly_test.py
"""
from __future__ import annotations

import shutil
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from pixelshift import metainject  # noqa: E402
from pixelshift.config import ImageOptions, RunOptions, VideoOptions, preset_options  # noqa: E402
from pixelshift.engine import Engine, collect_files  # noqa: E402
from pixelshift.hasher import digest_file  # noqa: E402

SAMPLES = ROOT / "samples"
PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# ------------------------------------------------------- 压缩数据提取（证明没动画面）
def jpeg_scan_bytes(data: bytes) -> bytes:
    start = data.find(b"\xff\xda")
    end = data.rfind(b"\xff\xd9")
    return data[start:end] if start >= 0 and end > start else b""


def png_payload(data: bytes, want: tuple[bytes, ...]) -> bytes:
    pos, out = 8, bytearray()
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        if ctype in want:
            out += data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IEND":
            break
    return bytes(out)


def gif_all_frames_rgba(path: Path) -> list[bytes]:
    """把动画 GIF 的每一帧解码成 RGBA 原始字节，用于证明画面完全没变。"""
    frames: list[bytes] = []
    with Image.open(path) as im:
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            frames.append(im.convert("RGBA").tobytes())
    return frames


def pixels(path: Path) -> bytes:
    with Image.open(path) as im:
        return im.convert("RGBA").tobytes()


def webp_image_chunks(data: bytes) -> bytes:
    pos, out = 12, bytearray()
    while pos + 8 <= len(data):
        fourcc = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if fourcc in (b"VP8 ", b"VP8L", b"VP8X", b"ALPH"):
            out += data[pos:pos + 8 + size]
        pos += 8 + size + (size & 1)
    return bytes(out)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ps_metaonly_"))
    opt = ImageOptions(mode="metaonly", strip_metadata=True, inject_metadata=True)
    try:
        print("\n== 1. 格式支持判断 ==")
        check("jpeg 支持", metainject.supported(SAMPLES / "photo_plain.jpg"))
        check("png 支持", metainject.supported(SAMPLES / "graphic_alpha.png"))
        check("gif 支持", metainject.supported(SAMPLES / "anim.gif"))
        bmp = tmp / "x.bmp"
        Image.new("RGB", (32, 32), (10, 20, 30)).save(bmp)
        check("bmp 不支持（需回退重编码）", not metainject.supported(bmp))

        print("\n== 2. JPEG：体积与压缩数据 ==")
        src = SAMPLES / "photo_exif.jpg"
        dst = tmp / "out.jpg"
        info = metainject.process(src, dst, opt, seed=1)
        src_b, dst_b = src.read_bytes(), dst.read_bytes()
        check("MD5 已改变", info["old_md5"] != info["new_md5"])
        check("体积增幅 < 2KB", abs(info["delta"]) < 2048, f"Δ{info['delta']}")
        check("压缩数据字节级一致（SOS..EOI）",
              jpeg_scan_bytes(src_b) == jpeg_scan_bytes(dst_b),
              f"{len(jpeg_scan_bytes(src_b))} bytes")
        check("逐像素完全一致", pixels(src) == pixels(dst))
        check("尺寸不变", (info["width"], info["height"]) == (1280, 720))
        with Image.open(dst) as im:
            exif = dict(im.getexif())
        check("写入了新 EXIF", bool(exif.get(0x10F)) or bool(exif.get(0x131)), exif)
        check("EXIF Make 来自随机池", isinstance(exif.get(0x10F), str), exif.get(0x10F))

        print("\n== 3. PNG：IDAT 未改动 ==")
        psrc = SAMPLES / "graphic_alpha.png"
        pdst = tmp / "out.png"
        pinfo = metainject.process(psrc, pdst, opt, seed=2)
        check("MD5 已改变", pinfo["old_md5"] != pinfo["new_md5"])
        check("体积增幅 < 2KB", abs(pinfo["delta"]) < 2048, f"Δ{pinfo['delta']}")
        check("IDAT 字节级一致",
              png_payload(psrc.read_bytes(), (b"IDAT",)) == png_payload(pdst.read_bytes(), (b"IDAT",)),
              f"{len(png_payload(psrc.read_bytes(), (b'IDAT',)))} bytes")
        check("逐像素完全一致", pixels(psrc) == pixels(pdst))
        with Image.open(pdst) as im:
            check("alpha 通道保留", im.mode == "RGBA", im.mode)
            check("文本块已写入", bool(im.text.get("Software")), dict(im.text))

        print("\n== 4. GIF：帧数与图像块未改动 ==")
        gsrc = SAMPLES / "anim.gif"
        gdst = tmp / "out.gif"
        ginfo = metainject.process(gsrc, gdst, opt, seed=3)
        check("MD5 已改变", ginfo["old_md5"] != ginfo["new_md5"])
        check("体积增幅 < 1KB", abs(ginfo["delta"]) < 1024, f"Δ{ginfo['delta']}")
        check("8 帧逐像素完全一致", gif_all_frames_rgba(gsrc) == gif_all_frames_rgba(gdst))
        with Image.open(gdst) as im:
            check("8 帧保留", getattr(im, "n_frames", 1) == 8, getattr(im, "n_frames", 1))

        print("\n== 5. WEBP：图像 chunk 未改动 ==")
        wsrc = tmp / "in.webp"
        with Image.open(SAMPLES / "photo_plain.jpg") as im:
            im.save(wsrc, "WEBP", quality=85)
        wdst = tmp / "out.webp"
        winfo = metainject.process(wsrc, wdst, opt, seed=4)
        check("MD5 已改变", winfo["old_md5"] != winfo["new_md5"])
        check("体积增幅 < 2KB", abs(winfo["delta"]) < 2048, f"Δ{winfo['delta']}")
        check("VP8 图像 chunk 字节级一致",
              webp_image_chunks(wsrc.read_bytes()) == webp_image_chunks(wdst.read_bytes()))
        check("逐像素完全一致", pixels(wsrc) == pixels(wdst))
        with Image.open(wdst) as im:
            check("WEBP 可解码", im.format == "WEBP", im.format)

        print("\n== 6. 每次输出都不一样（随机化）==")
        h = set()
        for i in range(3):
            o = tmp / f"r{i}.jpg"
            metainject.process(src, o, opt, seed=None)
            h.add(digest_file(o).md5)
        check("三次处理得到三个不同哈希", len(h) == 3, len(h))

        print("\n== 7. engine 集成：懒人预设 ==")
        out = tmp / "lazy_run"
        img_opt, vid_opt = preset_options("lazy")
        opts = RunOptions(out_dir=str(out), image=img_opt, video=vid_opt)
        jobs = [j for j in collect_files([SAMPLES]) if j.src.name != "clip_h265.mkv"]
        summary = Engine(opts).run(jobs)
        check("全部成功", summary.failed == 0, [r.error for r in summary.records if r.error])
        total_in = total_out = 0
        for rec in summary.records:
            if rec.ok and rec.dst:
                total_in += rec.old_size
                total_out += rec.new_size
                is_meta = "仅注入元数据" in (rec.note or "") or "免ffmpeg" in (rec.note or "")
                check(f"{rec.src.name} 走的是免重编码路径", is_meta, rec.note)
        growth = (total_out - total_in) / total_in * 100 if total_in else 0
        print(f"         总体积 {total_in/1024:.1f} KB -> {total_out/1024:.1f} KB ({growth:+.3f}%)")
        check("总体积增幅 < 1%", abs(growth) < 1.0, f"{growth:+.3f}%")

        print("\n== 8. 不支持格式自动回退重编码 ==")
        out2 = tmp / "fallback"
        bmp2 = tmp / "pic.bmp"
        Image.new("RGB", (64, 64), (200, 120, 40)).save(bmp2)
        opts2 = RunOptions(out_dir=str(out2), image=ImageOptions(mode="metaonly"),
                           video=VideoOptions())
        s2 = Engine(opts2).run(collect_files([bmp2]))
        rec2 = s2.records[0]
        check("bmp 处理成功", rec2.ok, rec2.error)
        check("标记为回退重编码", "无元数据区" in (rec2.note or ""), rec2.note)
        check("输出可解码", rec2.dst and Image.open(rec2.dst).size == (64, 64))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== 结果：通过 {PASS} · 失败 {FAIL} =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
