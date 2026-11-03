"""文件哈希 / 体积统计。"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, asdict
from pathlib import Path

CHUNK = 1 << 20


@dataclass
class FileDigest:
    size: int = 0
    md5: str = ""
    sha1: str = ""

    @property
    def short(self) -> str:
        return self.md5[:12] if self.md5 else "-"

    def as_row(self) -> dict:
        return asdict(self)


def digest_file(path: str | Path) -> FileDigest:
    """一次读取同时算 MD5 / SHA1 / 体积。失败时返回空摘要。"""
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    size = 0
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                md5.update(chunk)
                sha1.update(chunk)
    except OSError:
        return FileDigest()
    return FileDigest(size=size, md5=md5.hexdigest(), sha1=sha1.hexdigest())


def human_size(num: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < step or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= step
    return f"{num:.1f} TB"


def unique_path(target: Path, exists_check=None) -> Path:
    """若目标已存在，追加 _2 / _3 … 直到不冲突。"""
    exists_check = exists_check or (lambda p: p.exists())
    if not exists_check(target):
        return target
    stem, suffix, parent = target.stem, target.suffix, target.parent
    idx = 2
    while True:
        cand = parent / f"{stem}_{idx}{suffix}"
        if not exists_check(cand):
            return cand
        idx += 1


# ---------------------------------------------------------------- 感知哈希
def _dct_matrix(n: int) -> list[list[float]]:
    import math
    mat = []
    for k in range(n):
        row = []
        alpha = math.sqrt(1.0 / n) if k == 0 else math.sqrt(2.0 / n)
        for i in range(n):
            row.append(alpha * math.cos(math.pi * (2 * i + 1) * k / (2 * n)))
        mat.append(row)
    return mat


_DCT_CACHE: dict[int, list[list[float]]] = {}

# 16 个 32x32 归一化像素（只取前 8x8 频率…这里用 16x16 降本）
_PHASH_SIDE = 16


def perceptual_hash(path: str | Path) -> tuple[str, str]:
    """返回 (pHash, aHash) 的 16 进制串；失败返回 ("","")。

    pHash: 16x16 灰度 → 2D DCT → 取左上 8x8 → 与中位数比较 → 64 bit
    aHash: 8x8 灰度 → 与均值比较 → 64 bit
    两种都能反映"画面本身"是否被改动，与文件字节哈希互补。
    """
    try:
        from PIL import Image
    except Exception:
        return "", ""
    side = _PHASH_SIDE
    try:
        with Image.open(path) as im:
            g = im.convert("L").resize((side, side), Image.LANCZOS)
            px = list(g.getdata())
    except Exception:
        return "", ""

    mat = _DCT_CACHE.get(side)
    if mat is None:
        mat = _dct_matrix(side)
        _DCT_CACHE[side] = mat
    # 行变换
    rows = []
    for y in range(side):
        base = y * side
        src = px[base:base + side]
        rows.append([sum(mat[k][i] * src[i] for i in range(side)) for k in range(side)])
    # 列变换
    coeff = [[0.0] * side for _ in range(side)]
    for x in range(side):
        col = [rows[y][x] for y in range(side)]
        for k in range(side):
            coeff[k][x] = sum(mat[k][i] * col[i] for i in range(side))

    low = []
    for y in range(8):
        for x in range(8):
            if y == 0 and x == 0:
                continue
            low.append(coeff[y][x])
    if not low:
        return "", ""
    ordered = sorted(low)
    mid = ordered[len(ordered) // 2]
    bits = 0
    for v in low:
        bits = (bits << 1) | (1 if v > mid else 0)
    phash = f"{bits:016x}"

    try:
        with Image.open(path) as im:
            tiny = list(im.convert("L").resize((8, 8), Image.LANCZOS).getdata())
        avg = sum(tiny) / len(tiny)
        abits = 0
        for v in tiny:
            abits = (abits << 1) | (1 if v > avg else 0)
        ahash = f"{abits:016x}"
    except Exception:
        ahash = ""
    return phash, ahash


def hash_distance(h1: str, h2: str) -> int | None:
    """两个 16 进制感知哈希的汉明距离；空值返回 None。"""
    if not h1 or not h2:
        return None
    try:
        v = int(h1, 16) ^ int(h2, 16)
    except ValueError:
        return None
    return bin(v).count("1")


def same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return str(a).lower() == str(b).lower()
