"""PixelShift 启动入口（双击即用）。

    python app.py          启动图形界面
    python app.py -h       查看命令行参数（无界面批处理）

命令行示例：
    python app.py -i D:\\素材 -o D:\\输出 -p strong

打包成 exe 后：双击 exe 直接进界面；把 ffmpeg.exe / ffprobe.exe 放在 exe 同目录，
或同目录的 bin / ffmpeg 子目录里，视频功能即可用。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fix_std_streams() -> None:
    """PyInstaller --windowed 下 sys.stdout/stderr 是 None，print 会直接崩。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass


def _install_crash_handler() -> None:
    """打包成窗口程序后，未捕获异常默认静默退出——写日志并弹窗。"""
    import datetime
    import traceback

    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PixelShift"
    log_file = base / "crash.log"

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            base.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as fh:
                fh.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
                fh.write(text)
        except OSError:
            pass
        try:
            import tkinter.messagebox as mb
            mb.showerror(
                "PixelShift 出错",
                f"{exc_type.__name__}: {exc}\n\n详细信息已写入：\n{log_file}")
        except Exception:                              # noqa: BLE001
            pass

    sys.excepthook = hook


def main() -> None:
    _fix_std_streams()
    _install_crash_handler()

    argv = sys.argv[1:]
    if argv and argv[0] not in ("--gui", "-g"):
        from pixelshift.cli import main as cli_main
        raise SystemExit(cli_main(argv))
    from pixelshift.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
