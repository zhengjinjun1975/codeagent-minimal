#!/usr/bin/env python3
"""dep-impact 原子壳（open_source:true）。

复用（零改动核心）：dep_audit.build_graph / callers / reverse_reach /
circular_imports / coupling_metrics / dep_report。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力：
  impact.analyze  — 影响分析（改 symbol/模块波及谁 + 图概况）
  impact.circular — 循环依赖检测
  impact.coupling — 模块耦合指标
零 LLM，完全离线，数据不出厂。
"""

import os
import sys

# 让入口能 import 仓库根模块
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import dep_audit  # 复用核心：核心零改动


class DepImpactAgent(AtomicAgent):
    name = "dep-impact"
    version = "0.1.0"
    domain = "impact"
    description = "依赖图影响分析：复用 dep_audit，零 LLM"
    provides = ["impact.analyze", "impact.circular", "impact.coupling"]
    depends_on = []
    inputs = ["path", "symbol", "impact", "transitive"]
    outputs = ["files", "entities", "edges", "coupling", "circular_imports", "impact"]

    def _register_defaults(self):
        self.register("impact.analyze", self._analyze)
        self.register("impact.circular", self._circular)
        self.register("impact.coupling", self._coupling)

    # ── 能力实现（复用 dep_audit，一行不改核心）────────────────
    def _analyze(self, path, symbol=None, impact=None, transitive=False):
        """影响分析。复用 code_agent.dep_impact 同构逻辑（即 dep_audit.dep_report）。
        path 可为文件或目录；symbol/impact 为函数/类/模块名。"""
        if isinstance(path, str):
            path = [path]
        sym = impact or symbol
        graph = dep_audit.build_graph(path)
        report = dep_audit.dep_report(path, impact=sym, transitive=transitive)
        # 附上图概况（build_graph 直接复用）
        report["entities"] = len(graph["entities"])
        report["edges"] = len(graph["edges"])
        return report

    def _circular(self, path):
        """循环依赖检测：返回模块 import 环列表。"""
        graph = dep_audit.build_graph([path] if isinstance(path, str) else path)
        return {"circular_imports": dep_audit.circular_imports(graph)}

    def _coupling(self, path):
        """模块耦合：fan_in/fan_out/耦合指数/影响面，按影响面降序。"""
        graph = dep_audit.build_graph([path] if isinstance(path, str) else path)
        rows = dep_audit.coupling_metrics(graph)
        return {"coupling": sorted(rows, key=lambda r: -r["impact_surface"])}


# 模块级实例（loader 也可直接取用）
agent = DepImpactAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="dep-impact 原子独立自测入口")
    ap.add_argument("path", help="目标文件或目录")
    ap.add_argument("--impact", default=None, help="影响分析符号/模块名")
    ap.add_argument("--transitive", action="store_true", help="影响分析含传递闭包")
    ap.add_argument("--capability", default="impact.analyze", choices=["impact.analyze", "impact.circular", "impact.coupling"])
    args = ap.parse_args()

    agent.load()
    print("══ dep-impact 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, path=args.path, impact=args.impact,
                  symbol=args.impact, transitive=args.transitive)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if not r["ok"]:
        sys.exit(1)
