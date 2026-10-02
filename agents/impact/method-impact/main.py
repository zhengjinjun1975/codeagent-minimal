#!/usr/bin/env python3
"""method-impact 原子壳（open_source:true）。

复用（零改动核心）：method_impact.build_graph / reverse_reach / classify / forward_reach。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力：
  impact.method — 方法级影响分析（改符号波及的间接调用者 + 传播路径）
  impact.kind   — 边类型分类（CALLS/INSTANTIATES/INHERITS/REFERENCES）
借鉴 code-graph-rag 方法级 CALLS + 传递反向可达。零 LLM，数据不出厂。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import method_impact  # 复用核心：核心零改动


class MethodImpactAgent(AtomicAgent):
    name = "method-impact"
    version = "0.1.0"
    domain = "impact"
    description = "方法级影响分析：CALLS 反向可达 + 传播路径 + 边类型（借鉴 code-graph-rag）"
    provides = ["impact.method", "impact.kind"]
    depends_on = []
    inputs = ["path", "symbol", "transitive", "max_depth", "caller", "to"]
    outputs = ["symbol", "impact", "paths", "kind", "entities", "edges"]

    def _register_defaults(self):
        self.register("impact.method", self._method)
        self.register("impact.kind", self._kind)

    # ── 能力实现（复用 method_impact，一行不改核心）────────────────
    def _method(self, path, symbol=None, transitive=False, max_depth=None, **extra):
        """方法级影响分析。path 为目录；symbol 为 fqn（module.Class.method）。
        返回 impact（波及方）+ paths（最短传播路径）+ 图概况。"""
        g = method_impact.build_graph([path] if isinstance(path, str) else path)
        if symbol:
            impact, paths = method_impact.reverse_reach(g, symbol, transitive=transitive,
                                                        max_depth=max_depth)
            return {"symbol": symbol, "impact": impact, "paths": paths,
                    "entities": len(g["entities"]), "edges": len(g["edges"])}
        return {"entities": len(g["entities"]), "edges": len(g["edges"])}

    def _kind(self, path, caller, to, **extra):
        """边类型分类：给定 (caller,callee)，返回边类型列表。"""
        g = method_impact.build_graph([path] if isinstance(path, str) else path)
        return {"caller": caller, "callee": to, "kind": method_impact.classify(g, caller, to)}


# 模块级实例（loader 也可直接取用）
agent = MethodImpactAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="method-impact 原子独立自测入口")
    ap.add_argument("path", help="目标目录")
    ap.add_argument("--symbol", default=None, help="方法 fqn（如 module.Class.method）")
    ap.add_argument("--transitive", action="store_true")
    ap.add_argument("--capability", default="impact.method", choices=["impact.method", "impact.kind"])
    ap.add_argument("--caller", default=None)
    ap.add_argument("--to", default=None)
    args = ap.parse_args()

    agent.load()
    print("══ method-impact 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, path=args.path, symbol=args.symbol,
                  transitive=args.transitive, caller=args.caller, to=args.to)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
