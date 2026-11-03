"""从一张方形源图生成 Windows 多尺寸 ICO（支持圆角 + 透明背景）。

    python tools/make_icon.py                    # 默认圆角半径 22%
    python tools/make_icon.py --radius 0         # 方角
    python tools/make_icon.py --radius 0.25 --src assets/icon_flat.png

默认读 assets/icon_flat.png（AI 出的原始方角图），套上圆角后：
    assets/icon_rounded.png   圆角透明预览图（1024×1024）
    assets/app.ico            7 个尺寸的图标

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

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
DEFAULT_RADIUS = 0.22          # 圆角半径 = 边长的 22%
SUPERSAMPLE = 4                # 蒙版超采样倍数，保证圆角边缘平滑


def round_corners(img: Image.Image, ratio: float) -> Image.Image:
    """把方形图裁成圆角，四角透明。ratio = 半径 / 边长。"""
    img = img.convert("RGBA")
    w, h = img.size
    if ratio <= 0:
        return img
    r = int(min(w, h) * min(ratio, 0.5))
    big = (w * SUPERSAMPLE, h * SUPERSAMPLE)
    mask = Image.new("L", big, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, big[0] - 1, big[1] - 1], radius=r * SUPERSAMPLE, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)      # 缩回原尺寸时自带抗锯齿
    out = img.copy()
    out.putalpha(mask)
    return out


def build(src: Path, dst: Path, radius: float, preview: Path | None = None) -> None:
    if not src.exists():
        raise SystemExit(f"找不到源图：{src}")

    with Image.open(src) as im:
        img = im.convert("RGBA")

    if img.width != img.height:                   # 非正方形先居中裁成正方形
        side = min(img.size)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
        print(f"源图非正方形，已居中裁成 {side}x{side}")

    img = round_corners(img, radius)
    print(f"圆角半径：{radius:.0%}  →  {int(img.width * radius)} px (@{img.width})")

    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="ICO", sizes=SIZES)
    print(f"已生成 {dst.relative_to(ROOT)}  ({dst.stat().st_size / 1024:.1f} KB)")

    if preview is not None:
        img.resize((1024, 1024), Image.LANCZOS).save(preview)
        print(f"已生成 {preview.relative_to(ROOT)}")

    with Image.open(dst) as chk:
        got = sorted(chk.ico.sizes())
    print(f"包含尺寸: {', '.join(f'{w}x{h}' for w, h in got)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 Windows 多尺寸 ICO")
    ap.add_argument("--src", default=str(ASSETS / "icon_flat.png"), help="源图（方形 PNG）")
    ap.add_argument("--dst", default=str(ASSETS / "app.ico"), help="输出 ICO")
    ap.add_argument("--radius", type=float, default=DEFAULT_RADIUS,
                    help="圆角半径占边长的比例，0 = 方角（默认 0.22）")
    ap.add_argument("--no-preview", action="store_true", help="不生成圆角预览图")
    args = ap.parse_args()

    preview = None if args.no_preview else ASSETS / "icon_rounded.png"
    build(Path(args.src), Path(args.dst), args.radius, preview)


if __name__ == "__main__":
    main()
