#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data-tools — 通用工具原子(省重复 P1-3): 数据集处理(包装 pycommon + scripts/data)
提供: data.stats / data.csv.read / data.csv.write / data.dedup_lines / data.sample / data.clean_fields

来源: E:/scripts/data/20260824-data-clean-jsonl.py (高频数据集处理脚本)
加壳不改核心: ACTIONS + run() 保留原接口; 新增 AtomicAgent 子类使 loader 可加载复用。
"""
import sys, os, random

sys.path.insert(0, r"E:/scripts/lib")
from pycommon import read_csv, write_csv, dedup_lines, read_jsonl, write_jsonl, clean_cn

try:
    from atomic_base import AtomicAgent
except Exception:
    AtomicAgent = None  # 独立运行(非 loader)时无基类, 仅 run() 可用

ACTIONS = {}


def _stats(p):
    rows = read_jsonl(p)
    n = len(rows)
    keys = {}
    for r in rows[:5000]:
        if isinstance(r, dict):
            for k in r:
                keys[k] = keys.get(k, 0) + 1
    return {"rows": n, "fields": dict(list(sorted(keys.items(), key=lambda x: -x[1]))[:10])}


def _sample(p, n=100, seed=42):
    """固定种子随机抽样 jsonl, 返回抽样行。可重复。"""
    rows = read_jsonl(p)
    if not rows:
        return {"sample": [], "n": 0}
    random.seed(seed)
    k = min(int(n), len(rows))
    return {"sample": random.sample(rows, k), "n": k, "total": len(rows)}


def _clean_fields(p, fields, remove_ai_talk=False):
    """对 jsonl 指定字段做中文清洗(clean_cn), 写回原文件(覆盖, 幂等可重复)。"""
    fields = [f.strip() for f in fields.split(",") if f.strip()] if isinstance(fields, str) else list(fields or [])
    rows = read_jsonl(p)
    cnt = 0
    for r in rows:
        if isinstance(r, dict):
            for fld in fields:
                if isinstance(r.get(fld), str) and r[fld].strip():
                    r[fld] = clean_cn(r[fld], remove_ai_talk=remove_ai_talk)
                    cnt += 1
    write_jsonl(p, rows)
    return {"cleaned": cnt, "fields": fields, "rows": len(rows)}


ACTIONS["data.stats"] = lambda p, **k: _stats(p)
ACTIONS["data.csv.read"] = lambda p, delimiter=",", **k: read_csv(p, delimiter=delimiter)
ACTIONS["data.csv.write"] = lambda p, rows, delimiter=",", **k: write_csv(p, rows, delimiter=delimiter)
ACTIONS["data.dedup_lines"] = lambda p_in, p_out=None, **k: dedup_lines(p_in, p_out)
ACTIONS["data.sample"] = lambda p, n=100, seed=42, **k: _sample(p, n=n, seed=seed)
ACTIONS["data.clean_fields"] = lambda p, fields, remove_ai_talk=False, **k: _clean_fields(p, fields, remove_ai_talk=remove_ai_talk)


def run(action, **kwargs):
    fn = ACTIONS.get(action)
    if not fn:
        return {"error": f"unknown action: {action}", "available": sorted(ACTIONS)}
    try:
        return {"action": action, "result": fn(**kwargs)}
    except Exception as e:
        return {"action": action, "error": str(e)}


# ---------- AtomicAgent 壳(loader 可加载复用) ----------
if AtomicAgent is not None:
    class DataToolsAgent(AtomicAgent):
        name = "data-tools"
        version = "0.1.0"
        domain = "tools"
        description = ("通用工具原子(省重复 P1-3): 数据集统计/CSV 读写/行去重/固定种子抽样/字段中文清洗, "
                       "包装 pycommon + E:/scripts/data。纯 stdlib 数据不出厂。")
        provides = ["data.stats", "data.csv.read", "data.csv.write", "data.dedup_lines",
                    "data.sample", "data.clean_fields"]
        depends_on = []
        inputs = ["action", "p", "p_in", "p_out", "rows", "fields", "n", "seed", "delimiter", "remove_ai_talk"]
        outputs = ["result", "error", "rows", "fields", "sample", "cleaned"]

        def _exec(self, cap, **kw):
            r = run(action=cap, **kw)
            if "error" in r:
                return {"ok": False, "data": {}, "error": r["error"], "degraded": True}
            return {"ok": True, "data": r}

        def _register_defaults(self):
            for cap in self.provides:
                self.register(cap, (lambda c=cap: (lambda **kw: self._exec(c, **kw)))())


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(run(action="data.stats", p="__nope__.jsonl"), default=str))
