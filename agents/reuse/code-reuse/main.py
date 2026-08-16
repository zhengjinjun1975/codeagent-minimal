#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：review.py._list_code_atoms/_reuse_suggestion + atoms/ 19个复用库
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：reuse。数据不出厂，可独立运行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import review as rv


class CodeReuseAgent(AtomicAgent):
    name = "code-reuse"
    version = "0.1.0"
    domain = "reuse"
    description = "代码复用检索: review.py 检索 atoms/ 19个复用库"
    provides = ["reuse.local", "reuse.atom", "reuse.remote"]
    depends_on = []
    inputs = ["content", "path", "top_k", "task"]
    outputs = ["suggestions", "atoms", "count"]

    def _register_defaults(self):
        self.register("reuse.local", self._local)
        self.register("reuse.atom", self._atom)
        self.register("reuse.remote", self._remote)

    def _local(self, content=None, path=None, top_k=3):
        """本地检索: 复用 _reuse_suggestion(Obsidian atoms → GitHub 远端降级)。"""
        src = content
        if not src and path:
            src = open(str(path), encoding="utf-8", errors="ignore").read()
        if not src:
            return self._envelope(False, degraded=True, error="缺 content 或 path 入参")
        sug = rv._reuse_suggestion(src, top_k=top_k)
        return {"suggestions": sug, "count": len(sug)}

    def _atom(self):
        """列出 atoms/ 19个复用库。"""
        atoms = rv._list_code_atoms()
        return {"atoms": atoms, "count": len(atoms)}

    def _remote(self, content, top_k=3):
        """GitHub 远端降级检索。"""
        return {"suggestions": rv._remote_reuse_suggestion(content, top_k=top_k)}


agent = CodeReuseAgent()

if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="code-reuse 原子自测入口")
    ap.add_argument("--capability", default="reuse.atom",
                    choices=["reuse.local", "reuse.atom", "reuse.remote"])
    ap.add_argument("path", nargs="?", default=None, help="要检索的文件")
    args = ap.parse_args()
    agent.load()
    print("══ code-reuse 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    if args.capability == "reuse.atom":
        r = agent.run(_capability="reuse.atom")
    elif args.capability == "reuse.remote":
        r = agent.run(_capability="reuse.remote", content="import numpy\n")
    else:
        if args.path:
            r = agent.run(_capability="reuse.local", path=args.path)
        else:
            r = agent.run(_capability="reuse.local", content="def networkx(n):\n    import networkx as nx\n    return nx.Graph()")
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
