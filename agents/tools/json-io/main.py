#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
json-io — 通用工具原子: JSON/JSONL 读写(包装 pycommon)
提供: json.load / json.save / jsonl.read / jsonl.write / jsonl.append / jsonl.dedup
纯 stdlib + pycommon, 数据不出厂。
用法(被 agent_runtime 调用): run(action="load", path=...)
"""
import sys, os

sys.path.insert(0, r"E:/scripts/lib")
from pycommon import load_json, write_json, read_jsonl, write_jsonl, append_jsonl, dedup_jsonl_by

try:
    from atomic_base import AtomicAgent
except Exception:
    AtomicAgent = None  # 独立运行(非 loader)时无基类, 仅 run() 可用

ACTIONS = {
    "load": lambda path, **k: load_json(path, default=k.get("default")),
    "save": lambda path, obj, **k: write_json(path, obj, ensure_ascii=k.get("ensure_ascii", False)),
    "jsonl.read": lambda path, **k: read_jsonl(path),
    "jsonl.write": lambda path, rows, **k: write_jsonl(path, rows),
    "jsonl.append": lambda path, row, **k: append_jsonl(path, row),
    "jsonl.dedup": lambda p_in, p_out, key, **k: dedup_jsonl_by(p_in, p_out, key),
    # 别名: provides 声明 json.load/json.save, 与 run(action="load") 对齐
    "json.load": lambda path, **k: load_json(path, default=k.get("default")),
    "json.save": lambda path, obj, **k: write_json(path, obj, ensure_ascii=k.get("ensure_ascii", False)),
}


def run(action, **kwargs):
    fn = ACTIONS.get(action)
    if not fn:
        return {"error": f"unknown action: {action}", "available": sorted(ACTIONS)}
    return {"action": action, "result": fn(**kwargs)}


# ---------- AtomicAgent 壳(loader 可加载复用) ----------
if AtomicAgent is not None:
    class JsonIoAgent(AtomicAgent):
        name = "json-io"
        version = "0.1.0"
        domain = "tools"
        description = ("通用工具原子(省重复 P1-3): JSON/JSONL 读写去重, 包装 E:/scripts/lib/pycommon.py, "
                       "纯 stdlib 数据不出厂。收敛散落脚本手写 json helper。")
        provides = ["json.load", "json.save", "jsonl.read", "jsonl.write", "jsonl.append", "jsonl.dedup"]
        depends_on = []
        inputs = ["action", "path", "obj", "rows", "key", "default", "p_in", "p_out"]
        outputs = ["result", "error"]

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
    print(_j.dumps(run(action="load", path="__nope__"), ensure_ascii=False, default=str))
