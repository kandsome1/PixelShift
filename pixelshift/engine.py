"""任务引擎：批量 / 单文件、输出路径规划、并发调度、结果汇总与报告。"""
from __future__ import annotations

import csv
import datetime as _dt
import os
import random
import threading
import time
import traceback
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import images, metainject, mp4box, videos
from .config import IMAGE_EXTS, RunOptions, kind_of
from .hasher import digest_file, hash_distance, human_size, perceptual_hash, unique_path

IMAGE_OUT_EXT = {"keep": None, "jpg": ".jpg", "png": ".png", "webp": ".webp",
                 "bmp": ".bmp", "tiff": ".tiff"}


# ------------------------------------------------------------------ 数据模型
@dataclass
class Job:
    src: Path
    base: Path                      # 添加时的根目录，用于镜像层级
    kind: str = ""                  # image / video / other
    order: int = 0

    def __post_init__(self) -> None:
        self.src = Path(self.src)
        self.base = Path(self.base) if self.base else self.src.parent
        self.kind = self.kind or kind_of(str(self.src))


@dataclass
class Record:
    src: Path
    dst: Path | None = None
    kind: str = ""
    status: str = "ok"              # ok / failed / skipped / cancelled
    error: str = ""
    note: str = ""
    old_md5: str = ""
    new_md5: str = ""
    old_size: int = 0
    new_size: int = 0
    phash_old: str = ""
    phash_new: str = ""
    phash_dist: int | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def row(self) -> list[str]:
        return [
            str(self.src), str(self.dst or ""), self.kind, self.status,
            self.old_md5, self.new_md5,
            str(self.old_size), str(self.new_size), f"{self.seconds:.2f}",
            (str(self.phash_dist) if self.phash_dist is not None else ""),
            self.error or self.note,
        ]


CSV_HEADER = ["源文件", "输出文件", "类型", "状态", "原MD5", "新MD5",
              "原大小", "新大小", "耗时秒", "感知哈希距离", "备注/错误"]


@dataclass
class Summary:
    total: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    seconds: float = 0.0
    records: list[Record] = field(default_factory=list)
    report_path: Path | None = None

    def text(self) -> str:
        parts = [f"共 {self.total} 个", f"成功 {self.ok}"]
        if self.failed:
            parts.append(f"失败 {self.failed}")
        if self.skipped:
            parts.append(f"跳过 {self.skipped}")
        if self.cancelled:
            parts.append(f"取消 {self.cancelled}")
        parts.append(f"耗时 {self.seconds:.1f}s")
        if self.bytes_in:
            delta = self.bytes_out - self.bytes_in
            parts.append(f"体积 {human_size(self.bytes_in)} → {human_size(self.bytes_out)}"
                         f" ({delta / self.bytes_in * 100:+.1f}%)")
        return " · ".join(parts)


