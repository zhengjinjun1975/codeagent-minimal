# -*- coding: utf-8 -*-
"""stats.py — 统计原子: 描述统计 / 流式统计 / 趋势 / 异常检测 / 控制图 / 相关。

极简原则: 标准库(仅 math), 零依赖。数据分析(制造业/地球物理/通用)复用。
从 solo-agent-kit/factory/stats.py 提炼, 覆盖: 描述/趋势/异常/SPC/相关。
"""
from __future__ import annotations

import math


def _num(v) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _quantile(vals: list, q: float) -> float:
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo, hi = int(k), int(k) + 1
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def describe(values: list) -> dict:
    """描述性统计: count/min/max/mean/median/std/p25/p75。"""
    vals = [float(v) for v in values if _num(v)]
    if not vals:
        return {"count": 0}
    s = sorted(vals)
    n = len(s)
    mean = sum(s) / n
    var = sum((x - mean) ** 2 for x in s) / n
    return {
        "count": n, "min": s[0], "max": s[-1],
        "mean": round(mean, 3), "median": _quantile(s, 0.5),
        "std": round(math.sqrt(var), 3), "p25": _quantile(s, 0.25),
        "p75": _quantile(s, 0.75),
    }


def describe_stream(values) -> dict:
    """流式描述统计(Welford 在线, O(1) 内存, 大文件友好)。

    values 可为生成器。流式无法算中位数(需排序), 诚实标注 streaming=True。
    """
    count = 0
    mean = 0.0
    m2 = 0.0
    minv = maxv = None
    for v in values:
        try:
            x = float(v)
        except (ValueError, TypeError):
            continue
        count += 1
        delta = x - mean
        mean += delta / count
        m2 += delta * (x - mean)
        minv = x if minv is None or x < minv else minv
        maxv = x if maxv is None or x > maxv else maxv
    if count == 0:
        return {"count": 0}
    variance = m2 / count if count > 1 else 0.0
    return {"count": count, "min": minv, "max": maxv,
            "mean": round(mean, 3), "std": round(math.sqrt(variance), 3),
            "streaming": True}


def trend(values: list) -> dict:
    """线性回归斜率: 判断上升/下降/平稳。"""
    vals = [float(v) for v in values if _num(v)]
    if len(vals) < 2:
        return {"slope": 0, "direction": "insufficient"}
    n = len(vals)
    x_mean = (n - 1) / 2.0
    y_mean = sum(vals) / n
    num = sum((i - x_mean) * (vals[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n)) or 1
    slope = num / den
    thr = 0.01 * (y_mean or 1)
    direction = "rising" if slope > thr else ("falling" if slope < -thr else "flat")
    return {"slope": round(slope, 4), "direction": direction}


def detect_anomaly(values: list, method: str = "zscore", threshold: float = 3.0) -> list:
    """异常检测: 返回异常点 [{index, value, zscore?}]。

    method: 'zscore' 距均值>threshold*σ / 'iqr' 超出 Q1-1.5IQR..Q3+1.5IQR
    """
    vals = [float(v) for v in values if _num(v)]
    if len(vals) < 4:
        return []
    anomalies = []
    if method == "zscore":
        mean = sum(vals) / len(vals)
        std = math.sqrt(sum((x - mean) ** 2 for x in vals) / len(vals)) or 1
        for i, v in enumerate(vals):
            if abs(v - mean) > threshold * std:
                anomalies.append({"index": i, "value": round(v, 3),
                                  "zscore": round((v - mean) / std, 2)})
    elif method == "iqr":
        q1, q3 = _quantile(vals, 0.25), _quantile(vals, 0.75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        for i, v in enumerate(vals):
            if not (lo <= v <= hi):
                anomalies.append({"index": i, "value": round(v, 3)})
    return anomalies


def control_chart(values: list) -> dict:
    """SPC 控制图(X-bar): 中心线 + UCL/LCL(mean±3σ) + 失控点 + 数据点。"""
    vals = [float(v) for v in values if _num(v)]
    if len(vals) < 2:
        return {"error": "insufficient data"}
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((x - mean) ** 2 for x in vals) / len(vals))
    ucl, lcl = mean + 3 * std, mean - 3 * std
    ooc = [{"index": i, "value": round(v, 3), "violation": "UCL" if v > ucl else "LCL"}
           for i, v in enumerate(vals) if v > ucl or v < lcl]
    points = [{"index": i, "value": round(v, 3)} for i, v in enumerate(vals[:100])]
    return {"mean": round(mean, 3), "std": round(std, 3),
            "ucl": round(ucl, 3), "lcl": round(lcl, 3),
            "out_of_control": ooc, "points": points}


def correlation(a: list, b: list) -> float:
    """皮尔逊相关系数(两列相关, 如温度↔能耗)。"""
    x = [float(i) for i in a if _num(i)]
    y = [float(i) for i in b if _num(i)]
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    x, y = x[:n], y[:n]
    mx, my = sum(x) / n, sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = math.sqrt(sum((v - mx) ** 2 for v in x))
    dy = math.sqrt(sum((v - my) ** 2 for v in y))
    return round(num / (dx * dy or 1), 3)
