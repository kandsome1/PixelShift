"""验证原生拖放链路：构造 WM_DROPFILES 消息投进窗口，看文件是否真的进了列表。

    python tools/dnd_test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pixelshift.dnd import post_files_message  # noqa: E402
from pixelshift.gui import PixelShiftApp  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def pump(app, times: int = 40, delay: float = 0.03) -> None:
    for _ in range(times):
        app.update_idletasks()
        app.update()
        time.sleep(delay)


def main() -> int:
    app = PixelShiftApp()
    app.geometry("1200x820+60+60")
    pump(app, 60)                                  # 等 attach 与 after 回调

    target = app._drop_target
    print("\n== 0. 拖放目标挂载 ==")
    check("DropTarget 已创建", target is not None)
    check("子类化成功", bool(target and target.ok), getattr(target, "error", ""))
    check("拿到顶层 HWND", bool(target and target.hwnd), hex(target.hwnd) if target else "-")
    if not (target and target.ok):
        print("\n挂载失败，后续用例跳过")
        app.destroy()
        return 1
    print(f"         HWND = 0x{target.hwnd:X}")

    hwnd = target.hwnd
    a = ROOT / "samples" / "photo_exif.jpg"
    b = ROOT / "samples" / "clip_h264.mp4"

    print("\n== 1. 拖入两个文件 ==")
    base = len(app.jobs)
    post_files_message(hwnd, [str(a), str(b)])
    pump(app, 40)
    check("列表增加 2 项", len(app.jobs) == base + 2, f"{base} -> {len(app.jobs)}")
    names = [j.src.name for j in app.jobs]
    check("文件名正确", {"photo_exif.jpg", "clip_h264.mp4"} <= set(names), names)
    check("表格行数一致", len(app.tree.get_children()) == len(app.jobs))

    print("\n== 2. 重复拖入同一批（应去重）==")
    before = len(app.jobs)
    post_files_message(hwnd, [str(a), str(b)])
    pump(app, 30)
    check("没有重复添加", len(app.jobs) == before, f"{before} -> {len(app.jobs)}")

    print("\n== 3. 拖入文件夹（递归扫描）==")
    before = len(app.jobs)
    post_files_message(hwnd, [str(ROOT / "samples")])
    pump(app, 40)
    added = len(app.jobs) - before
    check("文件夹被展开", added > 0, f"新增 {added}")
    print(f"         新增 {added} 个，列表共 {len(app.jobs)} 个")

    print("\n== 4. 拖入不支持的类型 ==")
    junk = ROOT / "tools" / "_dnd_junk.txt"
    junk.write_text("x", encoding="utf-8")
    before = len(app.jobs)
    post_files_message(hwnd, [str(junk)])
    pump(app, 30)
    check("txt 不被加入", len(app.jobs) == before, f"{before} -> {len(app.jobs)}")
    junk.unlink(missing_ok=True)

    print("\n== 5. 拖入不存在的路径 ==")
    before = len(app.jobs)
    post_files_message(hwnd, [str(ROOT / "samples" / "not_exist_12345.jpg")])
    pump(app, 25)
    check("不存在路径被忽略", len(app.jobs) == before)

    print("\n== 6. 拖放提示与日志 ==")
    check("提示标签有文字", bool(app.lbl_drop_hint.cget("text")), app.lbl_drop_hint.cget("text"))
    log_text = app.log_box.get("1.0", "end")
    check("日志记录拖放启用", "拖放已启用" in log_text)
    check("日志记录添加条数", "已添加" in log_text)

    print("\n== 7. detach ==")
    target.detach()
    check("detach 后 ok=False", target.ok is False)

    app.destroy()
    print(f"\n===== 结果：通过 {PASS} · 失败 {FAIL} =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
