"""Windows 原生文件拖放 —— 不依赖任何第三方库。

原理：对 Tk 顶层窗口调用 DragAcceptFiles，并子类化窗口过程
（SetWindowLongPtrW + CallWindowProcW）截获 WM_DROPFILES 消息，
用 DragQueryFileW 取出被拖入的路径，再交回主线程的 Tk 事件循环处理。

非 Windows 平台或子类化失败时 attach() 返回 False，调用方应退回按钮添加。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

_IS_WIN = sys.platform == "win32"

if _IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND

    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = _LRESULT

    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL

    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                       ctypes.c_wchar_p, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]

    # 32 位系统没有 SetWindowLongPtrW
    _SetWndProc = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    _SetWndProc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _SetWndProc.restype = ctypes.c_void_p
else:                                              # pragma: no cover
    user32 = shell32 = None
    WNDPROC = None
    _SetWndProc = None


def top_level_hwnd(tk_widget) -> int:
    """拿到真正的顶层窗口句柄（winfo_id 给的是 Tk 的内层窗口）。"""
    hwnd = tk_widget.winfo_id()
    parent = user32.GetParent(wintypes.HWND(hwnd))
    return int(parent) if parent else int(hwnd)


def extract_dropped_files(hdrop: int) -> list[str]:
    """从 HDROP 句柄读出所有路径（宽字符，支持中文）。"""
    h = wintypes.HANDLE(hdrop)
    count = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
    paths: list[str] = []
    for i in range(count):
        need = shell32.DragQueryFileW(h, i, None, 0)
        if not need:
            continue
        buf = ctypes.create_unicode_buffer(need + 1)
        shell32.DragQueryFileW(h, i, buf, need + 1)
        if buf.value:
            paths.append(buf.value)
    return paths


class DropTarget:
    """把某个 Tk 窗口变成文件投放目标。

    注意：窗口过程回调运行在 Tcl 的消息派发栈里，此时**绝不能**调用任何 Tk API
    （会重入解释器并触发 `PyEval_RestoreThread` 崩溃）。所以回调里只把路径塞进
    一个普通 list，由主线程的定时器取出后再交给业务回调。
    """

    POLL_MS = 120

    def __init__(self, widget, callback: Callable[[list[str]], None]):
        self.widget = widget
        self.callback = callback
        self.ok = False
        self.hwnd = 0
        self.error = ""
        self._pending: list[list[str]] = []
        self._old_proc = None
        self._proc = None          # 必须持有引用，否则回调会被 GC
        self._polling = False

    def attach(self) -> bool:
        if not _IS_WIN:
            self.error = "非 Windows 平台"
            return False
        try:
            self.hwnd = top_level_hwnd(self.widget)
            shell32.DragAcceptFiles(wintypes.HWND(self.hwnd), True)
            self._proc = WNDPROC(self._wndproc)
            old = _SetWndProc(wintypes.HWND(self.hwnd), GWLP_WNDPROC,
                              ctypes.cast(self._proc, ctypes.c_void_p))
            if not old:
                self.error = f"SetWindowLongPtrW 失败，err={ctypes.get_last_error()}"
                return False
            self._old_proc = old
            self.ok = True
            self._start_poll()
            return True
        except Exception as exc:                    # noqa: BLE001
            self.error = str(exc)
            return False

    def detach(self) -> None:
        if not self.ok:
            return
        self.ok = False
        try:
            _SetWndProc(wintypes.HWND(self.hwnd), GWLP_WNDPROC, self._old_proc)
        except Exception:
            pass

    # -------------------------------------------------- 主线程轮询
    def _start_poll(self) -> None:
        if self._polling:
            return
        self._polling = True
        self.widget.after(self.POLL_MS, self._tick)

    def _tick(self) -> None:
        if not self.ok:
            self._polling = False
            return
        while self._pending:
            batch = self._pending.pop(0)
            try:
                self.callback(batch)
            except Exception:                       # noqa: BLE001
                pass
        try:
            self.widget.after(self.POLL_MS, self._tick)
        except Exception:
            self._polling = False

    # -------------------------------------------------- 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            paths: list[str] = []
            try:
                paths = extract_dropped_files(int(wparam))
            except Exception:                       # noqa: BLE001
                paths = []
            finally:
                try:
                    shell32.DragFinish(wintypes.HANDLE(wparam))
                except Exception:
                    pass
            if paths:
                self._pending.append(paths)         # 纯 list 操作，不碰 Tk
            return 0
        return user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)


def attach_drop(widget, callback: Callable[[list[str]], None],
                delay_ms: int = 120) -> DropTarget:
    """延迟 attach（等窗口真正创建），返回目标对象以便查询 ok / detach。"""
    target = DropTarget(widget, callback)
    try:
        widget.after(delay_ms, target.attach)
    except Exception:
        target.attach()
    return target


# ------------------------------------------------------------------ 测试辅助
class DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", wintypes.DWORD), ("pt", wintypes.POINT),
                ("fNC", wintypes.BOOL), ("fWide", wintypes.BOOL)]


def post_files_message(hwnd: int, paths: list[str]) -> int:
    """构造一个 HDROP 结构并 PostMessage(WM_DROPFILES)，用于自动化验证拖放链路。

    只在测试里用：模拟资源管理器把若干路径投进窗口。
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    GMEM_MOVEABLE, GMEM_ZEROINIT = 0x0040, 0x0040
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

    blob = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    head = DROPFILES(pFiles=ctypes.sizeof(DROPFILES), pt=wintypes.POINT(0, 0),
                     fNC=False, fWide=True)
    size = ctypes.sizeof(DROPFILES) + len(blob)
    hglobal = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, size)
    if not hglobal:
        raise OSError("GlobalAlloc 失败")
    ptr = kernel32.GlobalLock(hglobal)
    try:
        ctypes.memmove(ptr, ctypes.byref(head), ctypes.sizeof(head))
        ctypes.memmove(ptr + ctypes.sizeof(head), blob, len(blob))
    finally:
        kernel32.GlobalUnlock(hglobal)
    user32.PostMessageW(wintypes.HWND(hwnd), WM_DROPFILES,
                        wintypes.WPARAM(hglobal), wintypes.LPARAM(0))
    return int(hglobal)
