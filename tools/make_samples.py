"""生成自测素材（图片 + 视频），只在开发验证时用。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent / "samples"


def make_images(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # 1) 带 EXIF 的 JPEG
    im = Image.new("RGB", (1280, 720))
    d = ImageDraw.Draw(im)
    for y in range(720):
        d.line([(0, y), (1280, y)], fill=(30 + y // 6, 90 + y // 9, 160 - y // 12))
    d.ellipse((400, 180, 880, 560), fill=(250, 220, 120))
    d.rectangle((0, 600, 1280, 720), fill=(20, 40, 30))
    exif = Image.Exif()
    exif[271] = "Canon"
    exif[272] = "Canon EOS 5D Mark IV"
    exif[305] = "Adobe Photoshop 25.3"
    im.save(out / "photo_exif.jpg", quality=92, exif=exif.tobytes())
    # 2) 普通 JPEG
    im.filter(ImageFilter.GaussianBlur(0.6)).save(out / "photo_plain.jpg", quality=88)
    # 3) PNG 带 alpha
    png = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    dp = ImageDraw.Draw(png)
    dp.polygon([(400, 40), (760, 400), (400, 760), (40, 400)], fill=(40, 180, 230, 220))
    png.save(out / "graphic_alpha.png")
    # 4) 动画 GIF
    frames = []
    for i in range(8):
        f = Image.new("RGB", (320, 320), (i * 30 % 255, 80, 200 - i * 20))
        ImageDraw.Draw(f).text((120, 150), f"F{i}", fill=(255, 255, 255))
        frames.append(f)
    frames[0].save(out / "anim.gif", save_all=True, append_images=frames[1:],
                   duration=120, loop=0)
    print("图片素材:", [p.name for p in out.glob("*.*")])


def make_videos(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    mp4 = out / "clip_h264.mp4"
    if not mp4.exists():
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "128k", "-shortest",
               "-metadata", "title=original_sample",
               str(mp4)]
        subprocess.run(cmd, check=True)
    mkv = out / "clip_h265.mkv"
    if not mkv.exists():
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=25:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", str(mkv)]
        subprocess.run(cmd, check=True)
    print("视频素材:", [p.name for p in out.glob("*.mp4")] + [p.name for p in out.glob("*.mkv")])


if __name__ == "__main__":
    make_images(ROOT)
    make_videos(ROOT)
    print("完成：", ROOT)
