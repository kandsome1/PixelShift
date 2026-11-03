"""视频管线：基于 ffmpeg 的三种强度（重封装 / 轻度重编码 / 深度变形）。

remux  : -c copy，只重写容器与元数据 —— 秒级完成，画面零损失。
light  : 视频重编码（可带轻微噪声滤镜），音频直接拷贝。
deep   : 缩放往返 + 噪声 + 色调 + 关键帧重排 + 音频重编码，指纹差异最大。
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import random_fp
from .config import VideoOptions

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class VideoError(RuntimeError):
    pass


# ------------------------------------------------------------------ 二进制探测
def find_bin(name: str) -> str | None:
    """找 ffmpeg/ffprobe：先 PATH，再常见安装目录。"""
    exe = name + (".exe" if os.name == "nt" else "")
    p = shutil.which(name) or shutil.which(exe)
    if p:
        return p
    candidates = [
        Path(r"C:\Program Files\ffmpeg\bin") / exe,
        Path(r"C:\ffmpeg\bin") / exe,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ffmpeg" / "bin" / exe,
        Path("/usr/bin") / name,
        Path("/usr/local/bin") / name,
        Path("/opt/homebrew/bin") / name,
    ]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def ffmpeg_path() -> str:
    p = find_bin("ffmpeg")
    if not p:
        raise VideoError("未找到 ffmpeg，可在设置里手动指定路径")
    return p


def ffprobe_path() -> str:
    p = find_bin("ffprobe")
    if not p:
        raise VideoError("未找到 ffprobe，可在设置里手动指定路径")
    return p


def check_tools() -> tuple[bool, str]:
    try:
        ff = ffmpeg_path()
        fp = ffprobe_path()
        return True, f"ffmpeg: {ff}\nffprobe: {fp}"
    except VideoError as exc:
        return False, str(exc)


# ------------------------------------------------------------------ 探测
@dataclass
class ProbeInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    vcodec: str = ""
    acodec: str = ""
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    has_sub: bool = False
    format_name: str = ""
    bitrate: int = 0

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


def probe(path: str | Path) -> ProbeInfo:
    cmd = [ffprobe_path(), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60,
                             creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoError(f"ffprobe 执行失败: {exc}") from exc
    if out.returncode != 0:
        msg = (out.stderr or b"").decode("utf-8", "replace").strip()
        raise VideoError(f"ffprobe 无法解析: {msg or path}")
    try:
        data = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise VideoError(f"ffprobe 输出异常: {exc}") from exc

    info = ProbeInfo()
    fmt = data.get("format") or {}
    info.format_name = fmt.get("format_name", "")
    try:
        info.duration = float(fmt.get("duration") or 0)
    except ValueError:
        info.duration = 0.0
    try:
        info.bitrate = int(fmt.get("bit_rate") or 0)
    except ValueError:
        info.bitrate = 0

    for st in data.get("streams") or []:
        ctype = st.get("codec_type")
        if ctype == "video" and not info.has_video:
            # 跳过封面图（mjpeg/png 且 attached_pic）
            if (st.get("disposition") or {}).get("attached_pic"):
                continue
            info.has_video = True
            info.vcodec = st.get("codec_name", "")
            info.width = int(st.get("width") or 0)
            info.height = int(st.get("height") or 0)
            rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1"
            try:
                num, _, den = rate.partition("/")
                info.fps = float(num) / float(den or 1) if float(den or 1) else 0.0
            except (ValueError, ZeroDivisionError):
                info.fps = 0.0
        elif ctype == "audio":
            info.has_audio = True
            if not info.acodec:
                info.acodec = st.get("codec_name", "")
        elif ctype == "subtitle":
            info.has_sub = True
    if not info.has_video:
        raise VideoError("文件里没有可处理的视频流")
    return info


# ------------------------------------------------------------------ ffmpeg 执行
@dataclass
class FFResult:
    code: int = 0
    log: str = ""
    cancelled: bool = False


def _run_ffmpeg(args: list[str], total: float = 0.0, on_progress=None,
                cancel: threading.Event | None = None, timeout: float | None = None) -> FFResult:
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-stats_period", "0.5", *args]
    log: deque[str] = deque(maxlen=60)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise VideoError(f"无法启动 ffmpeg: {exc}") from exc

    cancelled = False
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if cancel is not None and cancel.is_set() and not cancelled:
                cancelled = True
                try:
                    proc.terminate()
                except OSError:
                    pass
            if not line:
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                if key == "out_time_us" and on_progress and total > 0:
                    try:
                        sec = int(val) / 1_000_000.0
                        on_progress(min(1.0, max(0.0, sec / total)))
                    except ValueError:
                        pass
                elif key in ("out_time_ms", "out_time") and on_progress and total > 0:
                    try:
                        if key == "out_time":
                            hh, mm, ss = val.split(":")
                            sec = int(hh) * 3600 + int(mm) * 60 + float(ss)
                        else:
                            sec = int(val) / 1_000_000.0
                        on_progress(min(1.0, max(0.0, sec / total)))
                    except (ValueError, IndexError):
                        pass
                continue
            log.append(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise VideoError("ffmpeg 超时")
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except OSError:
            pass
    if cancelled:
        return FFResult(code=proc.returncode or -1, log="\n".join(log), cancelled=True)
    return FFResult(code=proc.returncode, log="\n".join(log))


# ------------------------------------------------------------------ 命令构建
_CONTAINER_EXT = {"mp4": ".mp4", "mkv": ".mkv", "mov": ".mov"}


def target_container(opt: VideoOptions, src_path: Path) -> tuple[str, str]:
    """返回 (容器名, 扩展名)。"""
    if opt.container == "keep":
        ext = src_path.suffix.lower()
        name = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "mkv"}.get(ext, "mkv")
        return name, (ext if ext in (".mp4", ".m4v", ".mov", ".mkv") else ".mkv")
    return opt.container, _CONTAINER_EXT[opt.container]


def needs_reencode(opt: VideoOptions) -> bool:
    """结构变形（镜像/裁切/变速）无法在 -c copy 下完成，必须重编码。"""
    return bool(opt.mirror) or opt.crop_percent > 0 or abs(opt.speed - 1.0) > 1e-6


def build_filter(opt: VideoOptions, pr: ProbeInfo, rng: random.Random,
                 mode: str | None = None) -> str:
    mode = mode or opt.mode
    parts: list[str] = []

    # —— 结构变形（与滤镜等级无关，勾了就生效）
    if opt.mirror:
        parts.append("hflip")
    if opt.crop_percent > 0:
        k = min(6.0, max(0.2, opt.crop_percent)) / 100.0
        cw = max(16, int(pr.width * (1 - k)) // 2 * 2)
        ch = max(16, int(pr.height * (1 - k)) // 2 * 2)
        parts.append(f"crop={cw}:{ch}")
        parts.append(f"scale={pr.width}:{pr.height}:flags=lanczos")

    # —— 强度滤镜
    level = max(0, min(3, opt.filter_level))
    if mode != "remux" and level >= 3 and pr.width > 64 and pr.height > 64:
        w4 = max(64, (pr.width - 4) // 2 * 2)
        h4 = max(64, (pr.height - 4) // 2 * 2)
        parts.append(f"scale={w4}:{h4}:flags=lanczos")
        parts.append(f"scale={pr.width}:{pr.height}:flags=bicubic")
        parts.append(f"noise=alls={rng.randint(10, 16)}:allf=t+u")
        parts.append(f"eq=brightness={rng.uniform(0.008, 0.016):.4f}"
                     f":contrast={rng.uniform(1.008, 1.018):.4f}"
                     f":saturation={rng.uniform(1.012, 1.03):.4f}")
        parts.append("unsharp=3:3:0.15")
    elif mode != "remux" and level == 2:
        parts.append(f"noise=alls={rng.randint(6, 9)}:allf=t+u")
        parts.append(f"eq=brightness={rng.uniform(0.005, 0.010):.4f}"
                     f":contrast={rng.uniform(1.005, 1.012):.4f}"
                     f":saturation={rng.uniform(1.006, 1.015):.4f}")
    elif mode != "remux" and level == 1:
        parts.append(f"noise=alls={rng.randint(2, 4)}:allf=t")

    # —— 变速放到最后
    if abs(opt.speed - 1.0) > 1e-6:
        parts.append(f"setpts=PTS/{opt.speed:.5f}")
    return ",".join(parts)


def _meta_args(opt: VideoOptions, rng: random.Random, container: str,
               has_audio: bool, has_sub: bool) -> list[str]:
    args: list[str] = []
    if opt.strip_metadata:
        args += ["-map_metadata", "-1", "-map_chapters", "-1"]
    if opt.inject_metadata:
        md = random_fp.video_metadata(rng)
        args += ["-metadata", f"comment={md['comment']}"]
        args += ["-metadata", f"title={md['title']}"]
        if container in ("mp4", "mov"):
            args += ["-metadata", "creation_time=" + md["creation_time"]]
        args += ["-metadata", "encoder=" + md["encoder"]]
        args += ["-metadata:s:v:0", "handler_name=" + md["handler_name"]]
        if has_audio:
            audio_h = random_fp.video_metadata(rng)["handler_name"]
            args += ["-metadata:s:a:0", "handler_name=" + audio_h]
        if has_sub:
            args += ["-metadata:s:s:0", "handler_name=SubtitleHandler"]
    return args


def build_command(src: Path, dst: Path, opt: VideoOptions, pr: ProbeInfo,
                  rng: random.Random, mode: str | None = None) -> list[str]:
    mode = mode or opt.mode
    container, _ = target_container(opt, src)
    args: list[str] = ["-i", str(src)]

    if mode == "remux":
        args += ["-map", "0:v?"]
        if pr.has_audio:
            args += ["-map", "0:a?"]
        if pr.has_sub and opt.copy_other_streams and container == "mkv":
            args += ["-map", "0:s?"]
        args += ["-c", "copy"]
    else:
        args += ["-map", "0:v?"]
        if pr.has_audio:
            args += ["-map", "0:a?"]
        if pr.has_sub and opt.copy_other_streams:
            args += ["-map", "0:s?"]

        vf = build_filter(opt, pr, rng, mode)
        args += ["-c:v", "libx264", "-preset", opt.preset,
                 "-crf", str(max(16, min(32, opt.crf))), "-pix_fmt", "yuv420p"]
        keyint = opt.keyint if opt.keyint > 0 else rng.randint(200, 300)
        args += ["-g", str(keyint), "-bf", "3", "-profile:v", "high"]
        if vf:
            args += ["-vf", vf]

        speed = opt.speed if abs(opt.speed - 1.0) > 1e-6 else 1.0
        if pr.has_audio:
            if opt.audio_mode == "reencode" or speed != 1.0:
                args += ["-c:a", "aac", "-b:a", opt.audio_bitrate]
                if speed != 1.0:
                    args += ["-af", f"atempo={max(0.5, min(2.0, speed)):.5f}"]
            else:
                args += ["-c:a", "copy"]
        if pr.has_sub and opt.copy_other_streams:
            args += ["-c:s", "copy"] if container == "mkv" else ["-c:s", "mov_text"]

    args += _meta_args(opt, rng, container, pr.has_audio, pr.has_sub)

    if container in ("mp4", "mov"):
        args += ["-video_track_timescale", "90000"]
        args += ["-movflags", "+faststart" if opt.faststart else "-faststart"]
    args += ["-max_muxing_queue_size", "2048"]
    args.append(str(dst))
    return args


# ------------------------------------------------------------------ 主流程
def process_video(src: str | Path, dst: str | Path, opt: VideoOptions,
                  seed: int | None = None, on_progress=None,
                  cancel: threading.Event | None = None) -> dict:
    src, dst = Path(src), Path(dst)
    rng = random.Random(seed)
    info: dict = {"src": str(src), "dst": str(dst), "kind": "video"}

    pr = probe(src)
    info.update(duration=round(pr.duration, 2), width=pr.width, height=pr.height,
                vcodec=pr.vcodec, acodec=pr.acodec, fps=round(pr.fps, 3),
                has_audio=pr.has_audio, has_sub=pr.has_sub)

    dst.parent.mkdir(parents=True, exist_ok=True)
    mode = opt.mode
    if mode == "remux" and needs_reencode(opt):
        mode = "light"
        info["note"] = "结构变形需重编码，已自动升级为 light"
    cmd = build_command(src, dst, opt, pr, rng, mode)
    info["engine_mode"] = mode

    def _progress(frac: float) -> None:
        if on_progress:
            on_progress(frac)

    res = _run_ffmpeg(cmd, total=pr.duration, on_progress=_progress, cancel=cancel)
    if res.cancelled:
        _safe_unlink(dst)
        raise VideoError("已取消")
    if res.code != 0 or not dst.exists() or dst.stat().st_size == 0:
        # 首次失败常见于字幕/多轨不兼容，去掉多余流再试一次
        retry = [a for a in cmd]
        if "-map" in retry and opt.copy_other_streams:
            info["note"] = (info.get("note", "") + " 首次失败已改为仅主音视频流重试").strip()
            retry = _simplify_command(cmd, dst)
            res2 = _run_ffmpeg(retry, total=pr.duration, on_progress=_progress, cancel=cancel)
            if res2.code != 0 or not dst.exists() or dst.stat().st_size == 0:
                _safe_unlink(dst)
                raise VideoError(_friendly_error(res.log or res2.log))
        else:
            _safe_unlink(dst)
            raise VideoError(_friendly_error(res.log))

    try:
        out_pr = probe(dst)
        info.update(out_duration=round(out_pr.duration, 2),
                    out_width=out_pr.width, out_height=out_pr.height,
                    out_vcodec=out_pr.vcodec)
    except VideoError as exc:
        info["verify_error"] = str(exc)

    from .hasher import digest_file
    old = digest_file(src)
    new = digest_file(dst)
    info.update(old_size=old.size, new_size=new.size,
                old_md5=old.md5, new_md5=new.md5, changed=old.md5 != new.md5)
    if on_progress:
        on_progress(1.0)
    return info


def _simplify_command(cmd: list[str], dst: Path) -> list[str]:
    """去掉 -map 0:s? 与 -c:s，仅保留视频 + 音频。"""
    out: list[str] = []
    skip_next = False
    for i, tok in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        if tok == "-map" and i + 1 < len(cmd) and ":s?" in cmd[i + 1]:
            skip_next = True
            continue
        if tok == "-c:s":
            skip_next = True
            continue
        out.append(tok)
    return out


def _friendly_error(log: str) -> str:
    text = (log or "").strip()
    if not text:
        return "ffmpeg 返回失败但没有输出错误信息"
    tail = text.splitlines()[-6:]
    return "ffmpeg: " + " | ".join(t.strip() for t in tail if t.strip())


def _safe_unlink(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass
