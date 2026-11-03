"""tkinter 图形界面。

单窗口布局：
    ┌ 顶栏：预设 + 添加/移除/清空
    ├ 左：文件表格（原 MD5 / 新 MD5 / 状态）
    ├ 右：图片设置 / 视频设置 / 输出与运行
    ├ 日志
    └ 底部：进度条 + 统计 + 开始/停止
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image

from . import APP_TITLE, __version__
from .config import (DEFAULT_PRESET, IMAGE_EXTS, PRESETS, SUPPORTED_EXTS, VIDEO_EXTS,
                     VIDEO_PRESETS_ENCODER, ImageOptions, RunOptions, VideoOptions,
                     kind_of, preset_options)
from .engine import Engine, Job, Record, Summary, collect_files
from .hasher import digest_file, human_size
from . import mp4box
from .videos import check_tools

# Windows 用原生 WM_DROPFILES（零依赖，见 dnd.py）；其它平台才尝试 tkinterdnd2
_DND_FILES = None
_DND_AVAILABLE = False
_BaseTk = tk.Tk
if sys.platform != "win32":
    try:
        from tkinterdnd2 import DND_FILES as _DND_FILES, TkinterDnD as _TkinterDnD
        _BaseTk = _TkinterDnD.Tk
        _DND_AVAILABLE = True
    except Exception:                               # pragma: no cover
        pass

# ---------------------------------------------------------------- 配色 / 字体
BG = "#F2F4F8"
CARD = "#FFFFFF"
LINE = "#D9DFE9"
PRIMARY = "#2F6FEB"
PRIMARY_DK = "#1E53BF"
TEXT = "#1E2430"
MUTED = "#6B7280"
OK_C = "#1A7F37"
ERR_C = "#C33C3C"
WARN_C = "#B26A00"

FAMILY = "Microsoft YaHei UI"
FONT = (FAMILY, 9)
FONT_B = (FAMILY, 9, "bold")
FONT_H1 = (FAMILY, 14, "bold")
FONT_S = (FAMILY, 8)
MONO = ("Consolas", 9)


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _resource_path(*parts: str) -> Path:
    """打包后资源在 _MEIPASS 下，源码运行时在项目根目录。"""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parent.parent
    return root.joinpath(*parts)


def _tip(widget, text: str) -> None:
    """轻量 tooltip：鼠标悬停 0.45s 出现，避免设置面板里堆满说明文字。"""
    if not text:
        return
    state: dict = {"win": None, "after": None}

    def _show() -> None:
        if state["win"] is not None:
            return
        try:
            x = widget.winfo_rootx() + 14
            y = widget.winfo_rooty() + widget.winfo_height() + 6
        except tk.TclError:
            return
        win = tk.Toplevel(widget)
        win.wm_overrideredirect(True)
        win.configure(bg="#39404D")
        tk.Label(win, text=text, bg="#39404D", fg="#EAEFF7", font=(FAMILY, 8),
                 justify="left", wraplength=320, padx=9, pady=6).pack()
        win.wm_geometry(f"+{x}+{y}")
        state["win"] = win

    def _hide() -> None:
        if state["after"]:
            try:
                widget.after_cancel(state["after"])
            except tk.TclError:
                pass
            state["after"] = None
        if state["win"] is not None:
            try:
                state["win"].destroy()
            except tk.TclError:
                pass
            state["win"] = None

    def _enter(_e=None) -> None:
        _hide()
        state["after"] = widget.after(450, _show)

    widget.bind("<Enter>", _enter, add="+")
    widget.bind("<Leave>", lambda e: _hide(), add="+")
    widget.bind("<ButtonPress>", lambda e: _hide(), add="+")


# ---------------------------------------------------------------- 主窗口
class PixelShiftApp(_BaseTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE}  v{__version__}")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1360, max(1080, sw - 80)), min(900, max(700, sh - 90))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 10)}")
        self.minsize(1080, 700)
        self.configure(bg=BG)

        self.jobs: list[Job] = []
        self.iid_of_job: dict[str, str] = {}
        self.job_of_iid: dict[str, Job] = {}
        self.q: queue.Queue = queue.Queue()
        self.engine: Engine | None = None
        self.worker: threading.Thread | None = None
        self.digest_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="digest")
        self._digest_pending = 0
        self._running = False
        self._done_summary: Summary | None = None
        self._out_dir_last: str = ""
        self._dnd = self._init_dnd()

        self._init_style()
        self._apply_window_icon()
        self._build_vars()
        self._build_menu()
        self._build_ui()
        self._apply_preset(DEFAULT_PRESET, silent=True)
        self._attach_dnd()
        self._check_env()
        self.after(80, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ============================================================ 样式
    def _apply_window_icon(self) -> None:
        """标题栏 / 任务栏图标。打包后 exe 自带图标，源码模式靠这句补上。"""
        ico = _resource_path("assets", "app.ico")
        if not ico.exists():
            return
        path = str(ico)
        try:
            self.iconbitmap(default=path)     # 之后弹出的 Toplevel 也继承
        except tk.TclError:
            pass
        try:
            self.iconbitmap(path)             # 当前窗口
        except tk.TclError:
            pass

    def _init_style(self) -> None:
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=TEXT, font=FONT)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_S)
        st.configure("H1.TLabel", background=BG, foreground=TEXT, font=FONT_H1)
        st.configure("Bold.TLabel", background=CARD, foreground=TEXT, font=FONT_B)
        st.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=FONT_S)

        st.configure("TButton", padding=(10, 5), background="#E9EDF5",
                     foreground=TEXT, borderwidth=1, relief="flat", focusthickness=0)
        st.map("TButton",
               background=[("active", "#DBE3F1"), ("pressed", "#CBD5E8"),
                           ("disabled", "#F1F3F7")],
               foreground=[("disabled", "#A5ADBA")])
        st.configure("Accent.TButton", background=PRIMARY, foreground="#FFFFFF",
                     font=FONT_B, padding=(16, 7))
        st.map("Accent.TButton",
               background=[("active", PRIMARY_DK), ("pressed", PRIMARY_DK),
                           ("disabled", "#A9C0EF")],
               foreground=[("disabled", "#F2F5FD")])
        st.configure("Danger.TButton", background="#FBECEC", foreground=ERR_C, padding=(12, 6))
        st.map("Danger.TButton", background=[("active", "#F6D9D9")])

        st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                     rowheight=25, borderwidth=0, font=FONT)
        st.configure("Treeview.Heading", background="#E7ECF6", foreground="#33405A",
                     font=FONT_B, relief="flat", padding=(6, 5))
        st.map("Treeview.Heading", background=[("active", "#DCE4F3")])
        st.map("Treeview", background=[("selected", "#D7E4FF")],
               foreground=[("selected", TEXT)])

        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
        st.configure("TNotebook.Tab", background="#E4E9F3", foreground=MUTED,
                     padding=(14, 8), borderwidth=0, font=FONT)
        st.map("TNotebook.Tab",
               background=[("selected", CARD), ("active", "#EDF1F9")],
               foreground=[("selected", PRIMARY), ("active", TEXT)],
               font=[("selected", FONT_B)])

        st.configure("TLabelframe", background=CARD, borderwidth=1, relief="solid",
                     bordercolor=LINE)
        st.configure("TLabelframe.Label", background=CARD, foreground=PRIMARY, font=FONT_B)

        st.configure("TCheckbutton", background=CARD, foreground=TEXT, font=FONT)
        st.map("TCheckbutton", background=[("active", CARD)])
        st.configure("TRadiobutton", background=CARD, foreground=TEXT, font=FONT)
        st.map("TRadiobutton", background=[("active", CARD)])

        st.configure("TEntry", fieldbackground="#FBFCFE", bordercolor=LINE, padding=3)
        st.configure("TSpinbox", fieldbackground="#FBFCFE", bordercolor=LINE, padding=2)
        st.configure("TCombobox", fieldbackground="#FBFCFE", padding=2)
        st.configure("TScale", background=CARD, troughcolor="#E4E9F3")
        st.configure("Horizontal.TProgressbar", background=PRIMARY, troughcolor="#E4E9F3",
                     borderwidth=0, thickness=8)

    # ============================================================ 变量
    def _build_vars(self) -> None:
        # —— 图片
        self.v_img_mode = tk.StringVar(value="reencode")
        self.v_img_fmt = tk.StringVar(value="keep")
        self.v_img_q = tk.IntVar(value=95)
        self.v_img_png = tk.IntVar(value=-1)
        self.v_img_webp = tk.IntVar(value=92)
        self.v_img_noise = tk.IntVar(value=25)
        self.v_img_delta = tk.IntVar(value=2)
        self.v_img_jitter = tk.IntVar(value=0)
        self.v_img_resize = tk.IntVar(value=0)
        self.v_img_strip = tk.BooleanVar(value=True)
        self.v_img_inject = tk.BooleanVar(value=True)
        self.v_img_profile = tk.StringVar(value="auto")
        self.v_img_icc = tk.BooleanVar(value=True)
        self.v_img_crop = tk.StringVar(value="0")
        self.v_img_rotate = tk.StringVar(value="none")
        self.v_img_flip = tk.StringVar(value="none")
        # —— 视频
        self.v_vid_mode = tk.StringVar(value="light")
        self.v_vid_container = tk.StringVar(value="keep")
        self.v_vid_crf = tk.IntVar(value=20)
        self.v_vid_preset = tk.StringVar(value="veryfast")
        self.v_vid_filter = tk.IntVar(value=1)
        self.v_vid_audio = tk.StringVar(value="copy")
        self.v_vid_abr = tk.StringVar(value="192k")
        self.v_vid_keyint = tk.IntVar(value=0)
        self.v_vid_faststart = tk.BooleanVar(value=True)
        self.v_vid_strip = tk.BooleanVar(value=False)
        self.v_vid_inject = tk.BooleanVar(value=True)
        self.v_vid_mirror = tk.BooleanVar(value=False)
        self.v_vid_crop = tk.StringVar(value="0")
        self.v_vid_speed = tk.StringVar(value="1.000")
        self.v_vid_fallback = tk.BooleanVar(value=True)
        # —— 输出
        self.v_out_dir = tk.StringVar(value="")
        self.v_mirror_dir = tk.BooleanVar(value=True)
        self.v_collision = tk.StringVar(value="suffix")
        self.v_randname = tk.BooleanVar(value=False)
        self.v_seed = tk.StringVar(value="")
        self.v_conc = tk.IntVar(value=4)
        self.v_vworkers = tk.IntVar(value=2)
        self.v_open_when_done = tk.BooleanVar(value=False)
        self.v_preset = tk.StringVar(value=PRESETS[DEFAULT_PRESET].name)

    # ============================================================ 菜单
    def _build_menu(self) -> None:
        menubar = tk.Menu(self, tearoff=0)
        m_file = tk.Menu(menubar, tearoff=0, font=FONT)
        m_file.add_command(label="添加文件…", accelerator="Ctrl+O", command=self.add_files)
        m_file.add_command(label="添加文件夹…", accelerator="Ctrl+Shift+O",
                           command=self.add_folder)
        m_file.add_command(label="快速处理单个文件…", command=self.quick_single)
        m_file.add_separator()
        m_file.add_command(label="移除选中", command=self.remove_selected)
        m_file.add_command(label="清空列表", command=self.clear_all)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=m_file)

        m_preset = tk.Menu(menubar, tearoff=0, font=FONT)
        for key, p in PRESETS.items():
            m_preset.add_command(label=p.name, command=lambda k=key: self._apply_preset(k))
        menubar.add_cascade(label="预设", menu=m_preset)

        m_tool = tk.Menu(menubar, tearoff=0, font=FONT)
        m_tool.add_command(label="检测 ffmpeg", command=self._check_env)
        m_tool.add_command(label="打开输出目录", command=self._open_out_dir)
        m_tool.add_command(label="打开上次报告所在目录", command=self._open_report_dir)
        menubar.add_cascade(label="工具", menu=m_tool)

        m_help = tk.Menu(menubar, tearoff=0, font=FONT)
        m_help.add_command(label="使用说明", command=self._show_help)
        m_help.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=m_help)

        self.config(menu=menubar)
        self.bind_all("<Control-o>", lambda e: self.add_files())
        self.bind_all("<Control-O>", lambda e: self.add_folder())
        self.bind_all("<Delete>", lambda e: self.remove_selected())

    # ============================================================ 界面
    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_header()

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", padx=12, pady=(8, 0))
        paned.add(self._build_files(paned), weight=3)
        paned.add(self._build_settings(paned), weight=2)
        self.paned = paned
        self.after(140, self._init_sash)

        self._build_log()
        self._build_footer()

    # ---------------------------------------------------------- 顶栏
    def _build_header(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        bar.columnconfigure(1, weight=1)

        left = ttk.Frame(bar)
        left.grid(row=0, column=0, sticky="w")
        ttk.Label(left, text="PixelShift", style="H1.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(left, text="图片 / 视频特征码改写 · 单文件与批量", style="Muted.TLabel") \
            .grid(row=1, column=0, sticky="w", pady=(1, 0))

        mid = ttk.Frame(bar)
        mid.grid(row=0, column=1, sticky="w", padx=(28, 0))
        ttk.Label(mid, text="强度预设").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(mid, textvariable=self.v_preset, state="readonly", width=18,
                          values=[p.name for p in PRESETS.values()], font=FONT)
        cb.grid(row=1, column=0, sticky="w", pady=(2, 0))
        cb.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self.preset_desc = ttk.Label(mid, text="", style="Muted.TLabel", wraplength=430,
                                     justify="left")
        self.preset_desc.grid(row=1, column=1, sticky="w", padx=(10, 0))

        right = ttk.Frame(bar)
        right.grid(row=0, column=2, sticky="e")
        ttk.Button(right, text="＋ 添加文件", command=self.add_files).grid(row=0, column=0, padx=3)
        ttk.Button(right, text="📁 添加文件夹", command=self.add_folder).grid(row=0, column=1, padx=3)
        ttk.Button(right, text="移除选中", command=self.remove_selected).grid(row=0, column=2, padx=3)
        ttk.Button(right, text="清空", command=self.clear_all).grid(row=0, column=3, padx=3)

    # ---------------------------------------------------------- 文件列表
    def _build_files(self, parent) -> ttk.Frame:
        wrap = ttk.Frame(parent, style="Card.TFrame")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(1, weight=1)

        head = ttk.Frame(wrap, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="待处理文件", style="Bold.TLabel").grid(row=0, column=0, sticky="w")
        self.lbl_count = ttk.Label(head, text="0 个（图片 0 · 视频 0）", style="Hint.TLabel")
        self.lbl_count.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.lbl_drop_hint = ttk.Label(head, text="", style="Hint.TLabel")
        self.lbl_drop_hint.grid(row=0, column=2, sticky="e")
        self._update_drop_hint()

        columns = ("kind", "size", "md5", "new", "status")
        self.tree = ttk.Treeview(wrap, columns=columns, show="tree headings",
                                 selectmode="extended")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=10)
        heads = {"kind": ("类型", 58, "center"), "size": ("大小", 82, "e"),
                 "md5": ("原 MD5", 118, "w"), "new": ("新 MD5", 118, "w"),
                 "status": ("状态", 210, "w")}
        self.tree.heading("#0", text="文件")
        self.tree.column("#0", width=310, minwidth=160, stretch=True, anchor="w")
        for col, (text, width, anchor) in heads.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=50,
                             stretch=(col == "status"), anchor=anchor)

        self.tree.tag_configure("ok", foreground=OK_C)
        self.tree.tag_configure("fail", foreground=ERR_C)
        self.tree.tag_configure("skip", foreground=WARN_C)

        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        vs.grid(row=1, column=1, sticky="ns", padx=(0, 6))
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        hs.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<Button-3>", self._on_tree_menu)

        self.ctx = tk.Menu(self, tearoff=0, font=FONT)
        self.ctx.add_command(label="在资源管理器中显示", command=self._reveal_selected)
        self.ctx.add_command(label="移除选中", command=self.remove_selected)

        self.empty_hint = ttk.Label(wrap, style="Hint.TLabel",
                                    text="列表为空 —— 点右上角「添加文件 / 添加文件夹」，"
                                         "或直接把文件拖进窗口")
        self.empty_hint.grid(row=3, column=0, pady=(0, 10))

        if self._dnd == "dnd2":
            try:
                self.tree.drop_target_register(_DND_FILES)
                self.tree.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                self._dnd = None
        return wrap

    # ---------------------------------------------------------- 设置面板
    def _build_settings(self, parent) -> ttk.Frame:
        nb = ttk.Notebook(parent)
        nb.add(self._tab_image(nb), text="图片")
        nb.add(self._tab_video(nb), text="视频")
        nb.add(self._tab_run(nb), text="输出与运行")
        self.nb = nb
        return nb

    def _tab_image(self, parent) -> ttk.Frame:
        outer = ttk.Frame(parent, style="Card.TFrame")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, bg=CARD, highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=sb.set)
        body = ttk.Frame(canvas, style="Card.TFrame")
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        r = 0
        f0 = ttk.Labelframe(body, text=" 处理方式 ")
        f0.grid(row=r, column=0, sticky="ew", padx=12, pady=(10, 6))
        f0.columnconfigure(0, weight=1)
        r += 1
        ttk.Radiobutton(f0, text="重编码（改像素 + 重建元数据）", value="reencode",
                        variable=self.v_img_mode) \
            .grid(row=0, column=0, sticky="w", padx=10, pady=(5, 0))
        ttk.Radiobutton(f0, text="只注入元数据（不重编码，体积几乎不变）", value="metaonly",
                        variable=self.v_img_mode) \
            .grid(row=1, column=0, sticky="w", padx=10, pady=(2, 3))
        ttk.Label(f0, text="只在容器里插入 EXIF 与文本块，压缩数据一个字节不改；"
                           "BMP / TIFF / PPM 没有元数据区，会自动回退重编码。",
                  style="Hint.TLabel", wraplength=360) \
            .grid(row=2, column=0, sticky="w", padx=(28, 10), pady=(0, 8))

        f1 = ttk.Labelframe(body, text=" 编码与格式 ")
        f1.grid(row=r, column=0, sticky="ew", padx=12, pady=(10, 6))
        f1.columnconfigure(1, weight=1)
        r += 1
        self._row_combo(f1, 0, "输出格式", self.v_img_fmt,
                        [("keep", "保持原格式"), ("jpg", "JPEG"), ("png", "PNG"),
                         ("webp", "WEBP"), ("bmp", "BMP"), ("tiff", "TIFF")])
        self._row_spin(f1, 1, "JPEG 质量", self.v_img_q, 60, 100, 1,
                       "越高越清晰、体积越大；95 以上肉眼无差")
        self._row_spin(f1, 2, "PNG 压缩等级", self.v_img_png, -1, 9, 1,
                       "-1 表示每次随机取 6-9，让输出字节每次不同")
        self._row_spin(f1, 3, "WEBP 质量", self.v_img_webp, 50, 100, 1)

        f2 = ttk.Labelframe(body, text=" 像素改写（不改变观感） ")
        f2.grid(row=r, column=0, sticky="ew", padx=12, pady=6)
        f2.columnconfigure(1, weight=1)
        r += 1
        self._row_scale(f2, 0, "扰动强度", self.v_img_noise, 0, 100,
                        "随机挑选一小撮像素通道做 ±1~6 的微调；0 = 关闭")
        self._row_spin(f2, 1, "扰动幅度", self.v_img_delta, 1, 6, 1)
        self._row_scale(f2, 2, "色调微扰", self.v_img_jitter, 0, 100,
                        "亮度/对比/饱和/gamma 的极小随机漂移，肉眼基本无感")
        self._row_spin(f2, 3, "缩放往返 px", self.v_img_resize, 0, 4, 1,
                       "先缩小 N 像素再放大回来，打断重采样指纹")
        self._row_combo(f2, 4, "镜像", self.v_img_flip,
                        [("none", "不处理"), ("auto", "50% 概率水平镜像")])

        f3 = ttk.Labelframe(body, text=" 结构变形（真正拉开感知指纹，会改画面） ")
        f3.grid(row=r, column=0, sticky="ew", padx=12, pady=6)
        f3.columnconfigure(1, weight=1)
        r += 1
        self._row_entry(f3, 0, "居中裁切 %", self.v_img_crop,
                        "裁掉四周各 N/2% 再放大回原尺寸：0-6，0 = 不裁")
        self._row_combo(f3, 1, "旋转", self.v_img_rotate,
                        [("none", "不旋转"), ("right90", "顺时针 90°"),
                         ("left90", "逆时针 90°"), ("180", "旋转 180°")])

        f4 = ttk.Labelframe(body, text=" 元数据 ")
        f4.grid(row=r, column=0, sticky="ew", padx=12, pady=(6, 12))
        f4.columnconfigure(1, weight=1)
        r += 1
        ttk.Checkbutton(f4, text="清除原始 EXIF / XMP / 注释", variable=self.v_img_strip) \
            .grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=3)
        ttk.Checkbutton(f4, text="写入全新随机元数据（设备/软件/时间/指纹）",
                        variable=self.v_img_inject) \
            .grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=3)
        self._row_combo(f4, 2, "元数据风格", self.v_img_profile,
                        [("auto", "随机"), ("camera", "单反相机"), ("phone", "手机"),
                         ("scanner", "扫描仪"), ("plain", "极简")])
        ttk.Checkbutton(f4, text="保留 ICC 色彩配置", variable=self.v_img_icc) \
            .grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(3, 8))
        self._bind_wheel(body, canvas)
        return outer

    def _tab_video(self, parent) -> ttk.Frame:
        outer = ttk.Frame(parent, style="Card.TFrame")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, bg=CARD, highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=sb.set)
        body = ttk.Frame(canvas, style="Card.TFrame")
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        r = 0
        f1 = ttk.Labelframe(body, text=" 处理方式 ")
        f1.grid(row=r, column=0, sticky="ew", padx=12, pady=(10, 6))
        f1.columnconfigure(0, weight=1)
        r += 1
        modes = [("remux", "重封装 · 最快", "-c copy，只重写容器与元数据，画面零损失，体积基本不变"),
                 ("light", "轻度重编码 · 推荐", "视频重新编码（可带极轻噪声滤镜），音频直接拷贝"),
                 ("deep", "深度变形 · 最强", "缩放往返 + 噪声 + 色调 + 关键帧重排 + 音频重编码"),
                 ("metabox", "仅改元数据 · 免 ffmpeg",
                  "纯 Python 改写 MP4/MOV 容器（时间戳 + iTunes 标签），秒级完成，"
                  "画面与时长完全不变；没有 ffmpeg 也能用")]
        for i, (val, name, desc) in enumerate(modes):
            ttk.Radiobutton(f1, text=name, value=val, variable=self.v_vid_mode) \
                .grid(row=i * 2, column=0, sticky="w", padx=10, pady=(5 if i == 0 else 2, 0))
            ttk.Label(f1, text=desc, style="Hint.TLabel", wraplength=430) \
                .grid(row=i * 2 + 1, column=0, sticky="w", padx=(28, 10), pady=(0, 3))
        ttk.Checkbutton(f1, text="缺 ffmpeg 时自动降级为「仅改元数据」",
                        variable=self.v_vid_fallback) \
            .grid(row=len(modes) * 2, column=0, sticky="w", padx=10, pady=(8, 6))

        f2 = ttk.Labelframe(body, text=" 编码参数 ")
        f2.grid(row=r, column=0, sticky="ew", padx=12, pady=6)
        f2.columnconfigure(1, weight=1)
        r += 1
        self._row_combo(f2, 0, "容器", self.v_vid_container,
                        [("keep", "跟随源文件"), ("mp4", "MP4"), ("mkv", "MKV"), ("mov", "MOV")])
        self._row_spin(f2, 1, "CRF 画质", self.v_vid_crf, 16, 32, 1,
                       "数值越小画质越好体积越大；18-23 常用")
        self._row_combo(f2, 2, "编码速度", self.v_vid_preset,
                        [(p, p) for p in VIDEO_PRESETS_ENCODER])
        self._row_spin(f2, 3, "滤镜强度", self.v_vid_filter, 0, 3, 1,
                       "0 不加滤镜 / 1 极轻 / 2 中等 / 3 强（含缩放往返与调色）")
        self._row_combo(f2, 4, "音频", self.v_vid_audio,
                        [("copy", "直接拷贝"), ("reencode", "重新编码")])
        self._row_entry(f2, 5, "音频码率", self.v_vid_abr, "如 128k / 192k / 320k")
        self._row_spin(f2, 6, "关键帧间隔", self.v_vid_keyint, 0, 600, 10, "0 = 每段随机 200-300")
        ttk.Checkbutton(f2, text="MP4 前置 moov（faststart，边下边播）",
                        variable=self.v_vid_faststart) \
            .grid(row=7, column=0, columnspan=2, sticky="w", padx=10, pady=(3, 8))

        f3 = ttk.Labelframe(body, text=" 结构变形（会改画面/时长，默认关闭） ")
        f3.grid(row=r, column=0, sticky="ew", padx=12, pady=6)
        f3.columnconfigure(1, weight=1)
        r += 1
        ttk.Checkbutton(f3, text="水平镜像（左右翻转）", variable=self.v_vid_mirror) \
            .grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=3)
        self._row_entry(f3, 1, "居中裁切 %", self.v_vid_crop, "0-6，裁掉后放大回原分辨率")
        self._row_combo(f3, 2, "整体变速", self.v_vid_speed,
                        [(v, v) for v in ("1.000", "0.980", "0.990", "1.010", "1.020", "1.050")])
        ttk.Label(f3, text="变速会自动改用音频重编码以保持音画同步；remux 模式下勾选变形会自动升级为轻度重编码。",
                  style="Hint.TLabel", wraplength=430).grid(row=3, column=0, columnspan=2,
                                                            sticky="w", padx=10, pady=(2, 8))

        f4 = ttk.Labelframe(body, text=" 元数据 ")
        f4.grid(row=r, column=0, sticky="ew", padx=12, pady=(6, 12))
        f4.columnconfigure(1, weight=1)
        ttk.Checkbutton(f4, text="清除原始容器元数据", variable=self.v_vid_strip) \
            .grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=3)
        ttk.Checkbutton(f4, text="写入随机 comment / title / handler_name / creation_time",
                        variable=self.v_vid_inject) \
            .grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(3, 8))
        self._bind_wheel(body, canvas)
        return outer

    def _tab_run(self, parent) -> ttk.Frame:
        outer = ttk.Frame(parent, style="Card.TFrame")
        outer.columnconfigure(0, weight=1)
        body = ttk.Frame(outer, style="Card.TFrame")
        body.grid(row=0, column=0, sticky="new")
        body.columnconfigure(0, weight=1)

        f1 = ttk.Labelframe(body, text=" 输出位置 ")
        f1.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        f1.columnconfigure(0, weight=1)
        ttk.Label(f1, text="输出目录（留空 = 每个源文件旁的 _pixelshift 目录）",
                  style="Hint.TLabel").grid(row=0, column=0, columnspan=2, sticky="w",
                                            padx=10, pady=(4, 0))
        ttk.Entry(f1, textvariable=self.v_out_dir).grid(row=1, column=0, sticky="ew",
                                                        padx=(10, 4), pady=4)
        ttk.Button(f1, text="浏览…", command=self._pick_out_dir).grid(row=1, column=1,
                                                                     padx=(0, 10), pady=4)
        ttk.Checkbutton(f1, text="保留输入目录层级", variable=self.v_mirror_dir) \
            .grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))

        f2 = ttk.Labelframe(body, text=" 命名与冲突 ")
        f2.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        f2.columnconfigure(1, weight=1)
        self._row_combo(f2, 0, "同名文件", self.v_collision,
                        [("suffix", "自动改名（_2、_3…）"), ("overwrite", "覆盖"),
                         ("skip", "跳过不处理")])
        ttk.Checkbutton(f2, text="文件名追加随机后缀", variable=self.v_randname) \
            .grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=3)
        self._row_entry(f2, 2, "随机种子", self.v_seed, "留空 = 每次不同；填整数可复现同一结果")

        f3 = ttk.Labelframe(body, text=" 性能 ")
        f3.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        f3.columnconfigure(1, weight=1)
        self._row_spin(f3, 0, "图片并发", self.v_conc, 1, 16, 1, "同时处理的图片数量")
        self._row_spin(f3, 1, "视频并发", self.v_vworkers, 1, 6, 1,
                       "同时运行的 ffmpeg 进程数，建议不超过 CPU 物理核心数")

        f4 = ttk.Labelframe(body, text=" 完成后 ")
        f4.grid(row=3, column=0, sticky="ew", padx=12, pady=(6, 12))
        f4.columnconfigure(1, weight=1)
        ttk.Checkbutton(f4, text="处理完自动打开输出目录", variable=self.v_open_when_done) \
            .grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=6)
        self.lbl_env = ttk.Label(f4, text="正在检测 ffmpeg…", style="Hint.TLabel",
                                 wraplength=430, justify="left")
        self.lbl_env.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        return outer

    # ---------------------------------------------------------- 控件辅助
    def _row_combo(self, parent, row: int, label: str, var, pairs, hint: str = "") -> None:
        lab = ttk.Label(parent, text=label + (" ⓘ" if hint else ""), style="Card.TLabel")
        lab.grid(row=row, column=0, sticky="w", padx=(10, 6), pady=3)
        cb = ttk.Combobox(parent, state="readonly", width=20, font=FONT)
        cb.configure(values=[p[1] for p in pairs])
        mapping = {p[1]: p[0] for p in pairs}
        rev = {p[0]: p[1] for p in pairs}
        cur = var.get()
        cb.set(rev.get(str(cur), pairs[0][1]))

        def _picked(_e=None):
            var.set(mapping.get(cb.get(), pairs[0][0]))
        cb.bind("<<ComboboxSelected>>", _picked)
        parent._ps_combo_handles = getattr(parent, "_ps_combo_handles", [])
        parent._ps_combo_handles.append((var, rev, mapping, pairs, cb))
        cb.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=3)
        _tip(lab, hint)
        _tip(cb, hint)

    def _row_entry(self, parent, row: int, label: str, var, hint: str = "") -> None:
        lab = ttk.Label(parent, text=label + (" ⓘ" if hint else ""), style="Card.TLabel")
        lab.grid(row=row, column=0, sticky="w", padx=(10, 6), pady=3)
        e = ttk.Entry(parent, textvariable=var, width=14)
        e.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=3)
        _tip(lab, hint)
        _tip(e, hint)

    def _row_spin(self, parent, row: int, label: str, var, lo, hi, inc, hint: str = "") -> None:
        lab = ttk.Label(parent, text=label + (" ⓘ" if hint else ""), style="Card.TLabel")
        lab.grid(row=row, column=0, sticky="w", padx=(10, 6), pady=3)
        sp = ttk.Spinbox(parent, from_=lo, to=hi, increment=inc, width=8,
                         textvariable=var, font=FONT)
        sp.grid(row=row, column=1, sticky="w", padx=(0, 10), pady=3)
        _tip(lab, hint)
        _tip(sp, hint)

    def _row_scale(self, parent, row: int, label: str, var, lo, hi, hint: str = "") -> None:
        lab = ttk.Label(parent, text=label + (" ⓘ" if hint else ""), style="Card.TLabel")
        lab.grid(row=row, column=0, sticky="w", padx=(10, 6), pady=3)
        cell = ttk.Frame(parent, style="Card.TFrame")
        cell.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=3)
        cell.columnconfigure(0, weight=1)
        sc = ttk.Scale(cell, from_=lo, to=hi, variable=var, orient="horizontal",
                       command=lambda v: var.set(int(float(v))))
        sc.grid(row=0, column=0, sticky="ew")
        ttk.Label(cell, textvariable=var, width=4, style="Card.TLabel") \
            .grid(row=0, column=1, sticky="e", padx=(6, 0))
        _tip(lab, hint)
        _tip(sc, hint)

    def _scroll(self, canvas: tk.Canvas, event) -> None:
        try:
            if canvas.winfo_ismapped():
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except tk.TclError:
            pass

    def _bind_wheel(self, widget, canvas: tk.Canvas) -> None:
        """把滚轮事件绑到设置页内所有控件上（不用 bind_all，避免抢走文件列表的滚轮）。"""
        widget.bind("<MouseWheel>", lambda e: self._scroll(canvas, e), add="+")
        for child in widget.winfo_children():
            self._bind_wheel(child, canvas)

    # ---------------------------------------------------------- 日志 / 底栏
    def _build_log(self) -> None:
        wrap = ttk.Frame(self)
        wrap.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 0))
        wrap.columnconfigure(0, weight=1)

        head = ttk.Frame(wrap)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="运行日志", style="Bold.TLabel").grid(row=0, column=0, sticky="w")
        self.btn_log_toggle = ttk.Button(head, text="收起", width=6, command=self._toggle_log)
        self.btn_log_toggle.grid(row=0, column=2, sticky="e")

        self.log_box = tk.Text(wrap, height=6, bg="#1B1F27", fg="#D6DEE8", bd=0,
                               font=MONO, wrap="none", insertbackground="#D6DEE8",
                               padx=10, pady=8, state="disabled")
        self.log_box.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.log_box.tag_configure("error", foreground="#FF8A8A")
        self.log_box.tag_configure("warn", foreground="#FFC46B")
        self.log_box.tag_configure("ok", foreground="#8FE3A8")
        self.log_box.tag_configure("info", foreground="#9CC2FF")
        self.log_box.tag_configure("dim", foreground="#7C8798")
        self.log_visible = True

    def _build_footer(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 12))
        bar.columnconfigure(0, weight=1)

        left = ttk.Frame(bar)
        left.grid(row=0, column=0, sticky="ew")
        left.columnconfigure(0, weight=1)
        self.pb = ttk.Progressbar(left, mode="determinate", maximum=100)
        self.pb.grid(row=0, column=0, sticky="ew")
        self.lbl_stat = ttk.Label(left, text="就绪", style="Muted.TLabel")
        self.lbl_stat.grid(row=1, column=0, sticky="w", pady=(4, 0))

        right = ttk.Frame(bar)
        right.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.btn_start = ttk.Button(right, text="▶  开始处理", style="Accent.TButton",
                                    command=self.start)
        self.btn_start.grid(row=0, column=0, padx=(0, 6))
        self.btn_stop = ttk.Button(right, text="■  停止", style="Danger.TButton",
                                   command=self.stop, state="disabled")
        self.btn_stop.grid(row=0, column=1)

    def _init_sash(self) -> None:
        """把默认分割位置定在左 61% / 右 39%，右侧设置面板才够宽。"""
        try:
            w = self.paned.winfo_width()
            if w > 400:
                self.paned.sashpos(0, int(w * 0.61))
        except (tk.TclError, AttributeError):
            pass

    def _toggle_log(self) -> None:
        if self.log_visible:
            self.log_box.configure(height=1)
            self.btn_log_toggle.configure(text="展开")
        else:
            self.log_box.configure(height=6)
            self.btn_log_toggle.configure(text="收起")
        self.log_visible = not self.log_visible

    # ============================================================ 拖放
    def _init_dnd(self):
        """返回 'native'（Windows 原生，零依赖）/ 'dnd2'（tkinterdnd2）/ None。"""
        if sys.platform == "win32":
            return "native"
        return "dnd2" if _DND_AVAILABLE else None

    def _attach_dnd(self) -> None:
        self._drop_target = None
        if self._dnd != "native":
            return
        from .dnd import attach_drop
        self._drop_target = attach_drop(self, self._on_paths_dropped)
        self.after(700, self._report_dnd_state)

    def _report_dnd_state(self) -> None:
        target = getattr(self, "_drop_target", None)
        if target is None:
            return
        if target.ok:
            self._log(f"拖放已启用（顶层窗口句柄 0x{target.hwnd:X}）", "dim")
        else:
            self._dnd = None
            self._update_drop_hint()
            self._log(f"拖放未启用：{target.error or '未知原因'}；请用「添加文件」按钮", "warn")

    def _update_drop_hint(self) -> None:
        if not hasattr(self, "lbl_drop_hint"):
            return
        if self._dnd:
            self.lbl_drop_hint.configure(text="⇩ 可直接把文件 / 文件夹拖到这里",
                                         foreground=MUTED)
        else:
            self.lbl_drop_hint.configure(text="")

    def _on_paths_dropped(self, paths: list[str]) -> None:
        """原生拖放回调（已回到 Tk 主线程）。"""
        if not paths:
            self._flash_drop("没有识别到文件", ok=False)
            return
        added = self._add_paths([Path(p) for p in paths])
        if added > 0:
            self._flash_drop(f"已拖入 {added} 个文件")
            if self.empty_hint.winfo_exists():
                self.empty_hint.grid_remove()
        elif added == 0:
            self._flash_drop("没有新增（重复或类型不支持）", ok=False)
        else:
            self._flash_drop("正在处理，稍后再拖入", ok=False)

    def _flash_drop(self, text: str, ok: bool = True) -> None:
        if not hasattr(self, "lbl_drop_hint"):
            return
        self.lbl_drop_hint.configure(text=f"✔ {text}" if ok else f"✖ {text}",
                                     foreground=OK_C if ok else WARN_C)
        self.after(1600, self._update_drop_hint)

    def _on_drop(self, event):  # pragma: no cover - 仅 tkinterdnd2 路径
        try:
            raw = list(self.tk.splitlist(event.data))
        except Exception:
            raw = [event.data]
        self._on_paths_dropped([str(p) for p in raw])

    # ============================================================ 添加 / 移除
    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择图片或视频",
            filetypes=[("图片与视频", " ".join("*" + e for e in SUPPORTED_EXTS)),
                       ("图片", " ".join("*" + e for e in IMAGE_EXTS)),
                       ("视频", " ".join("*" + e for e in VIDEO_EXTS)),
                       ("所有文件", "*.*")])
        if paths:
            self._add_paths([Path(p) for p in paths])

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择文件夹（会递归包含子目录）")
        if folder:
            self._add_paths([Path(folder)])

    def quick_single(self) -> None:
        """单文件快捷处理：清空列表 → 只放这一个 → 立刻按当前设置处理。"""
        if self._running:
            messagebox.showinfo("正在处理", "先等当前批次结束。")
            return
        path = filedialog.askopenfilename(
            title="选择要处理的单个文件",
            filetypes=[("图片与视频", " ".join("*" + e for e in SUPPORTED_EXTS)),
                       ("所有文件", "*.*")])
        if not path:
            return
        if self.jobs and not messagebox.askyesno(
                "确认", "这会清空当前列表，只处理这一个文件。继续吗？"):
            return
        self.clear_all()
        self._add_paths([Path(path)])
        self.after(450, self.start)

    def _add_paths(self, paths: list[Path]) -> int:
        """返回新增数量；-1 表示正在处理中无法添加。"""
        if self._running:
            self._log("处理中，等当前批次结束后再添加", "warn")
            return -1
        new_jobs = collect_files([str(p) for p in paths], recursive=True)
        if not new_jobs:
            self._log("没有找到支持的图片/视频文件", "warn")
            return 0
        added = 0
        for job in new_jobs:
            key = str(job.src.resolve()).lower()
            if key in self.iid_of_job:
                continue
            self._insert_job(job)
            added += 1
        self._refresh_count()
        if added == 0:
            self._log("这些文件已经在列表里了", "dim")
        else:
            self._log(f"已添加 {added} 个文件（本次扫描到 {len(new_jobs)} 个）", "ok")
        return added

    def _insert_job(self, job: Job) -> str:
        try:
            size = job.src.stat().st_size
        except OSError:
            size = 0
        kind_text = "图片" if job.kind == "image" else ("视频" if job.kind == "video" else "其他")
        iid = self.tree.insert("", "end", text=job.src.name, values=(
            kind_text, human_size(size), "计算中…", "-", "待处理"))
        self.jobs.append(job)
        self.iid_of_job[str(job.src.resolve()).lower()] = iid
        self.job_of_iid[iid] = job
        if self.empty_hint.winfo_exists():
            self.empty_hint.grid_remove()
        self._queue_digest(iid, job)
        return iid

    def _queue_digest(self, iid: str, job: Job) -> None:
        self._digest_pending += 1

        def work() -> None:
            d = digest_file(job.src)
            self.q.put(("digest", (iid, d.md5), None))

        self.digest_pool.submit(work)

    def remove_selected(self) -> None:
        if self._running:
            return
        for iid in list(self.tree.selection()):
            job = self.job_of_iid.pop(iid, None)
            if job:
                key = str(job.src.resolve()).lower()
                self.iid_of_job.pop(key, None)
                self.jobs = [j for j in self.jobs if j is not job]
            self.tree.delete(iid)
        self._refresh_count()

    def clear_all(self) -> None:
        if self._running:
            return
        self.tree.delete(*self.tree.get_children())
        self.jobs.clear()
        self.iid_of_job.clear()
        self.job_of_iid.clear()
        self.pb.configure(value=0)
        self.lbl_stat.configure(text="就绪")
        self.empty_hint.grid()
        self._refresh_count()

    def _refresh_count(self) -> None:
        img = sum(1 for j in self.jobs if j.kind == "image")
        vid = sum(1 for j in self.jobs if j.kind == "video")
        self.lbl_count.configure(text=f"{len(self.jobs)} 个（图片 {img} · 视频 {vid}）")

    def _on_tree_double(self, _e=None) -> None:
        self._reveal_selected()

    def _on_tree_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        try:
            self.ctx.tk_popup(event.x_root, event.y_root)
        finally:
            self.ctx.grab_release()

    def _reveal_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        job = self.job_of_iid.get(sel[0])
        if not job:
            return
        target = job.src
        rec_iid = sel[0]
        new_val = self.tree.set(rec_iid, "new")
        if new_val and new_val != "-" and Path(new_val).exists():
            target = Path(new_val)
        self._reveal(target)

    @staticmethod
    def _reveal(path: Path) -> None:
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception:
            pass

    # ============================================================ 预设
    def _on_preset_selected(self, _e=None) -> None:
        for key, p in PRESETS.items():
            if p.name == self.v_preset.get():
                self._apply_preset(key)

    def _apply_preset(self, key: str, silent: bool = False) -> None:
        p = PRESETS.get(key)
        if not p:
            return
        img, vid = preset_options(key)
        self.v_preset.set(p.name)
        self.preset_desc.configure(text=p.desc)

        self.v_img_fmt.set(img.out_format)
        self.v_img_mode.set(img.mode)
        self.v_img_q.set(img.jpeg_quality)
        self.v_img_noise.set(img.pixel_noise)
        self.v_img_delta.set(img.noise_delta)
        self.v_img_jitter.set(img.color_jitter)
        self.v_img_resize.set(img.micro_resize)
        self.v_img_strip.set(img.strip_metadata)
        self.v_img_inject.set(img.inject_metadata)
        self.v_img_crop.set(f"{img.crop_percent:g}")
        self.v_img_flip.set(img.flip)
        self.v_img_rotate.set(img.rotate)

        self.v_vid_mode.set(vid.mode)
        self.v_vid_crf.set(vid.crf)
        self.v_vid_filter.set(vid.filter_level)
        self.v_vid_audio.set(vid.audio_mode)
        self.v_vid_strip.set(vid.strip_metadata)
        self.v_vid_inject.set(vid.inject_metadata)
        self.v_vid_crop.set(f"{vid.crop_percent:g}")
        self.v_vid_mirror.set(vid.mirror)
        self.v_vid_speed.set(f"{vid.speed:.3f}")
        self.v_vid_fallback.set(vid.no_ffmpeg_fallback)

        self._sync_combo_display()
        if not silent:
            self._log(f"已应用预设：{p.name}", "info")

    def _sync_combo_display(self) -> None:
        """变量被预设改写后，把 Combobox 的显示文字同步过去。"""
        for parent in self._walk_widgets(self):
            for var, rev, mapping, pairs, cb in getattr(parent, "_ps_combo_handles", []):
                try:
                    cur = str(var.get())
                    text = rev.get(cur)
                    if text is not None and cb.get() != text:
                        cb.set(text)
                except Exception:
                    pass

    def _walk_widgets(self, w):
        yield w
        for c in w.winfo_children():
            yield from self._walk_widgets(c)

    # ============================================================ 选项收集
    @staticmethod
    def _as_float(var, default: float) -> float:
        try:
            return float(str(var.get()).strip())
        except (ValueError, tk.TclError):
            return default

    @staticmethod
    def _as_int(var, default: int) -> int:
        try:
            return int(float(str(var.get()).strip()))
        except (ValueError, tk.TclError):
            return default

    def _collect_options(self) -> RunOptions:
        img = ImageOptions(
            mode=self.v_img_mode.get() or "reencode",
            out_format=self.v_img_fmt.get() or "keep",
            jpeg_quality=max(60, min(100, self.v_img_q.get())),
            png_level=max(-1, min(9, self.v_img_png.get())),
            webp_quality=max(50, min(100, self.v_img_webp.get())),
            pixel_noise=max(0, min(100, self.v_img_noise.get())),
            noise_delta=max(1, min(6, self.v_img_delta.get())),
            color_jitter=max(0, min(100, self.v_img_jitter.get())),
            micro_resize=max(0, min(4, self.v_img_resize.get())),
            crop_percent=max(0.0, min(6.0, self._as_float(self.v_img_crop, 0.0))),
            rotate=self.v_img_rotate.get(),
            flip=self.v_img_flip.get(),
            strip_metadata=bool(self.v_img_strip.get()),
            inject_metadata=bool(self.v_img_inject.get()),
            meta_profile=self.v_img_profile.get(),
            keep_icc=bool(self.v_img_icc.get()),
        )
        seed_text = self.v_seed.get().strip()
        seed = None
        if seed_text:
            try:
                seed = int(seed_text)
            except ValueError:
                seed = None
        vid = VideoOptions(
            mode=self.v_vid_mode.get(),
            container=self.v_vid_container.get(),
            crf=max(16, min(32, self.v_vid_crf.get())),
            preset=self.v_vid_preset.get(),
            filter_level=max(0, min(3, self.v_vid_filter.get())),
            audio_mode=self.v_vid_audio.get(),
            audio_bitrate=self.v_vid_abr.get().strip() or "192k",
            keyint=max(0, self.v_vid_keyint.get()),
            faststart=bool(self.v_vid_faststart.get()),
            strip_metadata=bool(self.v_vid_strip.get()),
            inject_metadata=bool(self.v_vid_inject.get()),
            mirror=bool(self.v_vid_mirror.get()),
            crop_percent=max(0.0, min(6.0, self._as_float(self.v_vid_crop, 0.0))),
            speed=max(0.9, min(1.1, self._as_float(self.v_vid_speed, 1.0))),
            no_ffmpeg_fallback=bool(self.v_vid_fallback.get()),
            metabox_patch_mdhd=True,
        )
        return RunOptions(
            out_dir=self.v_out_dir.get().strip(),
            mirror_structure=bool(self.v_mirror_dir.get()),
            collision=self.v_collision.get(),
            random_name=bool(self.v_randname.get()),
            random_seed=seed,
            concurrency=max(1, min(16, self.v_conc.get())),
            video_workers=max(1, min(6, self.v_vworkers.get())),
            verify_output=True,
            open_folder_when_done=bool(self.v_open_when_done.get()),
            image=img,
            video=vid,
        )

    # ============================================================ 运行
    def start(self) -> None:
        if self._running:
            return
        if not self.jobs:
            messagebox.showinfo("还没有文件", "先添加要处理的图片或视频。")
            return
        opts = self._collect_options()
        if opts.out_dir:
            try:
                Path(opts.out_dir).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("输出目录不可用", str(exc))
                return
        videos_in_list = [j for j in self.jobs if j.kind == "video"]
        if videos_in_list:
            ff_ok, msg = check_tools()
            if not ff_ok:
                mode = self.v_vid_mode.get()
                allow_fallback = bool(self.v_vid_fallback.get())
                if mode != "metabox" and not allow_fallback:
                    messagebox.showerror(
                        "缺少 ffmpeg",
                        msg + "\n\n可选：\n"
                              "· 安装 ffmpeg 后重试\n"
                              "· 视频处理方式改为「仅改元数据 · 免 ffmpeg」\n"
                              "· 勾选「缺少 ffmpeg 时自动降级」\n"
                              "· 或只处理图片")
                    return
                unsupported = [j for j in videos_in_list if not mp4box.supported(j.src)]
                if unsupported:
                    names = "、".join(p.src.name for p in unsupported[:6])
                    more = f" 等 {len(unsupported)} 个" if len(unsupported) > 6 else ""
                    if not messagebox.askokcancel(
                            "部分视频无法降级",
                            f"没有找到 ffmpeg，视频将改用「仅改元数据」模式。\n\n"
                            f"但下面这些不是 MP4/MOV 容器，会被跳过：\n{names}{more}\n\n继续吗？"):
                        return
                self._log("未检测到 ffmpeg：视频走「仅改元数据」模式（纯 Python，不重编码）", "warn")

        for iid in self.tree.get_children():
            self.tree.set(iid, "status", "排队中")
            self.tree.set(iid, "new", "-")
            self.tree.item(iid, tags=())

        self._running = True
        self._done_summary = None
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.pb.configure(value=0)
        self.lbl_stat.configure(text="准备中…")
        self._log("─" * 60, "dim")
        self._log(f"开始处理 {len(self.jobs)} 个文件 · {opts.image.label()} · {opts.video.label()}",
                  "info")

        self.engine = Engine(opts, self._on_engine_event)
        jobs = list(self.jobs)
        self._t_start = time.perf_counter()
        self.worker = threading.Thread(target=self._run_engine, args=(jobs,), daemon=True)
        self.worker.start()

    def _run_engine(self, jobs: list[Job]) -> None:
        try:
            assert self.engine is not None
            self.engine.run(jobs)
        except Exception as exc:                      # noqa: BLE001
            self.q.put(("log", (f"引擎异常：{exc}", "error"), None))
            self.q.put(("crashed", None, None))

    def stop(self) -> None:
        if self.engine and self._running:
            self.engine.stop()
            self.btn_stop.configure(state="disabled")
            self.lbl_stat.configure(text="正在停止…（等待当前文件结束）")
            self._log("已请求停止", "warn")

    def _on_engine_event(self, kind: str, payload) -> None:
        self.q.put((kind, payload, None))

    def _pump(self) -> None:
        try:
            while True:
                kind, payload, _ = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        except Exception as exc:                      # noqa: BLE001
            self._log(f"界面事件异常：{exc}", "error")
        finally:
            self.after(60, self._pump)

    def _handle(self, kind: str, payload) -> None:
        if kind == "digest":
            iid, md5 = payload
            if self.tree.exists(iid):
                self.tree.set(iid, "md5", md5[:12] if md5 else "-")
            return
        if kind == "start":
            self.lbl_stat.configure(text=f"共 {payload} 个文件…")
            return
        if kind == "progress":
            idx, frac = payload
            job = self.jobs[idx] if idx < len(self.jobs) else None
            if job:
                name = job.src.name
                if len(name) > 34:
                    name = name[:16] + "…" + name[-14:]
                self.lbl_stat.configure(text=f"正在处理：{name}  {frac * 100:.0f}%")
            return
        if kind == "item_done":
            idx, rec, done, total = payload
            self._apply_record(idx, rec, done, total)
            return
        if kind == "log":
            text, level = payload
            self._log(text, level)
            return
        if kind == "debug":
            if payload and payload.strip():
                line = payload.strip().splitlines()[-1]
                self._log("  " + line, "dim")
            return
        if kind == "done":
            self._on_done(payload)
            return
        if kind == "crashed":
            self._running = False
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.lbl_stat.configure(text="发生异常，已停止")
            return

    def _apply_record(self, idx: int, rec: Record, done: int, total: int) -> None:
        iid = None
        for k, v in self.job_of_iid.items():
            if v.src == rec.src:
                iid = k
                break
        if iid is None or not self.tree.exists(iid):
            return
        status_text = {"ok": "完成", "failed": "失败", "skipped": "跳过",
                       "cancelled": "已取消"}.get(rec.status, rec.status)
        if rec.ok and rec.phash_dist is not None:
            status_text += f"（pHashΔ{rec.phash_dist}）"
        elif rec.ok:
            status_text += f"（{rec.seconds:.1f}s）"
        self.tree.set(iid, "new", rec.new_md5[:12] if rec.new_md5 else "-")
        self.tree.set(iid, "status", status_text)
        if not rec.old_md5:
            self.tree.set(iid, "md5", "-")
        tags = {"ok": "ok", "failed": "fail", "skipped": "skip"}.get(rec.status)
        if tags:
            self.tree.item(iid, tags=(tags,))
        if rec.ok:
            self._log(f"[{done}/{total}] {rec.src.name} → "
                      f"{rec.old_md5[:8]}→{rec.new_md5[:8]}  {rec.seconds:.1f}s", "ok")
        elif rec.status == "skipped":
            self._log(f"[{done}/{total}] 跳过 {rec.src.name}：{rec.error}", "warn")
        elif rec.status == "cancelled":
            self._log(f"[{done}/{total}] 取消 {rec.src.name}", "dim")
        else:
            self._log(f"[{done}/{total}] 失败 {rec.src.name}：{rec.error}", "error")
        if total:
            self.pb.configure(value=done / total * 100)
        self.lbl_stat.configure(text=f"已完成 {done}/{total}")

    def _on_done(self, summary: Summary) -> None:
        self._running = False
        self._done_summary = summary
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.pb.configure(value=100 if summary.ok else self.pb["value"])
        self.lbl_stat.configure(text=summary.text())
        level = "ok" if summary.failed == 0 else "warn"
        self._log("─" * 60, "dim")
        self._log(summary.text(), level)
        if summary.report_path:
            self._log(f"报告已保存：{summary.report_path}", "info")
        if summary.ok and self.v_open_when_done.get():
            self._open_out_dir()

    # ============================================================ 工具动作
    def _pick_out_dir(self) -> None:
        init = self.v_out_dir.get().strip() or str(Path.home())
        folder = filedialog.askdirectory(title="选择输出目录", initialdir=init)
        if folder:
            self.v_out_dir.set(folder)
            self._out_dir_last = folder

    def _current_out_dir(self) -> Path | None:
        raw = self.v_out_dir.get().strip()
        if raw:
            return Path(raw)
        if self.jobs:
            return self.jobs[0].base / "_pixelshift"
        return None

    def _open_out_dir(self) -> None:
        target = self._current_out_dir()
        if not target or not target.exists():
            messagebox.showinfo("输出目录", "还没有输出目录，先运行一次处理。")
            return
        try:
            if os.name == "nt":
                os.startfile(str(target))      # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:              # noqa: BLE001
            messagebox.showerror("打开失败", str(exc))

    def _open_report_dir(self) -> None:
        if self._done_summary and self._done_summary.report_path:
            self._reveal(self._done_summary.report_path)
        else:
            messagebox.showinfo("报告", "本次还没有生成报告。")

    def _check_env(self) -> None:
        ok, msg = check_tools()
        if hasattr(self, "lbl_env"):
            self.lbl_env.configure(
                text=("ffmpeg 就绪：" + msg.splitlines()[0].replace("ffmpeg: ", ""))
                if ok else ("⚠ " + msg.replace("\n", " · ")))
        self._log(("ffmpeg 检测通过 · " + msg.replace("\n", " · ")) if ok
                  else ("ffmpeg 未找到：" + msg), "ok" if ok else "warn")

    def _show_help(self) -> None:
        text = (
            "使用步骤\n"
            "1) 右上角添加文件或文件夹（也支持直接拖入窗口），列表会显示原 MD5。\n"
            "2) 顶部选强度预设（轻度 / 标准 / 强力），也可以到右侧逐项微调。\n"
            "3) 「输出与运行」里选输出目录（留空 = 源文件旁的 _pixelshift）。\n"
            "4) 点「开始处理」，处理结果与原 MD5、pHash 差异会写回列表，并生成 CSV 报告。\n\n"
            "改了什么\n"
            "· 文件特征码（MD5/SHA1）：重编码 + 元数据重构，必然改变。\n"
            "· 感知指纹（pHash/aHash）：像素微扰、缩放往返、色调漂移；勾选结构变形"
            "（裁切/镜像/变速）后差异更大。\n"
            "· 元数据：可清空原始 EXIF/容器元数据，写入一套全新的随机设备、软件、时间与指纹。\n\n"
            "强度怎么选\n"
            "· 轻度：只换字节，画面几乎零改动，秒级完成（视频为 -c copy 重封装）。\n"
            "· 标准：像素级微扰 + 重编码，肉眼无差，推荐日常使用。\n"
            "· 强力：裁切 1.5% + 噪声 + 调色 + 重编码，感知指纹差异大，画质轻微损失。\n")
        self._text_window("使用说明", text)

    def _show_about(self) -> None:
        self._text_window(
            "关于 PixelShift",
            f"PixelShift v{__version__}\n\n"
            "批量 / 单个改写图片与视频的特征码：\n"
            "· 文件层：MD5 / SHA1 必然改变\n"
            "· 感知层：pHash / aHash 可变（强度可调）\n"
            "· 元数据：清空 + 重建随机指纹\n\n"
            f"Python {sys.version.split()[0]} · Pillow · ffmpeg\n"
            "处理全部在本机完成，不上传任何文件。")

    def _text_window(self, title: str, text: str) -> None:
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("620x520")
        win.configure(bg=BG)
        win.transient(self)
        box = tk.Text(win, bg=CARD, fg=TEXT, font=(FAMILY, 10), wrap="word",
                      padx=18, pady=16, bd=0, spacing1=2, spacing3=4)
        box.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        box.insert("1.0", text)
        box.configure(state="disabled")
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 12))

    def _log(self, msg: str, level: str = "info") -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{stamp}  ", ("dim",))
        self.log_box.insert("end", msg + "\n", (level,))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_close(self) -> None:
        if self._running:
            if not messagebox.askyesno("正在处理", "还有文件在处理，确定退出吗？"):
                return
            if self.engine:
                self.engine.stop()
        target = getattr(self, "_drop_target", None)
        if target is not None:
            try:
                target.detach()
            except Exception:
                pass
        try:
            self.digest_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self.destroy()


# ---------------------------------------------------------------- 入口
def main() -> None:
    _enable_dpi_awareness()
    app = PixelShiftApp()
    app.mainloop()


if __name__ == "__main__":
    main()
