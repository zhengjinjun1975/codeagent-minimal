#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""⑤ 诚实预算门 —— implement() 接线真实自测（stub 生成/审查, 不触网, 跑真实预算循环）。

断言(对 code_agent_engine.CodeAgent.implement 的新预算逻辑):
  A. loop + 轮数耗尽(递增分, 永不达80/不触智能早停) → 结果带 budget_exhausted=True,
     budget_reason 含"轮数", iter_cap_reached=True；跑满 max_iter 轮。
  B. loop + 早收敛(score>=80) → 正常收敛, 不带 budget_exhausted(不误报)。
  C. 单次(loop=False) → 不带预算耗尽标记(一次性结果不是"耗尽")。
"""
import os
import sys
import json
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import code_agent_engine as cae

_PASS = {"checks": [], "pass": True}


def check(name, cond, detail=""):
    _PASS["checks"].append({"name": name, "ok": bool(cond), "detail": "" if cond else detail})
    _PASS["pass"] = _PASS["pass"] and bool(cond)


def _stub_code():
    """返回一段可被 _parse_code_blocks 解析成 {main.py:...} 的生成响应。"""
    return {"content": "```python\ndef add(a, b):\n    return a + b\n```"}


def _make_self(scores):
    """构造 implement 所需的 self 桩(think/review 确定性)。"""
    s = types.SimpleNamespace()
    s._score_iter = iter(scores)
    s.think = lambda task, language="python", domain="general": {
        "plan": "最小实现", "files_needed": ["main.py"], "constraint_chain": []}
    s.review = lambda files, language="python", mode="code", rules=None, focus=None: {
        "score": next(s._score_iter), "issues": [], "summary": "stub审查"}
    s.reuse_search = lambda *a, **k: []
    s.auto_reuse_search = lambda *a, **k: []
    return s


def main():
    # 不触网/不落 Obsidian：打桩模块级副作用
    cae.inject_context = lambda *a, **k: ""
    cae._log_to_obsidian = lambda *a, **k: None

    tmp = tempfile.mkdtemp(prefix="budget_wiring_")

    # A) loop 轮数耗尽 → 诚实 budget_exhausted
    resA = cae.CodeAgent.implement(_make_self([40, 50, 60]), "add fn",
                                   loop=True, max_iter=3, auto_reuse=False,
                                   output_dir=tmp)
    check("A loop exhausted flagged", resA.get("budget_exhausted") is True,
          f"keys={list(resA.keys())}")
    check("A reason honest", "轮数" in (resA.get("budget_reason") or ""),
          f"budget_reason={resA.get('budget_reason')}")
    check("A iter_cap_reached set", resA.get("iter_cap_reached") is True)
    check("A ran max_iter rounds", len(resA.get("versions", [])) == 3
          and resA.get("best_version") == 3,
          f"versions={len(resA.get('versions', []))} best_version={resA.get('best_version')}")

    # B) loop 早收敛(score>=80) → 不带耗尽标记
    resB = cae.CodeAgent.implement(_make_self([95]), "add fn",
                                   loop=True, max_iter=5, auto_reuse=False,
                                   output_dir=tmp)
    check("B converged no-exhaust", resB.get("budget_exhausted") in (None, False),
          f"budget_exhausted={resB.get('budget_exhausted')}")
    check("B best kept", resB.get("best_version") == 1)

    # C) 单次(loop=False) → 一次性结果非"耗尽"
    resC = cae.CodeAgent.implement(_make_self([60]), "add fn",
                                   loop=False, max_iter=1, auto_reuse=False,
                                   output_dir=tmp)
    check("C one-shot no-exhaust", resC.get("budget_exhausted") in (None, False),
          f"budget_exhausted={resC.get('budget_exhausted')}")

    print(json.dumps(_PASS, ensure_ascii=False, indent=2))
    return _PASS["pass"]


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
