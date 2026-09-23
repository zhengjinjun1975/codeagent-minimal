#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lab-harness 原子（执行编排控制器：harness 执行器·调度器·门控）。
加壳不改核心，纯 stdlib，数据不出私域。

能力:
  harness.control — 执行编排控制器(执行器/调度器): 按顺序执行 steps(每步可重试 retries),
                    并聚合上游边注入的 evidence → 编排汇总 + 裁决 verdict。
  harness.gate    — 门控/条件分支控制器: 依 condition 判定是否放行进入下一阶段(循环/重试门控)。
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


def _verdict_from_evidence(ev):
    """从上游 evidence 推导裁决（门控的真实输入）：任一 ok=False / verdict!=ALL_OK → PARTIAL_FAIL。

    没有任何可判据时返回 None（门控不臆断，视为无证据）。
    """
    state = {"seen": False, "bad": False}

    def walk(x, depth=0):
        if depth > 4:
            return
        if isinstance(x, dict):
            v = x.get("verdict")
            if isinstance(v, str):
                state["seen"] = True
                if v != "ALL_OK":
                    state["bad"] = True
            if "ok" in x:
                state["seen"] = True
                if x.get("ok") is False:
                    state["bad"] = True
            for sub in x.values():
                walk(sub, depth + 1)
        elif isinstance(x, (list, tuple)):
            for sub in x:
                walk(sub, depth + 1)

    walk(ev)
    if not state["seen"]:
        return None
    return "PARTIAL_FAIL" if state["bad"] else "ALL_OK"


class LabHarnessAgent(AtomicAgent):
    name = "lab-harness"
    version = "0.1.0"
    domain = "harness"
    description = "执行编排控制器(执行器/调度器/门控): 顺序编排原子步骤+重试+聚合 evidence+条件门控。加壳不改核心。"
    provides = ["harness.control", "harness.gate"]
    depends_on = []
    inputs = ["steps", "evidence", "mode", "retries", "condition"]
    outputs = ["results", "summary", "verdict", "ok"]

    def _register_defaults(self):
        self.register("harness.control", self._control)
        self.register("harness.gate", self._gate)

    def _control(self, steps=None, evidence=None, mode="seq", retries=1):
        steps = _coerce(steps)
        retries = max(0, int(_coerce(retries) or 0))
        ev = _coerce(evidence)
        results = {}
        all_ok = True
        lines = []
        if isinstance(steps, list) and steps:
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                atom = step.get("atom")
                cap = step.get("capability") or (step.get("provides") or [""])[0]
                if not atom:
                    continue
                p = dict(step.get("params") or {})
                env = None
                attempt = 0
                while attempt <= retries:
                    attempt += 1
                    env = run_capability(atom, cap, p, codeagent_root=_codeagent_root())
                    if env and env.get("ok"):
                        break
                    p = dict(p)
                    p["_retry_error"] = str((env or {}).get("error"))[:300]
                    time.sleep(0.15)
                key = f"{atom}:{cap}"
                results[key] = {"ok": bool(env and env.get("ok")), "attempts": attempt,
                                "error": (env or {}).get("error"),
                                "data": (env or {}).get("data")}
                if not (env and env.get("ok")):
                    all_ok = False
                lines.append("[{0}] {1}/{2}: {3}(尝试{4})".format(
                    idx + 1, atom, cap, "OK" if env and env.get("ok") else "FAIL", attempt))
        ev_count = 0
        if isinstance(ev, (list, dict)):
            ev_count = len(ev)
            results["_evidence"] = ev
        elif ev is not None:
            ev_count = 1
            results["_evidence"] = ev
        summary = ("执行编排控制器: " + (" / ".join(lines) if lines else "无 steps(仅聚合上游 evidence)"))
        verdict = "ALL_OK" if all_ok else "PARTIAL_FAIL"
        return {"ok": all_ok, "data": {"results": results, "summary": summary,
                                       "verdict": verdict, "evidence_count": ev_count}}

    def _gate(self, condition="all_ok", evidence=None, steps=None, retries=1):
        ctl = self._control(steps=steps, evidence=evidence, retries=retries)
        data = ctl.get("data", {}) if isinstance(ctl.get("data"), dict) else {}
        # 以「上游证据的裁决」为准：evidence 才是门控的真实输入（上游失败时门必须能关）
        ev_verdict = _verdict_from_evidence(_coerce(evidence))
        verdict = ev_verdict or data.get("verdict")
        cond = _coerce(condition)
        if isinstance(cond, (list, tuple)):
            cond = cond[0] if cond else "all_ok"
        gate_open = bool(verdict) and (
            (cond == "all_ok" and verdict == "ALL_OK") or
            (cond == "any_fail" and verdict == "PARTIAL_FAIL") or
            (cond == "always"))
        decision = "放行进入下一阶段(门控通过)" if gate_open else "拦截(门控未通过, 进入重试/迭代反馈)"
        return {"ok": True, "data": {"verdict": verdict, "evidence_verdict": ev_verdict,
                                     "gate_open": gate_open,
                                     "decision": decision,
                                     "summary": "门控控制器: " + decision}}


agent = LabHarnessAgent()

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(LabHarnessAgent(), run_args={
        "capability": {"default": "harness.control", "choices": ["harness.control", "harness.gate"]},
        "steps": {}, "evidence": {}, "mode": {}, "retries": {}, "condition": {}
    }))
