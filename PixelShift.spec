# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包描述 —— onedir（目录形式，推荐）。

    python -m PyInstaller --noconfirm --clean PixelShift.spec

产物：dist/PixelShift/PixelShift.exe + dist/PixelShift/_internal/
优点：启动快（不用每次解压）、便于把 ffmpeg 塞进 _internal、杀软误报相对低。
"""

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Pillow 的编解码器是运行时按需 import 的，必须显式声明
PIL_PLUGINS = [
    "PIL._tkinter_finder",
    "PIL.BmpImagePlugin",
    "PIL.GifImagePlugin",
    "PIL.Jpeg2KImagePlugin",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
    "PIL.PpmImagePlugin",
    "PIL.TiffImagePlugin",
    "PIL.WebPImagePlugin",
]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=[],                       # 没有需要随包分发的外部资源
    hiddenimports=PIL_PLUGINS + collect_submodules("pixelshift"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 纯可选的大件，砍掉能省几十 MB
        "numpy", "pandas", "scipy", "matplotlib", "sympy", "sklearn",
        "pytest", "_pytest", "IPython", "notebook", "jupyter",
        "pip", "wheel", "setuptools", "pkg_resources",
        "tkinter.test", "pydoc_data", "lib2to3", "distutils",
        # 本项目不联网、不用数据库，连带省掉 OpenSSL 与 sqlite
        "ssl", "_ssl", "_hashlib", "sqlite3", "_sqlite3", "_decimal",
    ],
    noarchive=False,
    optimize=1,
)

# 再剔掉已收集进来的大二进制：Pillow 的 AVIF 编解码器（7.5 MB）与 OpenSSL（5.8 MB）
_DROP_BINARIES = ("_avif", "libcrypto", "libssl", "libssh2", "libcurl")
a.binaries = [b for b in a.binaries
              if not any(k in b[0].lower() for k in _DROP_BINARIES)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PixelShift",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX 压缩会显著提高杀软误报率
    console=False,                  # GUI 程序：不弹黑色控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                      # 有图标就填 "assets/app.ico"
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PixelShift",
)
