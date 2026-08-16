#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：self_evolve.remember/_load/_save + self_prompt(召回)
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：memory。数据不出厂，可独立运行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import self_evolve as se

class CodeMemoryAgent(AtomicAgent):
    name = "code-memory"
    version = "0.1.0"
    domain = "memory"
    description = "记忆/经验原子: self_evolve 经验沉淀+跨会话召回"
    provides = ["memory.save", "memory.recall", "memory.sediment"]
    depends_on = []
    inputs = ["findings", "task", "memdir", "top_k"]
    outputs = ["added", "lessons", "prompt", "skills"]

    def _register_defaults(self):
        self.register("memory.save", self._save)
        self.register("memory.recall", self._recall)
        self.register("memory.sediment", self._sediment)

    def _save(self, findings, task="", memdir=None):
        """经验沉淀: 把审查发现/改进经验写进 lessons.json(跨会话复用)。"""
        memdir = memdir or se.DEFAULT_MEM
        added = se.remember(findings, task=task, memdir=memdir)
        return {"added": added, "lessons": se._load(memdir, se._LESSONS),
                "memdir": memdir}

    def _recall(self, task, memdir=None, top_k=3):
        """跨会话召回: self_prompt 取回命中经验文本。"""
        memdir = memdir or se.DEFAULT_MEM
        return {"prompt": se.self_prompt(task, memdir=memdir, top_k=top_k),
                "lessons": se._load(memdir, se._LESSONS),
                "refinements": se._load(memdir, se._REFINES),
                "skills": se._load(memdir, se._SKILLS)}

    def _sediment(self, task, action, bucket, memdir=None):
        """技能沉淀: 写入 skills.json(去重)。"""
        memdir = memdir or se.DEFAULT_MEM
        before = len(se._load(memdir, se._SKILLS))
        se._sediment_skill(task, action, bucket, memdir)
        return {"skills": se._load(memdir, se._SKILLS), "added": len(se._load(memdir, se._SKILLS)) - before}


agent = CodeMemoryAgent()

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="code-memory 原子自测入口")
    ap.add_argument("--capability", default="memory.save",
                    choices=["memory.save", "memory.recall", "memory.sediment"])
    ap.add_argument("--task", default="实现加法函数 add(a,b)")
    args = ap.parse_args()
    agent.load()
    print("══ code-memory 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    if args.capability == "memory.save":
        r = agent.run(_capability="memory.save", findings=[{"severity": "major", "title": "缺边界值", "suggestion": "补 None/空串/0 处理"}], task=args.task)
    elif args.capability == "memory.recall":
        r = agent.run(_capability="memory.recall", task=args.task)
    else:
        r = agent.run(_capability="memory.sediment", task=args.task, action="先补参数校验", bucket="P")
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
