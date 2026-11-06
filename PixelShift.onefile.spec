# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包描述 —— onefile（单文件 exe）。

    python -m PyInstaller --noconfirm --clean PixelShift.onefile.spec

产物：dist/PixelShift.exe（约 20-30 MB）
优点：只有一个文件，便于分发。
代价：每次启动都要把内容解压到 %TEMP%\\_MEIxxxx，冷启动慢 1-3 秒；
      单文件 + bootloader 特征更容易被杀软误报。
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

_ver_file = Path(SPECPATH) / "build_onefile" / "version_info.txt"
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
        "numpy", "pandas", "scipy", "matplotlib", "sympy", "sklearn",
        "pytest", "_pytest", "IPython", "notebook", "jupyter",
        "pip", "wheel", "setuptools", "pkg_resources",
        "tkinter.test", "pydoc_data", "lib2to3", "distutils",
        "ssl", "_ssl", "_hashlib", "sqlite3", "_sqlite3", "_decimal",
    ],
    noarchive=False,
    optimize=1,
)

_DROP_BINARIES = ("_avif", "libcrypto", "libssl", "libssh2", "libcurl")
a.binaries = [b for b in a.binaries
              if not any(k in b[0].lower() for k in _DROP_BINARIES)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PixelShift",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app.ico",
    version=str(_ver_file),
)
