#!/usr/bin/env python3
"""bug-deep 原子壳（open_source:true）。

复用（零改动核心）：bug_deep.threat_model / adversarial_review / generate_poc /
run_poc_sandbox / close_loop / load_rules。只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力（深挖原子，纯 stdlib，数据不出厂）：
  bugdeep.model   — 威胁建模：先建攻击面（入口点/危险 sink/信任边界）
  bugdeep.adv     — 对抗性审查（针对攻击面，先假设误报证伪 + 沉淀规则反哺）
  bugdeep.poc     — 自动化 PoC 验证（子进程沙箱跑证据）
  bugdeep.rule    — AI 规则反哺闭环（验证漏洞 → 沉淀规则）
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
from pathguard import safe_read_text
import bug_deep as bd


class BugDeepAgent(AtomicAgent):
    name = "bug-deep"
    version = "0.1.0"
    domain = "bugdeep"
    description = ("深挖 bug 原子: 威胁建模先建攻击面 + 对抗性审查 + 自动化PoC沙箱跑证据"
                   " + AI规则反哺闭环(验证漏洞沉淀规则)。纯 stdlib 数据不出厂")
    provides = ["bugdeep.model", "bugdeep.adv", "bugdeep.poc", "bugdeep.rule"]
    depends_on = []
    inputs = ["path", "code", "sink", "title", "rules_file"]
    outputs = ["attack_surface", "findings", "confirmed", "evidence", "rule", "summary"]

    def _register_defaults(self):
        self.register("bugdeep.model", self._model)
        self.register("bugdeep.adv", self._adv)
        self.register("bugdeep.poc", self._poc)
        self.register("bugdeep.rule", self._rule)

    def _content(self, path=None, code=None):
        if path:
            return safe_read_text(path)
        if isinstance(code, str):
            return code
        if isinstance(code, dict):
            name = list(code.keys())[0]
            c = code[name]
            return c.get("content", c) if isinstance(c, dict) else c
        return None

    def _model(self, path=None, code=None):
        content = self._content(path, code)
        if content is None:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")
        r = bd.threat_model(content, file=str(path or "code"))
        return r

    def _adv(self, path=None, code=None, rules_file=bd.DEFAULT_RULES_FILE):
        content = self._content(path, code)
        if content is None:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")
        return bd.adversarial_review(content, file=str(path or "code"),
                                     rules=bd.load_rules(rules_file))

    def _poc(self, sink=None, title=None, run=True, timeout=8):
        issue = {"sink": sink, "title": title or sink}
        poc = bd.generate_poc(issue)
        if run:
            ev = bd.run_poc_sandbox(poc.get("poc_code"), timeout=timeout)
            return {"poc": poc, "sandbox": ev,
                    "summary": f"PoC[{poc.get('kind')}] 沙箱验证: {ev.get('verdict')}"}
        return {"poc": poc}

    def _rule(self, sink=None, title=None, verify=True, rules_file=bd.DEFAULT_RULES_FILE):
        issue = {"sink": sink, "title": title or sink}
        if verify:
            return bd.close_loop(issue, rules_file=rules_file)
        return bd.sediment_rule(issue, verified=False, rules_file=rules_file)


agent = BugDeepAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="bug-deep 原子自测入口")
    ap.add_argument("target", help="目标文件或目录")
    ap.add_argument("--capability", default="bugdeep.model",
                    choices=["bugdeep.model", "bugdeep.adv", "bugdeep.poc",
                             "bugdeep.rule"])
    ap.add_argument("--sink", default=None, help="PoC/rule 的 sink(危险函数名)")
    ap.add_argument("--rules", default=bd.DEFAULT_RULES_FILE,
                    help="规则沉淀文件(配合 bugdeep.rule)")
    ap.add_argument("--no-verify", action="store_true",
                    help="rule 仅沉淀不跑 PoC 验证")
    args = ap.parse_args()
    agent.load()
    print("══ bug-deep 原子自测 ══", agent.describe()["name"],
          "status=" + agent.describe()["status"])
    if args.capability == "bugdeep.poc":
        # 从 target 威胁建模取第一个 sink 生成 PoC 并沙箱验证
        content = open(args.target, encoding="utf-8", errors="ignore").read()
        model = bd.threat_model(content, str(args.target))
        sink = args.sink or (model["sinks"][0]["name"] if model["sinks"] else "eval")
        r = agent.run(_capability="bugdeep.poc", sink=sink, title=sink, run=True)
    elif args.capability == "bugdeep.rule":
        content = open(args.target, encoding="utf-8", errors="ignore").read()
        model = bd.threat_model(content, str(args.target))
        sink = args.sink or (model["sinks"][0]["name"] if model["sinks"] else "eval")
        r = agent.run(_capability="bugdeep.rule", sink=sink, title=sink,
                      verify=not args.no_verify, rules_file=args.rules)
    else:
        r = agent.run(_capability=args.capability, path=args.target)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