# ------------------------------------------------------------------ 引擎
class Engine:
    """on_event(kind, payload) 会在工作线程里被调用，GUI 需自行转到主线程。"""

    def __init__(self, opts: RunOptions, on_event=None):
        self.opts = opts
        self.on_event = on_event or (lambda kind, payload: None)
        self.cancel = threading.Event()
        self._video_sem = threading.Semaphore(max(1, opts.video_workers))
        self._dst_lock = threading.Lock()

    # ---------------------------------------------------------- 事件
    def _emit(self, kind: str, payload=None) -> None:
        try:
            self.on_event(kind, payload)
        except Exception:
            pass

    def stop(self) -> None:
        self.cancel.set()

    # ---------------------------------------------------------- 输出路径
    def _out_root(self, job: Job) -> Path:
        if self.opts.out_dir.strip():
            return Path(self.opts.out_dir).expanduser()
        return job.base / "_pixelshift"

    def _dst_for(self, job: Job, ext: str) -> Path:
        root = self._out_root(job)
        if self.opts.mirror_structure:
            try:
                rel = job.src.parent.relative_to(job.base)
            except ValueError:
                rel = Path()
            target_dir = root / rel if str(rel) not in ("", ".") else root
        else:
            target_dir = root
        stem = job.src.stem
        if self.opts.random_name:
            from .random_fp import random_suffix
            stem = f"{stem}_{random_suffix(4)}"
        with self._dst_lock:
            target = target_dir / f"{stem}{ext}"
            if self.opts.collision == "overwrite":
                return target
            if self.opts.collision == "skip" and target.exists():
                return target
            return unique_path(target)

    def _predict_ext(self, job: Job, rng: random.Random) -> str:
        if job.kind == "image":
            if self.opts.image.mode == "metaonly":
                # 只注入元数据：必须原样保留容器格式
                return job.src.suffix.lower() or ".jpg"
            fixed = IMAGE_OUT_EXT.get(self.opts.image.out_format)
            if fixed:
                return fixed
            return job.src.suffix.lower() or ".png"
        if self.opts.video.mode == "metabox":
            # 纯容器改写不能换容器
            return job.src.suffix.lower() or ".mp4"
        container, ext = videos.target_container(self.opts.video, job.src)
        return ext

    def _use_metabox(self, job: Job) -> bool:
        """该视频走不走"纯 Python 改元数据"这条路。"""
        if self.opts.video.mode == "metabox":
            return True
        if not self.opts.video.no_ffmpeg_fallback:
            return False
        return not videos.tools_available() and mp4box.supported(job.src)

    # ---------------------------------------------------------- 单任务
    def run_one(self, job: Job, index: int, total: int) -> Record:
        rec = Record(src=job.src, kind=job.kind)
        if self.cancel.is_set():
            rec.status = "cancelled"
            return rec
        if job.kind == "other":
            rec.status = "skipped"
            rec.error = "不支持的扩展名"
            return rec
        if self.opts.collision == "skip":
            ext = self._predict_ext(job, random.Random())
            if self._dst_for(job, ext).exists():
                rec.status = "skipped"
                rec.error = "目标已存在（跳过模式）"
                return rec

        seed = None
        if self.opts.random_seed is not None:
            seed = self.opts.random_seed + index
        changed_note = []
        t0 = time.perf_counter()
        try:
            rec.phash_old = perceptual_hash(job.src)[0] if job.kind == "image" else ""

            if job.kind == "image":
                ext = self._predict_ext(job, random.Random(seed))
                dst = self._dst_for(job, ext)
                use_metaonly = (self.opts.image.mode == "metaonly"
                                and metainject.supported(job.src))
                if use_metaonly:
                    info = metainject.process(job.src, dst, self.opts.image, seed=seed)
                    changed_note.append("仅注入元数据")
                else:
                    info = images.process_image(job.src, dst, self.opts.image, seed=seed)
                    if self.opts.image.mode == "metaonly":
                        changed_note.append(f"{job.src.suffix} 无元数据区，已改用重编码")
                    if info.get("mirror"):
                        changed_note.append("镜像")
                    if info.get("precise_mode"):
                        changed_note.append(f"{info['precise_mode']} 高精度模式：只改元数据")
                    if info.get("mode_note"):
                        changed_note.append(info["mode_note"])
                    if info.get("note"):
                        changed_note.append(info["note"])
                    if self.opts.image.inject_metadata:
                        changed_note.append("元数据已重构")
                rec.dst = Path(info["dst"])
            else:
                ext = self._predict_ext(job, random.Random(seed))
                dst = self._dst_for(job, ext)
                tmp_dst, restore = self._temp_target(job, dst)
                use_metabox = self._use_metabox(job)
                if use_metabox and not mp4box.supported(job.src):
                    rec.status = "skipped"
                    rec.error = f"{job.src.suffix} 不是 MP4/MOV 容器，metabox 模式无法处理"
                    return rec

                def prog(frac: float) -> None:
                    self._emit("progress", (index, frac))

                with self._video_sem:
                    if self.cancel.is_set():
                        rec.status = "cancelled"
                        return rec
                    if use_metabox:
                        if not mp4box.supported(job.src):
                            raise mp4box.MP4BoxError(
                                f"缺少 ffmpeg，且 {job.src.suffix} 不是 MP4/MOV 容器，无法降级")
                        info = mp4box.process(
                            job.src, tmp_dst, seed=seed,
                            patch_mdhd=self.opts.video.metabox_patch_mdhd)
                        self._emit("progress", (index, 1.0))
                    else:
                        info = videos.process_video(job.src, tmp_dst, self.opts.video,
                                                    seed=seed, on_progress=prog,
                                                    cancel=self.cancel)
                if restore:
                    tmp_dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(tmp_dst, dst)
                rec.dst = dst
                if use_metabox:
                    changed_note.append("免ffmpeg·容器元数据重建")
                else:
                    if info.get("note"):
                        changed_note.append(info["note"])
                    changed_note.append(f"{info.get('engine_mode')} {info.get('out_width')}x"
                                        f"{info.get('out_height')}")

            rec.old_size = info.get("old_size", 0)
            rec.new_size = info.get("new_size", 0)
            rec.old_md5 = info.get("old_md5", "")
            rec.new_md5 = info.get("new_md5", "")
            rec.status = "ok"
            rec.note = "; ".join(changed_note)
            if rec.old_md5 and rec.new_md5 and rec.old_md5 == rec.new_md5:
                rec.note = (rec.note + "; 警告：哈希未变化").strip("; ")

            if job.kind == "image" and rec.dst:
                rec.phash_new = perceptual_hash(rec.dst)[0]
                rec.phash_dist = hash_distance(rec.phash_old, rec.phash_new)
        except Exception as exc:                      # noqa: BLE001
            rec.status = "failed"
            msg = str(exc) or exc.__class__.__name__
            rec.error = msg
            rec.note = ""
            self._emit("log", (f"[失败] {job.src.name}: {msg}", "error"))
            self._emit("debug", traceback.format_exc())
            if rec.dst and rec.dst.exists() and self.opts.collision != "overwrite":
                try:
                    rec.dst.unlink()
                except OSError:
                    pass
            rec.dst = None
        finally:
            rec.seconds = time.perf_counter() - t0
        return rec

    def _temp_target(self, job: Job, dst: Path) -> tuple[Path, bool]:
        """若输出会覆盖源文件，先写临时文件再替换（ffmpeg 不能直接读写同一文件）。"""
        try:
            same = dst.exists() and os.path.samefile(dst, job.src)
        except OSError:
            same = False
        if not same:
            return dst, False
        tmp = dst.with_name(f".{dst.stem}.ps_tmp{dst.suffix}")
        return tmp, True

    # ---------------------------------------------------------- 批量
    def run(self, jobs: list[Job]) -> Summary:
        t0 = time.perf_counter()
        total = len(jobs)
        summary = Summary(total=total)
        self._emit("start", total)
        if total == 0:
            summary.seconds = 0.0
            self._emit("done", summary)
            return summary

        workers = max(1, min(self.opts.concurrency, 16))
        results: dict[int, Record] = {}
        done_count = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ps") as pool:
            futures = {pool.submit(self.run_one, job, idx, total): idx
                       for idx, job in enumerate(jobs)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    rec = fut.result()
                except CancelledError:
                    rec = Record(src=jobs[idx].src, kind=jobs[idx].kind,
                                 status="cancelled", error="已取消")
                except Exception as exc:              # noqa: BLE001
                    rec = Record(src=jobs[idx].src, kind=jobs[idx].kind,
                                 status="failed", error=str(exc))
                results[idx] = rec
                done_count += 1
                if rec.ok:
                    summary.ok += 1
                elif rec.status == "failed":
                    summary.failed += 1
                elif rec.status == "skipped":
                    summary.skipped += 1
                else:
                    summary.cancelled += 1
                summary.bytes_in += rec.old_size
                summary.bytes_out += rec.new_size
                self._emit("progress", (idx, 1.0))
                self._emit("item_done", (idx, rec, done_count, total))
                if self.cancel.is_set():
                    for f in futures:
                        f.cancel()

        summary.records = [results[i] for i in sorted(results)]
        summary.seconds = time.perf_counter() - t0
        summary.report_path = self.write_report(summary)
        self._emit("done", summary)
        return summary

    # ---------------------------------------------------------- 报告
    def write_report(self, summary: Summary) -> Path | None:
        if not summary.records:
            return None
        first = summary.records[0]
        try:
            root = self._out_root(Job(first.src, first.src.parent, first.kind))
        except Exception:
            root = Path(first.src).parent
        try:
            root.mkdir(parents=True, exist_ok=True)
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = unique_path(root / f"PixelShift_报告_{stamp}.csv")
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(CSV_HEADER)
                for rec in summary.records:
                    w.writerow(rec.row())
            return path
        except OSError:
            return None


# ------------------------------------------------------------------ 收集文件
def collect_files(paths: list[str | Path], recursive: bool = True,
                  exts: tuple[str, ...] | None = None) -> list[Job]:
    """把若干文件/目录展开成 Job 列表（去重、按自然顺序）。"""
    from .config import SUPPORTED_EXTS
    exts = exts or SUPPORTED_EXTS
    jobs: list[Job] = []
    seen: set[str] = set()

    def _add(p: Path, base: Path) -> None:
        key = str(p.resolve()).lower()
        if key in seen:
            return
        if p.suffix.lower() not in exts:
            return
        seen.add(key)
        jobs.append(Job(p, base))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            base = p
            it = p.rglob("*") if recursive else p.glob("*")
            for sub in sorted(it, key=lambda x: str(x).lower()):
                if sub.is_file():
                    _add(sub, base)
        elif p.is_file():
            _add(p, p.parent)
    for i, j in enumerate(jobs):
        j.order = i
    return jobs
