#!/usr/bin/env python3
"""deadcode 原子壳（open_source:true）。

复用（零改动核心）：deadcode.scan（复用 method_impact.build_graph + forward_reach）。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力：
  deadcode.scan  — 死代码扫描（入口 roots 正向可达 BFS，报不可达死符号）
  deadcode.stats — 死代码统计概况（total/live/dead/ratio/roots）
借鉴 code-graph-rag 死代码 = 入口点反向可达。零 LLM，数据不出厂。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import deadcode  # 复用核心：核心零改动


class DeadCodeAgent(AtomicAgent):
    name = "deadcode"
    version = "0.1.0"
    domain = "impact"
    description = "死代码检测：入口点正向可达 BFS，报不可达死符号（借鉴 code-graph-rag）"
    provides = ["deadcode.scan", "deadcode.stats"]
    depends_on = []
    inputs = ["path", "threshold"]
    outputs = ["total", "live", "dead_count", "ratio", "roots", "dead", "dead_by_file"]

    def _register_defaults(self):
        self.register("deadcode.scan", self._scan)
        self.register("deadcode.stats", self._stats)

    # ── 能力实现（复用 deadcode，一行不改核心）────────────────
    def _scan(self, path, threshold=0, **extra):
        """死代码扫描。path 为目录。返回完整报告（dead 列表 + dead_by_file）。"""
        return deadcode.scan([path] if isinstance(path, str) else path, threshold=threshold)

    def _stats(self, path, **extra):
        """死代码统计概况（便于 agent 快速判断规模）。"""
        r = deadcode.scan([path] if isinstance(path, str) else path)
        return {k: r[k] for k in ("total", "live", "dead_count", "ratio", "roots")}


# 模块级实例（loader 也可直接取用）
agent = DeadCodeAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="deadcode 原子独立自测入口")
    ap.add_argument("path", help="目标目录")
    ap.add_argument("--threshold", type=int, default=0)
    ap.add_argument("--capability", default="deadcode.scan", choices=["deadcode.scan", "deadcode.stats"])
    args = ap.parse_args()

    agent.load()
    print("══ deadcode 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, path=args.path, threshold=args.threshold)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
