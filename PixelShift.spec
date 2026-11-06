# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包描述 —— onedir（目录形式，推荐）。

    python -m PyInstaller --noconfirm --clean PixelShift.spec

产物：dist/PixelShift/PixelShift.exe + dist/PixelShift/_internal/
优点：启动快（不用每次解压）、便于把 ffmpeg 塞进 _internal、杀软误报相对低。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# ---- 生成 exe 版本资源（右键属性里显示中文名）--------------------------------
sys.path.insert(0, str(Path(SPECPATH)))
from pixelshift import __version__ as _VER          # noqa: E402

_parts = [int(x) for x in _VER.split(".")] + [0, 0, 0, 0]
_ver_tuple = tuple(_parts[:4])
_ver_str = ".".join(str(x) for x in _parts[:3])

_ver_file = Path(SPECPATH) / "build" / "version_info.txt"
_ver_file.parent.mkdir(parents=True, exist_ok=True)
_ver_file.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={_ver_tuple},
    prodvers={_ver_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'kandsome1'),
        StringStruct('FileDescription', '像素位移 PixelShift · 图片视频特征码改写器'),
        StringStruct('FileVersion', '{_ver_str}'),
        StringStruct('InternalName', 'PixelShift'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', 'PixelShift.exe'),
        StringStruct('ProductName', '像素位移 PixelShift'),
        StringStruct('ProductVersion', '{_ver_str}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""", encoding="utf-8")

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
    datas=[("assets/app.ico", "assets")],   # 窗口/任务栏图标，运行时按 _MEIPASS 取
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
    icon="assets/app.ico",
    version=str(_ver_file),
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
