"""从一张方形源图生成 Windows 多尺寸 ICO。

    python tools/make_icon.py [源图路径]

默认读 assets/icon_source.png，输出 assets/app.ico（7 个尺寸）。
Windows 各场景用到的尺寸：
    16  资源管理器列表 / 小图标视图
    24  任务栏小图标
    32  Alt+Tab、桌面中等图标
    48  开始菜单、中等图标
    64  大图标视图
    128 特大图标
    256 应用快捷方式、商店展示（ICO 内用 PNG 压缩存储）
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]


def build(src: Path, dst: Path) -> None:
    if not src.exists():
        raise SystemExit(f"找不到源图：{src}")
    with Image.open(src) as im:
        img = im.convert("RGBA")
    if img.width != img.height:
        side = min(img.size)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
        print(f"源图非正方形，已居中裁成 {side}x{side}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="ICO", sizes=SIZES)
    size_kb = dst.stat().st_size / 1024
    print(f"已生成 {dst.relative_to(ROOT)}  ({size_kb:.1f} KB)")

    with Image.open(dst) as chk:
        got = sorted(chk.ico.sizes())
    print(f"包含尺寸: {', '.join(f'{w}x{h}' for w, h in got)}")


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ASSETS / "icon_source.png"
    build(source, ASSETS / "app.ico")
