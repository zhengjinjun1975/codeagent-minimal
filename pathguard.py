#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pathguard.py — CodeAgent 路径安全守卫（纯 stdlib，数据不出厂）。

路径穿越防护：任意"读/扫/写"目标路径必须先经 safe_resolve / safe_read_text 归一化，
并对可选 base 根目录做包含性校验（normpath + realpath 双归一化），杜绝 `../`、
绝对路径、符号链接逃逸等路径穿越读取/写入。

被 bug_deep.py / security_scan.py / 各原子壳复用，单点统一防护。
"""
import os
import re
from pathlib import Path

__all__ = ["safe_resolve", "safe_read_text", "assert_within"]

# Windows 绝对路径盘符(如 C:/ 或 C:\)；在 Linux 上反斜杠/盘符不被视为路径分隔符，
# 会被当成普通相对路径而逃逸检测，故必须显式识别并按"绝对路径逃逸"拒绝。
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _normalize(path: str) -> str:
    """把 Windows 反斜杠统一为平台分隔符，使反斜杠形式的 ../.. 在 Linux 也能被 normpath 正确解析。"""
    return path.replace("\\", "/")


def safe_resolve(path, base=None):
    """归一化并解析 path 到绝对路径。

    参数:
      path : 原始路径字符串或 Path。
      base : 可选。若提供，解析后的真实路径必须位于 base 之内（含 base 自身），
             否则抛 ValueError（路径穿越）。

    返回: Path（绝对、归一化、真实路径 realpath）。
    """
    if path is None:
        raise ValueError("路径为空")
    p = str(path)
    if not isinstance(p, str) or not p.strip():
        raise ValueError("无效路径")
    p = _normalize(p)
    # 非 Windows 平台上，Windows 盘符绝对路径(C:/、C:\)本应是"绝对路径逃逸"，
    # 但 Linux 把 `C:/` 当普通相对目录名而逃逸检测，故显式按逃逸拒绝。
    # Windows 上 `C:/` 是合法绝对路径，交由下方 normpath/realpath 包含性校验处理。
    if os.name != "nt" and _WIN_DRIVE_RE.match(p):
        raise ValueError(f"绝对路径逃逸被拒绝: {p!r}")
    if base is None:
        # 无根约束：仅做绝对化 + 归一化（防相对遍历把裸 ``../x`` 当可读目标）
        return Path(os.path.abspath(os.path.normpath(os.path.expanduser(p))))
    basep = os.path.abspath(os.path.normpath(os.path.expanduser(str(base))))
    target = os.path.abspath(os.path.join(basep, p))
    norm = os.path.normpath(target)
    real = os.path.realpath(norm)
    base_real = os.path.realpath(basep)
    if not (real == base_real or real.startswith(base_real + os.sep)):
        raise ValueError(f"路径穿越被拒绝: {p!r} 逃逸出根目录 {base_real}")
    return Path(real)


def assert_within(root, path):
    """断言 path（绝对路径）位于 root 之内；否则抛 ValueError。返回真实 Path。"""
    root_p = Path(os.path.realpath(str(root)))
    target = Path(os.path.realpath(str(path)))
    if not (target == root_p or target.is_relative_to(root_p)):
        raise ValueError(f"路径穿越: {target} 不在根目录 {root_p} 内")
    return target


def safe_read_text(path, base=None, encoding="utf-8", errors="ignore"):
    """安全读取文本文件：先做路径穿越防护，再读取。返回文件内容字符串。"""
    p = safe_resolve(path, base=base)
    if not p.is_file():
        raise FileNotFoundError(f"目标文件不存在或非文件: {p}")
    return p.read_text(encoding=encoding, errors=errors)
