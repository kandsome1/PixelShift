"""回归自测：把关键路径跑一遍，全部通过才算是可用交付。

    python tools/smoke_test.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from pixelshift.config import ImageOptions, RunOptions, VideoOptions  # noqa: E402
from pixelshift.engine import Engine, collect_files  # noqa: E402
from pixelshift.hasher import digest_file, perceptual_hash  # noqa: E402
from pixelshift.videos import probe  # noqa: E402

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


def run(jobs, out: Path, **kw) -> "object":
    opts = RunOptions(out_dir=str(out), concurrency=3, video_workers=2, **kw)
    eng = Engine(opts)
    return eng.run(jobs)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ps_smoke_"))
    try:
        print("\n== 1. 图片：标准预设 ==")
        out = tmp / "img"
        jobs = collect_files([SAMPLES / "photo_exif.jpg"])
        s = run(jobs, out, image=ImageOptions(pixel_noise=25, color_jitter=8,
                                              micro_resize=1, inject_metadata=True),
                video=VideoOptions())
        f = out / "photo_exif.jpg"
        check("输出文件生成", f.exists())
        rec = s.records[0]
        check("MD5 已改变", rec.old_md5 != rec.new_md5, f"{rec.old_md5} vs {rec.new_md5}")
        check("EXIF 已写入", bool(Image.open(f).getexif()))
        check("尺寸保持一致", Image.open(f).size == Image.open(SAMPLES / "photo_exif.jpg").size)

        print("\n== 2. 图片：格式转换 JPEG → WEBP ==")
        out2 = tmp / "conv"
        jobs = collect_files([SAMPLES / "photo_plain.jpg"])
        run(jobs, out2, image=ImageOptions(out_format="webp"), video=VideoOptions())
        w = out2 / "photo_plain.webp"
        check("WEBP 输出存在", w.exists())
        check("格式正确", w.exists() and Image.open(w).format == "WEBP")

        print("\n== 3. 图片：结构变形（裁切 + 镜像）=")
        out3 = tmp / "deform"
        jobs = collect_files([SAMPLES / "photo_plain.jpg"])
        run(jobs, out3, image=ImageOptions(crop_percent=3.0, flip="auto", pixel_noise=30),
            video=VideoOptions())
        d = out3 / "photo_plain.jpg"
        h_old = perceptual_hash(SAMPLES / "photo_plain.jpg")[0]
        h_new = perceptual_hash(d)[0]
        check("感知哈希已改变", h_old != h_new, f"{h_old} vs {h_new}")
        check("分辨率不变", Image.open(d).size == (1280, 720))

        print("\n== 4. 视频：三种模式 ==")
        for mode, filt, audio in (("remux", 0, "copy"), ("light", 1, "copy"),
                                  ("deep", 3, "reencode")):
            o = tmp / f"vid_{mode}"
            jobs = collect_files([SAMPLES / "clip_h264.mp4"])
            s = run(jobs, o, image=ImageOptions(), video=VideoOptions(
                mode=mode, filter_level=filt, audio_mode=audio, crf=24))
            r = s.records[0]
            f = r.dst
            check(f"{mode} 成功", r.ok and f is not None and f.exists(), r.error)
            if f and f.exists():
                pr = probe(f)
                check(f"{mode} 可解码且分辨率保留", pr.width == 640 and pr.height == 360,
                      f"{pr.width}x{pr.height}")
                check(f"{mode} MD5 已改变", r.old_md5 != r.new_md5)

        print("\n== 5. 视频：镜像 + 变速 + 裁切 ==")
        o = tmp / "vid_deform"
        jobs = collect_files([SAMPLES / "clip_h264.mp4"])
        s = run(jobs, o, image=ImageOptions(),
                video=VideoOptions(mode="remux", mirror=True, speed=1.02, crop_percent=2.0))
        r = s.records[0]
        check("remux 自动升级为重编码", r.ok and r.dst and r.dst.exists(), r.error)
        if r.ok and r.dst:
            pr = probe(r.dst)
            check("时长近似（变速生效）", abs(pr.duration - 3.0 / 1.02) < 0.3,
                  f"{pr.duration:.2f}s")

        print("\n== 6. 原地覆盖（输出到源目录 + overwrite）==")
        work = tmp / "inplace"
        work.mkdir()
        shutil.copy(SAMPLES / "photo_plain.jpg", work / "a.jpg")
        shutil.copy(SAMPLES / "clip_h264.mp4", work / "b.mp4")
        before = {p.name: digest_file(p).md5 for p in work.iterdir() if p.suffix != ".csv"}
        jobs = collect_files([work])
        opts = RunOptions(out_dir=str(work), collision="overwrite", concurrency=2,
                          image=ImageOptions(pixel_noise=20), video=VideoOptions(mode="remux"))
        s = Engine(opts).run(jobs)
        after = {p.name: digest_file(p).md5 for p in work.iterdir() if p.suffix != ".csv"}
        check("文件数量不变", len(before) == len(after), f"{before.keys()} / {after.keys()}")
        check("没有残留临时文件", not any(".ps_tmp" in p.name for p in work.iterdir()))
        check("图片哈希已变", before.get("a.jpg") != after.get("a.jpg"))
        check("视频哈希已变", before.get("b.mp4") != after.get("b.mp4"))
        check("视频仍可解码", 640 == probe(work / "b.mp4").width)

        print("\n== 7. 不支持的文件 / 跳过模式 ==")
        junk = tmp / "junk"
        junk.mkdir()
        (junk / "note.txt").write_text("hello", encoding="utf-8")
        shutil.copy(SAMPLES / "photo_plain.jpg", junk / "img.jpg")
        jobs = collect_files([junk])
        check("txt 未被收集", len(jobs) == 1, f"{[j.src.name for j in jobs]}")
        out7 = tmp / "skip_out"
        s1 = run(collect_files([junk]), out7, image=ImageOptions(), video=VideoOptions())
        s2 = run(collect_files([junk]), out7, collision="skip",
                 image=ImageOptions(), video=VideoOptions())
        check("第二次运行为跳过", all(r.status == "skipped" for r in s2.records),
              [r.status for r in s2.records])

        print("\n== 8. 动画 GIF ==")
        out8 = tmp / "gif"
        jobs = collect_files([SAMPLES / "anim.gif"])
        run(jobs, out8, image=ImageOptions(pixel_noise=25), video=VideoOptions())
        g = Image.open(out8 / "anim.gif")
        check("帧数保留", getattr(g, "n_frames", 1) == 8, getattr(g, "n_frames", 1))

        print("\n== 9. CSV 报告 ==")
        check("报告已生成", any(p.suffix == ".csv" for p in tmp.rglob("*.csv")))

        print("\n== 10. PNG 透明通道 ==")
        out10 = tmp / "png"
        run(collect_files([SAMPLES / "graphic_alpha.png"]), out10,
            image=ImageOptions(pixel_noise=20), video=VideoOptions())
        p = Image.open(out10 / "graphic_alpha.png")
        check("alpha 通道保留", p.mode == "RGBA", p.mode)
        check("PNG 文本块已写入", bool(p.text.get("Software")), dict(p.text))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== 结果：通过 {PASS} · 失败 {FAIL} =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
