#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lab-loop 原子（循环控制：重试 / 迭代，带反馈环）。
加壳不改核心，纯 stdlib，数据不出私域。

能力:
  loop.retry   — 重试控制: 对目标能力按 attempts 次重试，失败把上次错误反馈进下一轮再试。
  loop.iterate — 迭代控制: 对 items 逐项执行目标能力，上轮结果反馈给下一轮（反馈环）。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# 可移植路径解析（P1-1 去硬编码 E:\）：扩展在 lab/extensions/<name>/ 下
#   LAB = 上级的上级 = lab/ ；ROOT = 再上级 = 项目根
LAB = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(LAB)
for _p in (ROOT, LAB):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from atomic_base import AtomicAgent
from atom_runner import run_capability

# 嵌套执行时用当前配置的 codeagent_root（感知项目实际根，跨机可移植）
def _codeagent_root():
    try:
        from lab_config import get_config
        return get_config().codeagent_root()
    except Exception:  # noqa: BLE001
        return ROOT


def _coerce(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


class LabLoopAgent(AtomicAgent):
    name = "lab-loop"
    version = "0.1.0"
    domain = "loop"
    description = "循环控制原子: 重试(loop.retry) / 迭代(loop.iterate), 带反馈环, 加壳不改核心。"
    provides = ["loop.retry", "loop.iterate"]
    depends_on = []
    inputs = ["target_atom", "target_cap", "attempts", "params", "items", "item_param"]
    outputs = ["ok", "attempts_used", "final", "results", "iterations", "summary"]

    def _register_defaults(self):
        self.register("loop.retry", self._retry)
        self.register("loop.iterate", self._iterate)

    def _retry(self, target_atom=None, target_cap=None, attempts=2, params=None):
        atom = target_atom or "code-test"
        cap = target_cap or "test.run"
        n = max(1, int(_coerce(attempts) or 2))
        p = dict(_coerce(params) or {})
        p.pop("_retry_error", None)
        last, attempts_used = None, 0
        for i in range(1, n + 1):
            attempts_used = i
            last = run_capability(atom, cap, p, codeagent_root=_codeagent_root())
            if last and last.get("ok"):
                break
            # 反馈环: 把上次错误/失败证据反馈进下一轮
            p["_retry_error"] = str((last or {}).get("error"))[:300]
            p["_retry_round"] = i
            time.sleep(0.15)
        ok = bool(last and last.get("ok"))
        return {"ok": ok,
                "data": {"attempts_used": attempts_used,
                         "final": (last or {}).get("data"),
                         "ok": ok, "error": (last or {}).get("error"),
                         "summary": "重试控制: 目标 {0}/{1}, 尝试{2}轮, {3}".format(
                             atom, cap, attempts_used, "成功" if ok else "失败")}}

    def _iterate(self, target_atom=None, target_cap=None, items=None, params=None, item_param="item"):
        atom = target_atom or "code-test"
        cap = target_cap or "test.run"
        items = _coerce(items)
        if items is None:
            items = []
        if isinstance(items, str):
            items = [items]
        base = dict(_coerce(params) or {})
        base.pop("_retry_error", None)
        results = []
        ok = True
        for idx, item in enumerate(items):
            p = dict(base)
            p[item_param or "item"] = item
            p["_iter_round"] = idx + 1
            env = run_capability(atom, cap, p, codeagent_root=_codeagent_root())
            eok = bool(env and env.get("ok"))
            ok = ok and eok
            results.append({"round": idx + 1, "item": item, "ok": eok,
                            "data": (env or {}).get("data"), "error": (env or {}).get("error")})
            # 反馈环: 上轮结果作为下轮提示
            base["_feedback"] = (env or {}).get("data")
            time.sleep(0.1)
        return {"ok": ok, "data": {"results": results, "iterations": len(results),
                                   "ok": ok, "summary": "迭代控制: 共{0}轮, {1}".format(
                                       len(results), "全部成功" if ok else "存在失败")}}


agent = LabLoopAgent()

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(LabLoopAgent(), run_args={
        "capability": {"default": "loop.retry", "choices": ["loop.retry", "loop.iterate"]},
        "target_atom": {}, "target_cap": {}, "attempts": {}, "params": {},
        "items": {}, "item_param": {}
    }))
