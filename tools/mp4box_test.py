"""免 ffmpeg 兜底档（纯 Python MP4 容器改写）的自测。

    python tools/mp4box_test.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pixelshift import mp4box, videos  # noqa: E402
from pixelshift.config import ImageOptions, RunOptions, VideoOptions  # noqa: E402
from pixelshift.engine import Engine, collect_files  # noqa: E402
from pixelshift.hasher import digest_file  # noqa: E402

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


def ffprobe_tags(path: Path) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=60)
        if out.returncode != 0:
            return {}
        return json.loads(out.stdout.decode("utf-8", "replace") or "{}")
    except Exception:                               # noqa: BLE001
        return {}


def make_clean_mp4(dst: Path) -> bool:
    """生成一个没有 udta 的 MP4（-fflags +bitexact 会去掉所有元数据）。"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             "-map_metadata", "-1", "-fflags", "+bitexact", "-flags", "+bitexact",
             str(dst)],
            capture_output=True, timeout=120)
        return r.returncode == 0 and dst.exists()
    except Exception:                               # noqa: BLE001
        return False


def mdat_bytes(path: Path) -> bytes:
    boxes = mp4box.scan_top_level(path)
    mdat = next((b for b in boxes if b.type == b"mdat"), None)
    if not mdat:
        return b""
    raw = path.read_bytes()
    return raw[mdat.offset: mdat.offset + mdat.size]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ps_mbx_"))
    src = ROOT / "samples" / "clip_h264.mp4"
    try:
        print("\n== 1. 容器识别 ==")
        check("mp4 支持", mp4box.supported("a.mp4"))
        check("mov 支持", mp4box.supported("a.MOV"))
        check("m4v 支持", mp4box.supported("a.m4v"))
        check("mkv 不支持", not mp4box.supported("a.mkv"))
        check("jpg 不支持", not mp4box.supported("a.jpg"))

        print("\n== 2. 顶层 box 解析 ==")
        tops = mp4box.scan_top_level(src)
        types = [b.type.decode("latin-1") for b in tops]
        check("能列出顶层 box", "moov" in types and "mdat" in types, types)
        check("偏移连续无空洞",
              tops[0].offset == 0 and tops[-1].offset + tops[-1].size == src.stat().st_size,
              f"{tops[-1].offset + tops[-1].size} vs {src.stat().st_size}")

        print("\n== 3. 改写带 udta 的文件 ==")
        out = tmp / "with_udta.mp4"
        before_tags = mp4box.read_tags(src)
        info = mp4box.process(src, out, seed=11)
        after_tags = mp4box.read_tags(out)
        check("输出存在", out.exists())
        check("MD5 已改变", info["old_md5"] != info["new_md5"])
        check("标签发生变化", before_tags != after_tags, f"{before_tags} -> {after_tags}")
        check("写入了 5 个标签", len(after_tags) >= 5, after_tags)
        check("时长不变", abs(info["duration"] - info["out_duration"]) < 0.01,
              f"{info['duration']} -> {info['out_duration']}")
        check("mdat 字节未改动", mdat_bytes(src) == mdat_bytes(out))

        print("\n== 4. ffprobe 交叉验证 ==")
        probe = ffprobe_tags(out)
        streams = probe.get("streams") or []
        vstreams = [s for s in streams if s.get("codec_type") == "video"]
        astreams = [s for s in streams if s.get("codec_type") == "audio"]
        tags = {k.lower(): v for k, v in (probe.get("format") or {}).get("tags", {}).items()}
        check("ffprobe 能解析", bool(probe), "解析失败")
        check("有视频流", len(vstreams) == 1, len(vstreams))
        check("有音频流", len(astreams) == 1, len(astreams))
        check("分辨率不变", vstreams and vstreams[0].get("width") == 640
              and vstreams[0].get("height") == 360,
              f"{vstreams[0].get('width')}x{vstreams[0].get('height')}" if vstreams else "-")
        check("时长 3s 不变", abs(float((probe.get("format") or {}).get("duration", 0)) - 3.0) < 0.05,
              (probe.get("format") or {}).get("duration"))
        check("ffprobe 读到新 title", tags.get("title") == after_tags.get("title"),
              f"{tags.get('title')} vs {after_tags.get('title')}")
        check("ffprobe 读到新 comment", tags.get("comment") == after_tags.get("comment"),
              f"{tags.get('comment')} vs {after_tags.get('comment')}")
        check("ffprobe 读到新 creation_time", "creation_time" in tags, tags)

        print("\n== 5. 新建 udta（源文件没有元数据）==")
        clean = tmp / "clean.mp4"
        if make_clean_mp4(clean):
            check("源文件确实没有 ilst", mp4box.read_tags(clean) == {},
                  mp4box.read_tags(clean))
            out2 = tmp / "clean_done.mp4"
            info2 = mp4box.process(clean, out2, seed=5)
            tags2 = mp4box.read_tags(out2)
            check("成功新建并有标签", len(tags2) >= 5, tags2)
            check("MD5 已改变", info2["old_md5"] != info2["new_md5"])
            check("mdat 字节未改动", mdat_bytes(clean) == mdat_bytes(out2))
            p2 = ffprobe_tags(out2)
            t2 = {k.lower(): v for k, v in (p2.get("format") or {}).get("tags", {}).items()}
            check("ffprobe 能读新建的标签", t2.get("title") == tags2.get("title"),
                  f"{t2.get('title')} vs {tags2.get('title')}")
        else:
            print("  [SKIP] 本机无法用 ffmpeg 生成干净样本")

        print("\n== 6. 拒绝不支持的输入 ==")
        for bad in (ROOT / "samples" / "clip_h265.mkv", ROOT / "samples" / "photo_exif.jpg"):
            try:
                mp4box.process(bad, tmp / ("x" + bad.suffix))
                check(f"{bad.suffix} 被拒绝", False, "没有抛异常")
            except mp4box.MP4BoxError:
                check(f"{bad.suffix} 被拒绝", True)

        print("\n== 7. engine 集成：主动选 metabox ==")
        out3 = tmp / "engine_mbx"
        jobs = collect_files([src])
        opts = RunOptions(out_dir=str(out3), image=ImageOptions(),
                          video=VideoOptions(mode="metabox"))
        summary = Engine(opts).run(jobs)
        rec = summary.records[0]
        check("处理成功", rec.ok, rec.error)
        check("走的是 metabox", "免ffmpeg" in (rec.note or ""), rec.note)
        check("输出存在", rec.dst and Path(rec.dst).exists())
        check("哈希已变", rec.old_md5 != rec.new_md5)
        if rec.dst:
            check("输出仍可解码", bool(ffprobe_tags(Path(rec.dst))))

        print("\n== 8. engine 集成：缺 ffmpeg 自动降级 ==")
        out4 = tmp / "engine_fallback"
        jobs = collect_files([src])
        opts = RunOptions(out_dir=str(out4), image=ImageOptions(),
                          video=VideoOptions(mode="light", no_ffmpeg_fallback=True))
        original = videos.tools_available
        videos.tools_available = lambda refresh=False: False   # 假装没装 ffmpeg
        try:
            summary = Engine(opts).run(jobs)
        finally:
            videos.tools_available = original
        rec = summary.records[0]
        check("降级后仍成功", rec.ok, rec.error)
        check("降级标记正确", "免ffmpeg" in (rec.note or ""), rec.note)
        check("输出仍可解码", rec.dst and bool(ffprobe_tags(Path(rec.dst))))

        print("\n== 9. 关闭兜底时应失败（而不是静默降级）==")
        out5 = tmp / "engine_nofallback"
        opts = RunOptions(out_dir=str(out5), image=ImageOptions(),
                          video=VideoOptions(mode="light", no_ffmpeg_fallback=False))
        orig_find = videos.find_bin
        videos.find_bin = lambda name: None            # 彻底假装机器上没有 ffmpeg
        videos.tools_available = lambda refresh=False: False
        try:
            summary = Engine(opts).run(collect_files([src]))
        finally:
            videos.find_bin = orig_find
            videos.tools_available = original
        rec = summary.records[0]
        check("按预期失败", not rec.ok, rec.status)
        check("错误信息可读", bool(rec.error) and ("ffmpeg" in rec.error or "ffprobe" in rec.error),
              rec.error)

        print("\n== 10. 原地覆盖（输出到源目录 + overwrite）==")
        work = tmp / "inplace"
        work.mkdir()
        target = work / "a.mp4"
        shutil.copy(src, target)
        base_md5 = digest_file(target).md5
        opts = RunOptions(out_dir=str(work), collision="overwrite", image=ImageOptions(),
                          video=VideoOptions(mode="metabox"))
        summary = Engine(opts).run(collect_files([work]))
        rec = summary.records[0]
        check("原地处理成功", rec.ok, rec.error)
        check("哈希已变", digest_file(target).md5 != base_md5)
        check("没有残留临时文件",
              not any(".mbx_" in p.name or ".ps_tmp" in p.name for p in work.iterdir()),
              [p.name for p in work.iterdir()])
        check("原位文件仍可解码", bool(ffprobe_tags(target)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== 结果：通过 {PASS} · 失败 {FAIL} =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
