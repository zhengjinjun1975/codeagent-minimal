#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：self_evolve.refine/self_prompt/tdd_loop/_sediment_skill
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：evolve。数据不出厂，可独立运行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import self_evolve as se

class CodeEvolveAgent(AtomicAgent):
    name = "code-evolve"
    version = "0.1.0"
    domain = "evolve"
    description = "自进化原子: self_evolve refine四步闭环+技能沉淀+tdd"
    provides = ["evolve.refine", "evolve.skill", "evolve.self_prompt", "evolve.tdd"]
    depends_on = ["memory.recall"]
    inputs = ["task", "outcome", "snapshot", "memdir", "auto_sediment", "top_k"]
    outputs = ["observation", "attribution", "refinement", "kept", "verdict", "skills", "prompt", "tdd"]

    def _register_defaults(self):
        self.register("evolve.refine", self._refine)
        self.register("evolve.skill", self._skill)
        self.register("evolve.self_prompt", self._self_prompt)
        self.register("evolve.tdd", self._tdd)

    def _refine(self, task, outcome, snapshot=None, memdir=None, auto_sediment=True):
        """四步闭环: 观察→归因→精炼→校验(快照回滚)+自动沉淀技能。"""
        memdir = memdir or se.DEFAULT_MEM
        r = se.refine(task, outcome, memdir=memdir, snapshot=snapshot,
                      auto_sediment=auto_sediment)
        return {"observation": r["observation"], "attribution": r["attribution"],
                "refinement": r["refinement"], "kept": r["kept"],
                "verdict": r["verdict"], "snapshot": r["snapshot"]}

    def _skill(self, task, action, bucket, memdir=None):
        """沉淀技能: 把可复用精炼动作写入 skills.json(去重)。"""
        memdir = memdir or se.DEFAULT_MEM
        before = len(se._load(memdir, se._SKILLS))
        se._sediment_skill(task, action, bucket, memdir)
        after = len(se._load(memdir, se._SKILLS))
        return {"skills": se._load(memdir, se._SKILLS), "added": after - before}

    def _self_prompt(self, task, memdir=None, top_k=3):
        """跨会话召回经验: 取回 lessons/refinements/skills 命中。"""
        memdir = memdir or se.DEFAULT_MEM
        return {"prompt": se.self_prompt(task, memdir=memdir, top_k=top_k),
                "memdir": memdir}

    def _tdd(self, task="", path=None, memdir=None, max_fix=3):
        """TDD 反馈闭环: 测试反馈→改进→再测试。"""
        memdir = memdir or se.DEFAULT_MEM
        if path:
            target = path
        else:
            target = os.path.join(REPO_ROOT, "sample_target.py")
        r = se.tdd_loop(target, memdir=memdir, max_fix=max_fix, task=task)
        return {"tdd": {"red": r["red"], "green": r["green"], "rounds": r["rounds"],
                        "fixes": r["fixes"], "memory_precipitated": r["memory_precipitated"]}}


agent = CodeEvolveAgent()

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="code-evolve 原子自测入口")
    ap.add_argument("--capability", default="evolve.refine",
                    choices=["evolve.refine", "evolve.skill", "evolve.self_prompt", "evolve.tdd"])
    ap.add_argument("--task", default="实现加法函数 add(a,b)")
    args = ap.parse_args()
    agent.load()
    print("══ code-evolve 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    if args.capability == "evolve.refine":
        r = agent.run(_capability="evolve.refine", task=args.task, outcome={"score": 75, "issues": ["缺边界值"]})
    elif args.capability == "evolve.skill":
        r = agent.run(_capability="evolve.skill", task=args.task, action="先补参数校验", bucket="P")
    elif args.capability == "evolve.self_prompt":
        r = agent.run(_capability="evolve.self_prompt", task=args.task)
    else:
        r = agent.run(_capability="evolve.tdd", task=args.task)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
