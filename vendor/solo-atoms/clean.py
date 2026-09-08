# -*- coding: utf-8 -*-
"""clean.py — 数据清洗原子: 类型推断 / 去重 / 缺失处理 / 异常剔除 / 报告。

极简原则: 标准库(仅 csv/json/math/re), 零依赖。
制造业/地球物理/通用数据清洗复用。从 solo-agent-kit/factory/clean.py 提炼。
"""
from __future__ import annotations

import csv
import math
import re


def guess_type(value: str) -> str:
    """猜值类型: missing/integer/float/date/text。"""
    v = str(value).strip()
    if not v:
        return "missing"
    if re.fullmatch(r"-?\d+", v):
        return "integer"
    if re.fullmatch(r"-?\d+\.\d+", v):
        return "float"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return "date"
    return "text"


def _isnum(v) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _quantile(vals: list, q: float) -> float:
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo, hi = int(k), int(k) + 1
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def clean(rows: list, fill_missing: str = "drop",
          outlier_method: str = "iqr", numeric_cols: list = None) -> tuple:
    """数据清洗主流程。返回 (clean_rows, report)。

    fill_missing: 'drop'删缺失行 / 'zero'填0 / 'mean'填均值
    outlier_method: 'iqr'四分位距 / 'zscore'标准差
    numeric_cols: 指定数值列；None 自动推断
    report: {rows, dropped_dup, filled_missing, dropped_outlier, types, missing_by_col}
    """
    report = {"rows": len(rows), "dropped_dup": 0, "filled_missing": 0,
              "dropped_outlier": 0, "types": {}, "missing_by_col": {}}
    if not rows:
        return rows, report

    cols = list(rows[0].keys())
    # 类型推断(跳过缺失)
    types = {}
    for c in cols:
        non_missing = [r.get(c, "").strip() for r in rows if str(r.get(c, "")).strip()]
        types[c] = guess_type(non_missing[0]) if non_missing else "missing"
    report["types"] = types

    num_cols = numeric_cols or [c for c, t in types.items() if t in ("integer", "float")]
    for c in num_cols:
        report["missing_by_col"][c] = sum(1 for r in rows if not str(r.get(c, "")).strip())

    # 1. 去重
    seen = set()
    dedup = []
    for r in rows:
        key = tuple(sorted((k, str(r.get(k, ""))) for k in r))
        if key in seen:
            report["dropped_dup"] += 1
            continue
        seen.add(key)
        dedup.append(r)
    rows = dedup

    # 2. 缺失值处理
    cleaned = []
    for r in rows:
        row = dict(r)
        skip = False
        for c in cols:
            if not str(row.get(c, "")).strip():
                if fill_missing == "drop":
                    skip = True
                    break
                elif fill_missing == "zero":
                    row[c] = "0"
                    report["filled_missing"] += 1
                elif fill_missing == "mean" and c in num_cols:
                    vals = [float(x[c]) for x in rows if str(x.get(c, "")).strip() and _isnum(x.get(c))]
                    row[c] = str(sum(vals) / len(vals)) if vals else "0"
                    report["filled_missing"] += 1
        if not skip:
            cleaned.append(row)
    rows = cleaned

    # 3. 异常值(IQR 统一过滤防污染)
    if outlier_method == "iqr":
        to_drop = set()
        for c in num_cols:
            vals = [float(r[c]) for r in rows if _isnum(r.get(c))]
            if len(vals) < 4:
                continue
            q1, q3 = _quantile(vals, 0.25), _quantile(vals, 0.75)
            iqr = q3 - q1
            if iqr == 0:
                continue
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            for idx, r in enumerate(rows):
                if _isnum(r.get(c)) and not (lo <= float(r[c]) <= hi):
                    to_drop.add(idx)
        report["dropped_outlier"] += len(to_drop)
        rows = [r for i, r in enumerate(rows) if i not in to_drop]
    elif outlier_method == "zscore":
        for c in num_cols:
            vals = [float(r[c]) for r in rows if _isnum(r.get(c))]
            if len(vals) < 3:
                continue
            mean = sum(vals) / len(vals)
            std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) or 1
            rows = [r for r in rows if not (_isnum(r.get(c)) and abs(float(r[c]) - mean) > 3 * std)]

    return rows, report


def load_csv(path: str) -> list:
    """读 CSV 为行列表(list[dict])。"""
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_csv(rows: list, path: str) -> None:
    """写行列表为 CSV。"""
    if not rows:
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
