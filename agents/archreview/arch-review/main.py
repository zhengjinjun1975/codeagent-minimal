#!/usr/bin/env python3
"""arch-review 原子壳（open_source:true）。

复用（零改动核心）：arch_review.layered_analysis / boundary_analysis /
attack_surface_inventory / design_intent_compare。只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力（架构原子，纯 stdlib，数据不出厂）：
  archreview.layers    — 分层审查：分层 + 依赖方向（违规向上/横向）
  archreview.boundary  — 边界审查：信任边界/输入校验/网络/文件/命令边界
  archreview.surface   — 攻击面清单：外部入口盘点
  archreview.intent    — 设计意图比对：声明(docs/manifest) vs 实现(代码)
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import arch_review as ar


class ArchReviewAgent(AtomicAgent):
    name = "arch-review"
    version = "0.1.0"
    domain = "archreview"
    description = ("架构审查原子: 分层/依赖方向/边界/攻击面清单/设计意图比对(声明vs实现)。"
                   "纯 stdlib 数据不出厂")
    provides = ["archreview.layers", "archreview.boundary",
                "archreview.surface", "archreview.intent"]
    depends_on = []
    inputs = ["path"]
    outputs = ["layers", "by_layer", "violations", "boundaries", "inventory",
               "missing", "summary"]

    def _register_defaults(self):
        self.register("archreview.layers", self._layers)
        self.register("archreview.boundary", self._boundary)
        self.register("archreview.surface", self._surface)
        self.register("archreview.intent", self._intent)

    def _layers(self, path):
        if not path:
            return self._envelope(False, degraded=True, error="缺 path 入参")
        return ar.layered_analysis(path)

    def _boundary(self, path):
        if not path:
            return self._envelope(False, degraded=True, error="缺 path 入参")
        return ar.boundary_analysis(path)

    def _surface(self, path):
        if not path:
            return self._envelope(False, degraded=True, error="缺 path 入参")
        return ar.attack_surface_inventory(path)

    def _intent(self, path):
        if not path:
            return self._envelope(False, degraded=True, error="缺 path 入参")
        return ar.design_intent_compare(path)


agent = ArchReviewAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="arch-review 原子自测入口")
    ap.add_argument("target", help="目标文件或目录")
    ap.add_argument("--capability", default="archreview.layers",
                    choices=["archreview.layers", "archreview.boundary",
                             "archreview.surface", "archreview.intent"])
    args = ap.parse_args()
    agent.load()
    print("══ arch-review 原子自测 ══", agent.describe()["name"],
          "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, path=args.target)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
