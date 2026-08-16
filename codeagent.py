#!/usr/bin/env python3
"""codeagent.py — CodeAgent 统一入口（codeagent 命令，开源侧）。

这是「吸收 OpenCode 能力后融合成新 CodeAgent 大整体」的统一入口。
用户统一使用 `codeagent` 命令（CLI + 可 import 的 API），不再逐个原子调用。

统一命令覆盖：
  review / test / evolve / reuse / impact / plan / memory / skill / mcp / llm /
  dispatch / project / taskstate / deliver   —— 映射到对应原子能力
  chain     —— 运行组装链（think→gen→review→test→evolve 大链，含 MCP/多模型/SKILL 协同）
  evolve-loop —— 大自进化闭环（观察→归因→精炼→校验 + 记忆复盘 + 技能沉淀 + SKILL 资产）
  registry / status / models —— 运行时自省

存量兼容：`--review/--test/--dep/--refine/--reuse/--project/--dispatch-template/--budget`
与 legacy_cli.py 的命令同义（此处内部走 AgentRuntime，行为一致）。

API 用法：
    from codeagent import CodeAgent
    ca = CodeAgent()
    ca.review("path/to/file.py")          # 或 ca.run("codereview.review", path=...)
    ca.chain("实现加法函数", code={...})  # 组装链
    ca.evolve_loop("任务", outcome={...}) # 大自进化
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from agent_runtime import AgentRuntime, _ok, _fail


class CodeAgent:
    """统一 API 门面：把全部原子能力聚合成一个可编程入口。"""

    def __init__(self, local_only=True, mcp_allow_tools=None):
        self.rt = AgentRuntime(local_only=local_only, mcp_allow_tools=mcp_allow_tools)

    # ── 统一运行 ─────────────────────────────
    def run(self, capability, **kwargs):
        return self.rt.run_capability(capability, **kwargs)

    def atoms(self):
        return self.rt.describe()

    # ── 各原子能力（统一别名）─────────────────
    def review(self, path=None, code=None, mode="code"):
        return self.rt.run_capability("codereview.review", path=path, code=code,
                                      mode=mode, use_llm=False, reuse_atoms=True)

    def test(self, path=None, code=None, target_dir=None, do_mutation=False):
        kw = {"target_dir": target_dir or "."}
        if path:
            kw["path"] = path
        if code:
            kw["code"] = code
        return self.rt.run_capability("test.run", do_mutation=do_mutation, **kw)

    def refine(self, task, outcome, snapshot=None):
        return self.rt.run_capability("evolve.refine", task=task, outcome=outcome,
                                      snapshot=snapshot)

    def reuse(self, content=None, path=None, top_k=3):
        return self.rt.run_capability("reuse.local", content=content, path=path, top_k=top_k)

    def impact(self, path, symbol=None):
        return self.rt.run_capability("impact.analyze", path=path, symbol=symbol,
                                      impact="dep_report", transitive=True)

    def plan(self, task, language="python"):
        return self.rt.run_capability("plan.think", task=task, language=language)

    def memory(self, findings, task=""):
        return self.rt.run_capability("memory.save", findings=findings, task=task)

    def skill(self, action="list", **kw):
        cap = {"list": "skill.list", "load": "skill.load", "export": "skill.export",
               "sediment": "skill.sediment"}.get(action, "skill.list")
        return self.rt.run_capability(cap, **kw)

    def mcp(self, action="tools", **kw):
        cap = {"tools": "mcp.tools", "call": "mcp.call", "list": "mcp.list"}.get(action, "mcp.tools")
        return self.rt.run_capability(cap, **kw)

    def llm(self, action="list_models", **kw):
        cap = {"list_models": "llm.list_models", "registry": "llm.registry",
               "review": "llm.review"}.get(action, "llm.list_models")
        return self.rt.run_capability(cap, **kw)

    def dispatch(self, task=None, files_needed=1):
        return self.rt.run_capability("dispatch.template") if not task else \
            self.rt.run_capability("dispatch.budget", task=task, files_needed=files_needed)

    def project(self, path):
        return self.rt.run_capability("project.load", path=path)

    def deliver(self, chain, outputs):
        return self.rt.run_capability("deliver.report", chain=chain, outputs=outputs)

    # ── 组装链（完整大链，含 MCP/多模型/SKILL 协同）──
    def chain(self, task, code=None, spec=None, language="python", chain=None):
        """运行 think→gen→review→test→evolve 大链（数据不出厂）。
        可选参数：code/spec 直接给 gen；chain 自定义能力序列。"""
        default = [
            {"step": "think", "capability": "plan.think",
             "inputs": lambda d, s: {"task": s.get("task"), "language": s.get("language", "python")}},
            {"step": "gen", "capability": "plan.gen",
             "inputs": lambda d, s: {"task": s.get("task"), "code": s.get("code"),
                                     "spec": s.get("spec"), "language": s.get("language", "python")}},
            {"step": "review", "capability": "codereview.review",
             "inputs": lambda d, s: {"code": d.get("gen", {}).get("files") or s.get("code"),
                                     "use_llm": False, "reuse_atoms": True}},
            {"step": "test", "capability": "test.run",
             "inputs": lambda d, s: {"target_dir": "."}},
            {"step": "evolve", "capability": "evolve.refine",
             "inputs": lambda d, s: {"task": s.get("task"),
                                     "outcome": {"score": d.get("review", {}).get("score", 0),
                                                 "issues": d.get("review", {}).get("issues", []),
                                                 "task": s.get("task")}}},
        ]
        return self.rt.run_chain(chain or default, task=task,
                                 seed={"task": task, "code": code, "spec": spec,
                                       "language": language})

    # ── 大自进化闭环 ─────────────────────────
    def evolve_loop(self, task, outcome, review_path=None, snapshot=None):
        return self.rt.evolve_loop(task, outcome, review_path=review_path, snapshot=snapshot)

    # ── 协同演示 ─────────────────────────────
    def review_with_mcp(self, path):
        return self.rt.review_with_mcp(path)

    def reuse_with_skill(self, content, top_k=3):
        return self.rt.reuse_with_skill(content, top_k=top_k)


# ── CLI ─────────────────────────────────────
def _add_json(p):
    p.add_argument("--json", action="store_true", help="输出 JSON")


def _cli():
    ap = argparse.ArgumentParser(description="CodeAgent 统一入口（融合后的新 CodeAgent 大整体）")
    sub = ap.add_subparsers(dest="cmd")

    # 统一子命令（每个子命令自带 --json，避免顶层 flag 与子命令/存量位置参数冲突）
    p = sub.add_parser("status", help="运行时全貌"); _add_json(p)
    p = sub.add_parser("models", help="多模型注册表"); _add_json(p)
    p = sub.add_parser("review", help="审查文件")
    p.add_argument("path"); p.add_argument("--mode", default="code"); _add_json(p)
    p = sub.add_parser("test", help="测试文件")
    p.add_argument("path"); p.add_argument("--no-mutation", action="store_true"); _add_json(p)
    p = sub.add_parser("evolve", help="自进化 refine")
    p.add_argument("--task", required=True); p.add_argument("--outcome", required=True); _add_json(p)
    p = sub.add_parser("evolve-loop", help="大自进化闭环")
    p.add_argument("--task", required=True); p.add_argument("--outcome", required=True)
    p.add_argument("--review-path", default=None); _add_json(p)
    p = sub.add_parser("chain", help="组装链(think→gen→review→test→evolve)")
    p.add_argument("--task", required=True)
    p.add_argument("--code", default=None); p.add_argument("--language", default="python"); _add_json(p)
    p = sub.add_parser("reuse", help="复用检索")
    p.add_argument("--content", default=None); p.add_argument("--path", default=None); _add_json(p)
    p = sub.add_parser("impact", help="依赖图影响分析")
    p.add_argument("path"); p.add_argument("--symbol", default=None); _add_json(p)
    p = sub.add_parser("mcp", help="MCP 工具")
    p.add_argument("--action", default="tools", choices=["tools", "call", "list"])
    p.add_argument("--tool", default=None); p.add_argument("--arg", default=None); _add_json(p)
    p = sub.add_parser("skill", help="SKILL 技能")
    p.add_argument("--action", default="list", choices=["list", "load", "export", "sediment"])
    p.add_argument("--name", default=None); p.add_argument("--task", default=None); _add_json(p)
    p = sub.add_parser("plan", help="方案设计")
    p.add_argument("--task", required=True); p.add_argument("--language", default="python"); _add_json(p)
    p = sub.add_parser("llm", help="多模型")
    p.add_argument("--action", default="list_models", choices=["list_models", "registry", "review"]); _add_json(p)
    p = sub.add_parser("memory", help="记忆沉淀")
    p.add_argument("--findings", required=True); p.add_argument("--task", default=""); _add_json(p)
    p = sub.add_parser("dispatch", help="派单")
    p.add_argument("--task", default=None); p.add_argument("--files", type=int, default=1); _add_json(p)
    p = sub.add_parser("project", help="项目分析")
    p.add_argument("path"); _add_json(p)
    p = sub.add_parser("deliver", help="交付报告")
    p.add_argument("--chain", default="think,gen,review,test,evolve")
    p.add_argument("--outputs", default="{}"); _add_json(p)

    # 存量兼容入口（无子命令时）：`codeagent <target> --review/--test/...`
    compat = argparse.ArgumentParser(add_help=False)
    compat.add_argument("target", nargs="?", default=None)
    compat.add_argument("--review", action="store_true")
    compat.add_argument("--test", action="store_true")
    compat.add_argument("--dep", action="store_true")
    compat.add_argument("--refine", metavar="OUTCOME_JSON", default=None)
    compat.add_argument("--reuse", action="store_true")
    compat.add_argument("--project", action="store_true")
    compat.add_argument("--dispatch-template", action="store_true")
    compat.add_argument("--budget", action="store_true")
    compat.add_argument("--task", default=None)
    compat.add_argument("--json", action="store_true")

    # ── 首参探测：是子命令则走统一子命令，否则走存量兼容（compat）──
    _SUBCMDS = {"status", "models", "review", "test", "evolve", "evolve-loop", "chain",
                "reuse", "impact", "mcp", "skill", "plan", "llm", "memory", "dispatch",
                "project", "deliver"}
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    is_sub = bool(argv and argv[0] in _SUBCMDS)

    ca = CodeAgent()
    out = {}

    if not is_sub:
        # 存量兼容入口：`codeagent <target> --review/--test/...`
        cargs = compat.parse_args()
        target = cargs.target
        if cargs.review and target:
            out["review"] = ca.review(path=target)
        if cargs.test and target:
            out["test"] = ca.test(path=target, target_dir=os.path.dirname(target) or ".")
        if cargs.dep and target:
            out["dep"] = ca.impact(target)
        if cargs.refine:
            outcome = json.loads(cargs.refine)
            out["refine"] = ca.refine(cargs.task or outcome.get("task", target or "task"), outcome)
        if cargs.reuse and target:
            out["reuse"] = ca.reuse(path=target)
        if cargs.project and target:
            out["project"] = ca.project(target)
        if cargs.dispatch_template:
            out["dispatch"] = ca.dispatch()
        if cargs.budget:
            out["dispatch"] = ca.dispatch(task=cargs.task or target or "task", files_needed=1)
        if not out:
            ap.print_help()
            return 0
        _emit(out, cargs.json)
        return 0 if any(r.get("ok") for r in out.values()) else 1

    # ── 统一子命令 ──
    args = ap.parse_args()
    if args.cmd == "status":
        _emit({"status": ca.atoms()}, args.json)
    elif args.cmd == "models":
        _emit({"models": ca.rt.list_models()}, args.json)
    elif args.cmd == "review":
        _emit({"review": ca.review(args.path, mode=args.mode)}, args.json)
    elif args.cmd == "test":
        _emit({"test": ca.test(path=args.path, target_dir=os.path.dirname(args.path) or ".",
                               do_mutation=not args.no_mutation)}, args.json)
    elif args.cmd == "evolve":
        _emit({"evolve": ca.refine(args.task, json.loads(args.outcome))}, args.json)
    elif args.cmd == "evolve-loop":
        _emit({"evolve_loop": ca.evolve_loop(args.task, json.loads(args.outcome),
                                             review_path=args.review_path)}, args.json)
    elif args.cmd == "chain":
        code = json.loads(args.code) if args.code else None
        _emit({"chain": ca.chain(args.task, code=code, language=args.language)}, args.json)
    elif args.cmd == "reuse":
        _emit({"reuse": ca.reuse(content=args.content, path=args.path)}, args.json)
    elif args.cmd == "impact":
        _emit({"impact": ca.impact(args.path, symbol=args.symbol)}, args.json)
    elif args.cmd == "mcp":
        kw = {}
        if args.tool:
            kw["tool"] = args.tool
        if args.arg:
            kw["arguments"] = {"text": args.arg}
        _emit({"mcp": ca.mcp(action=args.action, **kw)}, args.json)
    elif args.cmd == "skill":
        kw = {}
        if args.name:
            kw["name"] = args.name
        if args.task:
            kw["task"] = args.task
        _emit({"skill": ca.skill(action=args.action, **kw)}, args.json)
    elif args.cmd == "plan":
        _emit({"plan": ca.plan(args.task, language=args.language)}, args.json)
    elif args.cmd == "llm":
        _emit({"llm": ca.llm(action=args.action)}, args.json)
    elif args.cmd == "memory":
        _emit({"memory": ca.memory(json.loads(args.findings), task=args.task)}, args.json)
    elif args.cmd == "dispatch":
        _emit({"dispatch": ca.dispatch(task=args.task, files_needed=args.files)}, args.json)
    elif args.cmd == "project":
        _emit({"project": ca.project(args.path)}, args.json)
    elif args.cmd == "deliver":
        _emit({"deliver": ca.deliver(args.chain.split(","), json.loads(args.outputs))}, args.json)
    else:
        ap.print_help()
        return 0
    return 0


def _emit(out, as_json):
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        for k, v in out.items():
            if isinstance(v, dict) and "ok" in v:
                ok = v.get("ok")
                mark = "✅" if ok else "❌"
                summ = ""
                if ok and isinstance(v.get("data"), dict):
                    summ = str(v["data"].get("summary", v["data"].get("verdict", "")))
                print(f"{mark} [{k}] {summ} {('degraded:'+str(v.get('error')) if not ok else '')}")
            else:
                # 非信封 dict（如 status/models）直接展示摘要
                mark = "✅"
                info = ""
                if isinstance(v, dict):
                    info = str(v.get("count", v.get("verdict", v.get("summary", ""))))
                print(f"{mark} [{k}] {info}")


if __name__ == "__main__":
    sys.exit(_cli())
