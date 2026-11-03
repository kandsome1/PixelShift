"""开发自检：实例化 GUI、填几个文件、截图，用于人工/接口核对布局。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from pixelshift.gui import PixelShiftApp  # noqa: E402


def pump(app, times: int = 40, delay: float = 0.03) -> None:
    for _ in range(times):
        app.update_idletasks()
        app.update()
        time.sleep(delay)


def shot(app, name: str) -> Path:
    pump(app)
    app.lift()
    app.attributes("-topmost", True)
    pump(app, 12)
    full = ImageGrab.grab()                       # 物理像素全屏
    scale = full.size[0] / app.winfo_screenwidth()  # DPI 缩放比（逻辑 → 物理）
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    box = (int(x * scale), int(y * scale), int((x + w) * scale), int((y + h) * scale))
    out = ROOT / name
    full.crop(box).save(out)
    app.attributes("-topmost", False)
    print("saved", out, f"{w}x{h} scale={scale:.2f}")
    return out


def main() -> None:
    app = PixelShiftApp()
    app.geometry("1320x880+40+40")
    pump(app)
    app._add_paths([ROOT / "samples"])
    pump(app, 60)
    shot(app, "shot_main.png")

    # 切到视频页
    app.nb.select(1)
    shot(app, "shot_video.png")
    # 切到输出页
    app.nb.select(2)
    shot(app, "shot_run.png")
    app.nb.select(0)

    # 跑一次真实处理
    app.v_out_dir.set(str(ROOT / "out_gui"))
    app.start()
    for _ in range(400):
        pump(app, 4)
        if not app._running:
            break
    pump(app, 20)
    shot(app, "shot_done.png")
    print("status:", app.lbl_stat.cget("text"))
    app.destroy()


if __name__ == "__main__":
    main()
