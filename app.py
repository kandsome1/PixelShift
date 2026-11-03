"""PixelShift 启动入口（双击即用）。

    python app.py          启动图形界面
    python app.py -h       查看命令行参数（无界面批处理）

命令行示例：
    python app.py -i D:\\素材 -o D:\\输出 -p strong
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] not in ("--gui", "-g"):
        from pixelshift.cli import main as cli_main
        raise SystemExit(cli_main(argv))
    from pixelshift.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
