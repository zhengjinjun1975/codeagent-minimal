#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ddmin.py — 失败输入最小化（Zeller 经典 ddmin，黑箱，纯算法）。

用法：ddmin(full_list, test)  → 最小子集
  test(subset) 必须返回 True 当且仅当子集仍能复现失败（黑箱 predicate）。
不读源码、不依赖任何工具 —— 这是黑箱调试状态机的第 2 步（MINIMIZE）。
"""


def ddmin(c, test, granularity=2):
    """Zeller ddmin：按 granularity 分块，逐块剔除仍能复现失败的块。"""
    if not isinstance(c, list):
        c = list(c)
    if len(c) == 1:
        return c
    n = granularity
    while len(c) >= 2:
        subsets = _split(c, n)
        reduced = False
        for s in subsets:
            try:
                still_fails = bool(test(s))
            except Exception:  # noqa: BLE001 谓词异常视为未复现，跳过该块
                still_fails = False
            if still_fails:
                c = s
                n = max(granularity, n - 1)
                reduced = True
                break
        if not reduced:
            if n >= len(c):
                break
            n = min(len(c), n * 2)
    return c


def _split(c, n):
    """把 c 等分为 n 块（每块尽量均等）。"""
    if n <= 1:
        return [c]
    k = (len(c) + n - 1) // n
    return [c[i:i + k] for i in range(0, len(c), k)]


def lines_keep_signature(text, errsig, max_iter=12):
    """黑箱文本最小化：按行 ddmin，保持错误签名（errsig 关键词）仍出现。

    返回最小行子集（str）。谓词 = 子集文本仍含全部 errsig 关键词。
    """
    lines = text.split("\n")
    if not errsig:
        return text

    def test(sub):
        joined = "\n".join(sub)
        return all(k in joined for k in errsig if k)

    reduced = ddmin(lines, test)
    return "\n".join(reduced)