#!/usr/bin/env python3
"""dep-scan 原子壳（open_source:true）。

复用（零改动核心）：dep_scan.scan_dependencies / taint_analyze / scan_all / _osv_query。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力（SCA + 污点 一站式，纯 stdlib，数据不出厂）：
  depscan.scan   — SCA + taint 一站式扫描（scan_all）
  depscan.sca    — 依赖漏洞 SCA 扫描（scan_dependencies，离线默认）
  depscan.taint  — Semgrep 级污点分析（source→sink 数据流）
  depscan.osv    — 可选 OSV 在线查询（需 allow_remote=True，数据不出厂默认关）

核心零改动，完全离线，数据不出厂。
"""

import os
import sys

# 让入口能 import 仓库根模块
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import dep_scan  # 复用核心：核心零改动


class DepScanAgent(AtomicAgent):
    name = "dep-scan"
    version = "0.1.0"
    domain = "depscan"
    description = "依赖漏洞SCA+污点原子：复用 dep_scan，纯 stdlib 数据不出厂"
    provides = ["depscan.scan", "depscan.sca", "depscan.taint", "depscan.osv"]
    depends_on = []
    inputs = ["target", "osv_query", "allow_remote"]
    outputs = ["deps", "vulns", "sca", "taint", "findings", "total_findings", "summary"]

    def _register_defaults(self):
        self.register("depscan.scan", self._scan_all)
        self.register("depscan.sca", self._sca)
        self.register("depscan.taint", self._taint)
        self.register("depscan.osv", self._osv)

    # ── 能力实现（复用 dep_scan，一行不改核心）────────────────
    def _scan_all(self, target, osv_query=False, allow_remote=False):
        """SCA + taint 一站式。target 为文件或目录。"""
        return dep_scan.scan_all(target, osv_query=osv_query, allow_remote=allow_remote)

    def _sca(self, target, osv_query=False, allow_remote=False):
        """依赖漏洞 SCA：默认完全离线（数据不出厂）。"""
        return dep_scan.scan_dependencies(target, osv_query=osv_query,
                                          allow_remote=allow_remote)

    def _taint(self, target):
        """Semgrep 级污点分析：source→sink 数据流。"""
        return dep_scan.taint_analyze(target)

    def _osv(self, target, allow_remote=False):
        """显式 OSV 在线查询（需 allow_remote=True，数据不出厂默认关）。"""
        return dep_scan.scan_dependencies(target, osv_query=True,
                                          allow_remote=allow_remote)


# 模块级实例（loader 也可直接取用）
agent = DepScanAgent()


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="dep-scan 原子独立自测入口")
    ap.add_argument("target", help="目标文件或目录")
    ap.add_argument("--capability", default="depscan.scan",
                    choices=["depscan.scan", "depscan.sca", "depscan.taint", "depscan.osv"])
    ap.add_argument("--osv", action="store_true", help="启用 OSV 在线查询")
    ap.add_argument("--remote", action="store_true", help="允许联网(数据不出厂默认关)")
    args = ap.parse_args()

    agent.load()
    print("══ dep-scan 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, target=args.target,
                  osv_query=args.osv, allow_remote=args.remote)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
