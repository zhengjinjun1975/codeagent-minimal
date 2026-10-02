# -*- coding: utf-8 -*-
"""geophys.py — 地球物理原子: LAS测井解析 / 深度-时间 / 曲线操作。

极简原则: 纯标准库(不依赖 obspy/welly/segyio), 轻量可独立用。
覆盖: LAS文件文本解析, 深度-时间转换, 测井曲线极值/归一化/平滑。
大型处理(地震数据/波形)用 domain-libs 的 obspy/welly(已路由), 此处只沉淀轻量原子。
"""
from __future__ import annotations

import math
import re


# ---- LAS 测井文件解析(标准库) ----
def las_parse(path: str) -> dict:
    """解析 LAS 文件: 返回 {well:{}, curves:[(mnem,unit,desc)], data:[{mnem:val,...}]}。

    LAS 格式: ~V/ ~W/ ~C/ ~A/ ~D 段。纯文本标准库解析, 不依赖 welly。
    """
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    well = {}
    curves = []
    data_start = None
    data_rows = []

    section = None
    for i, ln in enumerate(lines):
        ln = ln.strip()
        if ln.startswith("~V"):
            section = "V"
        elif ln.startswith("~W"):
            section = "W"
        elif ln.startswith("~C"):
            section = "C"
            continue
        elif ln.startswith("~A"):
            section = "A"
        elif ln.startswith("~D"):
            section = "D"
            data_start = i + 1
        elif section == "V":
            # 井头信息: 曲线.字段 单位 : 描述
            m = re.match(r"(\w+)\.(\w+)\s+([^:]*):\s*(.*)", ln)
            if m:
                well[m.group(2).lower()] = m.group(4).strip()
        elif section == "C":
            # 曲线定义: 助记符.单元 代码 描述
            m = re.match(r"(\w+)\.(\S+)\s+(\w+)\s*:\s*(.*)", ln)
            if m:
                curves.append({"mnem": m.group(1), "unit": m.group(2),
                               "code": m.group(3), "desc": m.group(4).strip()})
        elif section == "D" and data_start and i >= data_start:
            if ln:
                data_rows.append([float(x) if _isnum(x) else x for x in ln.split()])

    # 组装数据: 每行对应 curves 的每列
    data = []
    mnems = [c["mnem"] for c in curves]
    for row in data_rows:
        data.append(dict(zip(mnems, row)))

    return {"well": well, "curves": curves, "data": data}


def _isnum(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


# ---- 深度-时间转换 ----
def depth_to_time(depth: float, velocity: float) -> float:
    """单程深度→时间(ms): t = depth/velocity*1000。"""
    return depth / velocity * 1000.0 if velocity else 0.0


def time_to_depth(time_ms: float, velocity: float) -> float:
    """时间(ms)→深度: depth = time_ms/1000*velocity。"""
    return time_ms / 1000.0 * velocity


# ---- 曲线操作 ----
def curve_resample(curve: list, new_n: int) -> list:
    """曲线重采样: 线性插值到 new_n 个点。"""
    n = len(curve)
    if n < 2 or new_n <= 0:
        return list(curve)
    out = []
    for i in range(new_n):
        pos = i * (n - 1) / (new_n - 1)
        lo = int(pos)
        frac = pos - lo
        hi = min(lo + 1, n - 1)
        out.append(round(curve[lo] + (curve[hi] - curve[lo]) * frac, 4))
    return out


def curve_normalize(curve: list, lo: float = 0.0, hi: float = 1.0) -> list:
    """曲线最小-最大归一化到 [lo, hi]。"""
    vals = [float(v) for v in curve]
    if not vals:
        return []
    mn, mx = min(vals), max(vals)
    span = mx - mn or 1.0
    return [round(lo + (v - mn) / span * (hi - lo), 4) for v in vals]


def curve_smooth(curve: list, window: int = 3) -> list:
    """简单滑动平均平滑(奇数窗口)。"""
    w = max(1, window | 1)  # 保证奇数
    if len(curve) < w:
        return list(curve)
    out = []
    half = w // 2
    for i in range(len(curve)):
        lo = max(0, i - half)
        hi = min(len(curve), i + half + 1)
        out.append(round(sum(curve[lo:hi]) / (hi - lo), 4))
    return out


def curve_extremes(curve: list, mode: str = "max") -> list:
    """曲线极值点索引: mode='max'/'min'。返回局部极值索引列表。"""
    n = len(curve)
    if n < 3:
        return []
    out = []
    for i in range(1, n - 1):
        if mode == "max" and curve[i] > curve[i - 1] and curve[i] > curve[i + 1]:
            out.append(i)
        elif mode == "min" and curve[i] < curve[i - 1] and curve[i] < curve[i + 1]:
            out.append(i)
    return out
