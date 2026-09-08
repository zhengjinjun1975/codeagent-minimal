#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：纯汇总: 聚合组装链输出为验收报告(不复制编排算法)
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：deliver。数据不出厂，可独立运行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent


class CodeDeliverAgent(AtomicAgent):
    name = "code-deliver"
    version = "0.1.0"
    domain = "deliver"
    description = "交付/报告: 聚合组装链输出为验收报告"
    provides = ["deliver.report", "deliver.package"]
    depends_on = []
    inputs = ["chain", "outputs", "evidence"]
    outputs = ["report", "artifacts", "verdict"]

    def _register_defaults(self):
        self.register("deliver.report", self._report)
        self.register("deliver.package", self._package)

    def _report(self, chain, outputs, evidence=None):
        """汇总组装链各环输出为可读验收报告。纯本地聚合。"""
        NL = "\n"
        lines = ["# 组装链验收报告", ""]
        steps = chain if isinstance(chain, list) else ["think", "gen", "review", "test", "evolve"]
        ok_steps = []
        for step in steps:
            out = (outputs or {}).get(step, {})
            ok = bool(out.get("ok", False)) if isinstance(out, dict) else bool(out)
            ok_steps.append(step if ok else None)
            lines.append("## %s — %s" % (step, "✅" if ok else "❌"))
            if isinstance(out, dict) and "data" in out:
                d = out["data"]
                if isinstance(d, dict) and "summary" in d:
                    lines.append("  " + d["summary"])
            lines.append("")
        lines.append("## 证据回执")
        if evidence:
            lines.append("  " + evidence)
        ok_count = len([s for s in ok_steps if s])
        verdict = "全部通过" if ok_count == len(steps) else "通过 %d/%d, 存在失败环" % (ok_count, len(steps))
        lines.append("## 结论: " + verdict)
        return {"report": NL.join(lines), "verdict": verdict,
                "ok_steps": [s for s in ok_steps if s]}

    def _package(self, outputs, artifacts=None):
        """收集产物文件清单。"""
        files = []
        if isinstance(artifacts, list):
            files = artifacts
        else:
            for k, v in (outputs or {}).items():
                if isinstance(v, dict) and isinstance(v.get("data"), dict):
                    f = v["data"].get("files")
                    if isinstance(f, dict):
                        files += list(f.keys())
        return {"artifacts": sorted(set(files)), "count": len(set(files))}


agent = CodeDeliverAgent()

if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="code-deliver 原子自测入口")
    ap.add_argument("--capability", default="deliver.report",
                    choices=["deliver.report", "deliver.package"])
    args = ap.parse_args()
    agent.load()
    print("══ code-deliver 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    outputs = {"think": {"ok": True, "data": {"summary": "方案完成"}},
               "gen": {"ok": True, "data": {"files": {"main.py": "x"}}},
               "review": {"ok": True, "data": {"summary": "评分90"}},
               "test": {"ok": True, "data": {"summary": "全绿"}},
               "evolve": {"ok": True, "data": {"summary": "沉淀1条"}}}
    if args.capability == "deliver.report":
        r = agent.run(_capability="deliver.report", chain=["think", "gen", "review", "test", "evolve"], outputs=outputs)
    else:
        r = agent.run(_capability="deliver.package", outputs=outputs)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
