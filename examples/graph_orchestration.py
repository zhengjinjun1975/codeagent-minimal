#!/usr/bin/env python3
"""graph_orchestration.py — 把 codeagent 命令作为「子代理节点」接入图编排。

适用：LangGraph / AutoGen GroupChat / CrewAI Process.sequential / 自研 DAG。
每个节点 = 一次 `codeagent <子命令>` 子进程调用（或进程内 run_capability），
节点间的 `{ok,data}` 作为下游节点入参，实现原子级子代理编排。

本文件自带一个最小「图编排」示例（零第三方依赖，纯标准库）：
    节点1 review  sample_target.py
        ↓ 把 score/issues 传给下游
    节点2 guard  （review + dep-scan + fuzz 协同）
        ↓
    节点3 deliver（生成交付报告）

跑通验证：
    python examples/graph_orchestration.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codeagent_toolkit import cli_tool, run_capability  # noqa: E402


# ── 节点定义：每个节点是一个「输入 dict → {ok,data} 信封」函数 ──────
def node_review(state: dict) -> dict:
    """子代理节点①：审查。返回 {ok,data} 信封。"""
    return json.loads(run_capability("codereview.review", path=state["path"], mode="code"))


def node_guard(state: dict) -> dict:
    """子代理节点②：安全·质量组装链（review+dep-scan+fuzz）。"""
    # 通过统一入口 codeagent 命令调用 guard
    out = cli_tool("guard", state["path"])
    return json.loads(out)["guard"]


def node_deliver(state: dict) -> dict:
    """子代理节点③：交付报告。把上游信封摘要写进报告。"""
    return json.loads(run_capability(
        "deliver.report",
        chain=["review", "guard"],
        outputs={"review_score": state.get("review", {}).get("data", {}).get("score"),
                 "guard_merged": state.get("guard", {}).get("data", {}).get("guard_merged")},
    ))


# ── 最小 DAG 编排器（拓扑序：review → guard → deliver）───────────
def orchestrate(initial: dict) -> dict:
    state = dict(initial)
    results = {}
    for step, fn in [("review", node_review), ("guard", node_guard),
                     ("deliver", node_deliver)]:
        res = fn(state)
        results[step] = res
        state[step] = res           # 下游节点可读取上游信封
    return {"results": results, "state": state}


def _main() -> int:
    out = orchestrate({"path": "sample_target.py"})
    for step, r in out["results"].items():
        ok = r.get("ok")
        d = r.get("data", {})
        summ = d.get("summary") if isinstance(d, dict) else ""
        print(f"[{'OK ' if ok else 'FAIL'}] {step}: {summ or list(d.keys()) if isinstance(d, dict) else d}")
    return 0 if all(r.get("ok") for r in out["results"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(_main())
