"""命令行入口（无界面批处理，也用于自检）。

    python -m pixelshift.cli -i 输入路径 -o 输出目录 -p medium
    python -m pixelshift.cli -i a.jpg b.mp4 --preset strong --verbose
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .config import PRESETS, RunOptions, preset_options
from .engine import Engine, collect_files
from .hasher import human_size
from .videos import check_tools


def build_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="pixelshift",
        description="PixelShift —— 批量/单个改写图片与视频的特征码（文件哈希 + 感知指纹）")
    ap.add_argument("-i", "--input", nargs="+", required=True, help="文件或目录，可多个")
    ap.add_argument("-o", "--out", default="", help="输出目录；留空则输出到源目录 _pixelshift 下")
    ap.add_argument("-p", "--preset", default="medium", choices=list(PRESETS),
                    help="强度预设：light / medium / strong")
    ap.add_argument("--only", default="all", choices=["all", "image", "video"])
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--video-workers", type=int, default=2)
    ap.add_argument("--no-recursive", action="store_true", help="目录只处理第一层")
    ap.add_argument("--random-name", action="store_true", help="文件名追加随机后缀")
    ap.add_argument("--overwrite", action="store_true", help="同名覆盖而不是改名")
    ap.add_argument("--seed", type=int, default=None, help="固定随机种子（可复现）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要处理的文件")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"PixelShift {__version__}")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = build_args(argv)
    img_opt, vid_opt = preset_options(args.preset)
    opts = RunOptions(
        out_dir=args.out,
        collision="overwrite" if args.overwrite else "suffix",
        random_name=args.random_name,
        random_seed=args.seed,
        concurrency=args.concurrency,
        video_workers=args.video_workers,
        image=img_opt,
        video=vid_opt,
    )

    jobs = collect_files(args.input, recursive=not args.no_recursive)
    if args.only != "all":
        jobs = [j for j in jobs if j.kind == args.only]
    if not jobs:
        print("没有匹配的文件")
        return 1

    print(f"PixelShift {__version__} · 预设 {PRESETS[args.preset].name} · 共 {len(jobs)} 个文件")
    ok, msg = check_tools()
    if any(j.kind == "video" for j in jobs):
        print(("[ffmpeg OK] " if ok else "[ffmpeg 缺失] ") + msg.splitlines()[0])

    if args.dry_run:
        for j in jobs:
            print(f"  {j.kind:5} {j.src}")
        return 0

    def on_event(kind: str, payload) -> None:
        if kind == "item_done":
            idx, rec, done, total = payload
            flag = {"ok": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(rec.status, rec.status)
            line = f"[{done}/{total}] {flag} {rec.src.name} → {rec.dst.name if rec.dst else '-'}"
            if rec.ok:
                line += f"  {rec.old_md5[:8]}→{rec.new_md5[:8]}"
                if rec.phash_dist is not None:
                    line += f" phashΔ{rec.phash_dist}"
                line += f" {rec.seconds:.1f}s"
            elif rec.error:
                line += f"  {rec.error}"
            print(line, flush=True)
        elif kind == "log" and args.verbose:
            print(payload[0], flush=True)

    engine = Engine(opts, on_event)
    t0 = time.perf_counter()
    summary = engine.run(jobs)
    print("\n" + summary.text())
    if summary.report_path:
        print(f"报告: {summary.report_path}")
    print(f"总耗时 {time.perf_counter() - t0:.1f}s")
    return 0 if summary.failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
