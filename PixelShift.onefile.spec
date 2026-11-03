# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包描述 —— onefile（单文件 exe）。

    python -m PyInstaller --noconfirm --clean PixelShift.onefile.spec

产物：dist/PixelShift.exe（约 20-30 MB）
优点：只有一个文件，便于分发。
代价：每次启动都要把内容解压到 %TEMP%\\_MEIxxxx，冷启动慢 1-3 秒；
      单文件 + bootloader 特征更容易被杀软误报。
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

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
)
