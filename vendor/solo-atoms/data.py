# -*- coding: utf-8 -*-
"""data.py — 数据原子: 数值判断 / 列类型推断 / 流式CSV读。

极简原则: 标准库, 零依赖, 数据类模块(清洗/分析/建模)通用。
"""
from __future__ import annotations

import csv
import os


def num(v) -> bool:
    """判断值能否转 float。"""
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def guess_col_type(v: str) -> str:
    """推断单值列类型: integer/float/bool/date/empty/text。"""
    s = str(v).strip()
    if not s:
        return "empty"
    if s.lower() in ("true", "false"):
        return "bool"
    if num(s):
        return "integer" if float(s).is_integer() else "float"
    # 简单日期(YYYY-MM-DD 或 YYYY/M/D)
    try:
        import datetime
        datetime.datetime.strptime(s[:10], "%Y-%m-%d")
        return "date"
    except ValueError:
        pass
    return "text"


def iter_csv(path: str):
    """流式读 CSV: 逐行 yield dict, 不整文件入内存。

    大数据 CSV 用这个(替代 csv.DictReader 一次性读全)。
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def load_csv(path: str, limit: int = None) -> list:
    """读 CSV 为行列表(可选 limit)。小文件便捷版, 大文件用 iter_csv。"""
    rows = []
    for i, row in enumerate(iter_csv(path)):
        if limit is not None and i >= limit:
            break
        rows.append(row)
    return rows
