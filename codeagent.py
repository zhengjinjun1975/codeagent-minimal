#!/usr/bin/env python3
"""codeagent.py — CodeAgent 统一入口（codeagent 命令，开源侧）。

这是「吸收 OpenCode 能力后融合成新 CodeAgent 大整体」的统一入口。
用户统一使用 `codeagent` 命令（CLI + 可 import 的 API），不再逐个原子调用。

统一命令覆盖：
  review / test / evolve / reuse / impact / plan / memory / skill / mcp / llm /
  dispatch / project / taskstate / deliver   —— 映射到对应原子能力
  dep-scan / fuzz / reg-guard                —— 重组合新增：SCA+污点扫描/属性模糊/回归护栏
  chain     —— 运行组装链（think→gen→review→test→evolve 大链，含 MCP/多模型/SKILL 协同）
  guard     —— 安全·质量组装链（review+dep-scan+fuzz+reg-guard 协同，数据不出厂）
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

from agent_runtime import AgentRuntime, _fail


class CodeAgent:
    """统一 API 门面：把全部原子能力聚合成一个可编程入口。"""

    def __init__(self, local_only=True, mcp_allow_tools=None):
        self.rt = AgentRuntime(local_only=local_only, mcp_allow_tools=mcp_allow_tools)

    # ── 统一运行 ─────────────────────────────
    def run(self, capability, **kwargs):
        # dispatch-sealed: *  ← 统一门面 run(capability) → 任意能力（对外 API 入口）
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
        # dispatch-sealed: skill.  ← 统一门面 skill(action) → skill.*
        return self.rt.run_capability(cap, **kw)

    def mcp(self, action="tools", **kw):
        cap = {"tools": "mcp.tools", "call": "mcp.call", "list": "mcp.list"}.get(action, "mcp.tools")
        # dispatch-sealed: mcp.  ← 统一门面 mcp(action) → mcp.*
        return self.rt.run_capability(cap, **kw)

    def llm(self, action="list_models", **kw):
        cap = {"list_models": "llm.list_models", "registry": "llm.registry",
               "review": "llm.review"}.get(action, "llm.list_models")
        # dispatch-sealed: llm.  ← 统一门面 llm(action) → llm.*
        return self.rt.run_capability(cap, **kw)

    def dispatch(self, task=None, files_needed=1):
        return self.rt.run_capability("dispatch.template") if not task else \
            self.rt.run_capability("dispatch.budget", task=task, files_needed=files_needed)

    def project(self, path):
        return self.rt.run_capability("project.load", path=path)

    def deliver(self, chain, outputs):
        return self.rt.run_capability("deliver.report", chain=chain, outputs=outputs)

    # ── 重组合新增：安全·质量命令（dep-scan / fuzz / reg-guard）──
    def dep_scan(self, target, osv_query=False, allow_remote=False):
        """SCA+污点扫描：目标文件或目录。数据不出厂默认。"""
        return self.rt.dep_scan(target, osv_query=osv_query, allow_remote=allow_remote)

    def fuzz(self, path, funcname=None, iterations=40):
        """属性模糊：funcname 指定单函数模糊，缺省覆盖驱动用例生成。"""
        return self.rt.fuzz(path, funcname=funcname, iterations=iterations)

    def reg_guard(self, action="snapshot", path=None, funcs=None):
        """回归护栏：action=snapshot 建/比回归快照；action=affected 增量测试选择。"""
        kw = {}
        if action == "affected":
            # test.affected 只接收 changed_files(文件路径列表)，不接收 path/funcs
            kw["changed_files"] = [path] if path else []
        else:
            if path:
                kw["path"] = path
            if funcs:
                kw["funcs"] = funcs
        return self.rt.reg_guard(action=action, **kw)

    def guard_chain(self, target, mode="code"):
        """安全·质量组装链：review + dep-scan + fuzz 协同（数据不出厂）。"""
        return self.rt.review_with_guard(target, mode=mode)

    # ── 组装链（完整大链，含 MCP/多模型/SKILL 协同）──
    def chain(self, task, code=None, spec=None, language="python", chain=None,
              auto=False, template=None):
        """运行组装链。

        组合**只有一个源**：`chain_templates.json`。这里不再留写死的默认链
        （旧版有一条 5 步硬编码链，和模板表里那套用不同原子，同一个意图两种结果）。
          chain=None, auto=False  → 走模板表里 default:true 的那条（默认「全流程/综合任务」）
          chain=None, auto=True   → 让运行时按任务关键词选链（选不中才退模型兜底）
          template="改缺陷/修回归" → 指定模板表里的某条
          chain=[...]             → 直接给能力序列（调用方自带）
        可选参数：code/spec 直接给 gen；language 传给 think/gen。
        """
        if chain is None and not auto:
            chain, err = self.rt.default_chain(template)
            if chain is None:
                return _fail("取不到组合：%s（模板表见 agents/dispatch/code-dispatch/"
                             "chain_templates.json）" % err)
        return self.rt.run_chain(chain, task=task,
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
def _gq_cap(graph, cap):
    """反查一条能力：在哪、谁在调、被哪些组合用到。"""
    info = graph["capability_index"].get(cap)
    if not info:
        near = [c for c in graph["capability_index"] if cap.split(".")[0] in c][:8]
        return {"ok": False, "error": "能力不存在：%s" % cap, "near": near}
    node = next((n for n in graph["nodes"] if n["id"] == info["atom"]), {})
    return {"ok": True, "capability": cap, "atom": info["atom"],
            "domain": node.get("domain"),
            "wired": bool(info["sites"] or info["chains"]),
            "call_sites": info["sites"], "used_by_chains": info["chains"],
            "dependents": sorted({e["from"] for e in graph["edges"]
                                  if e.get("capability") == cap})}


def _gq_chain(graph, q):
    """按名字前缀查组合。"""
    q = q.lower()
    hits = [c for c in graph["chains"]
            if q in str(c["name"]).lower() or q in c["chain_id"].lower()]
    if not hits:
        return {"ok": False, "matched": 0, "chains": [], "near":
                [{"id": c["chain_id"], "name": c["name"]} for c in graph["chains"]]}
    return {"ok": True, "matched": len(hits),
            "chains": [{"id": c["chain_id"], "name": c["name"], "file": c["file"],
                        "reach": c.get("reach"),
                        "steps": [{"order": s.get("order"), "capability": s.get("capability"),
                                   "atom": s.get("atom"), "resolved": s.get("resolved"),
                                   "inputs": s.get("inputs")} for s in c["steps"]]}
                       for c in hits]}


def _gq_summary(graph):
    m = graph["metrics"]
    return {"ok": True,
            "atoms": m["atoms"], "capabilities": m["capabilities"],
            "call_sites": {"total": m["call_sites"], "visible": m["call_sites_static"],
                           "evidence": m["evidence"], "blind_ratio": m["blind_ratio"]},
            "chains": {"total": m["chains"], "steps": m["chain_steps"],
                       "by_reach": m["chains_by_reach"],
                       "items": [{"id": c["chain_id"], "name": c["name"],
                                  "steps": len(c["steps"]), "dynamic": c["dynamic"],
                                  "default": bool(c.get("default")),
                                  "reach": c.get("reach"),
                                  "seq": [s.get("capability") for s in c["steps"]]}
                                 for c in graph["chains"]]},
            "questions": m["questions"],
            "findings_by_rule": m["rules_fired"]}


def _graph_query(args):
    """原子能力图谱查询：图从代码现算（registry + 各原子 main.py + 全仓调用点 + 组合模板），
    不读任何缓存文件 —— 缓存会让人查到一张昨天的图。

    三个入口：
      graph                      总览（家底 / 四问 / 组合可达性）
      graph --cap code.implement 反查：在哪、谁在调、被哪些组合用到
      graph --chain 改缺陷        看一条组合的全部步骤与签名核对
    """
    import atoms_graph_core as g

    graph = g.build_graph(os.path.dirname(os.path.abspath(__file__)))
    if args.cap:
        return _gq_cap(graph, args.cap)
    if args.chain:
        return _gq_chain(graph, args.chain)
    return _gq_summary(graph)


def _pg_cap(res):
    """反查一条能力：它在哪、谁在调、被哪些组合用到。"""
    print("能力 %s" % res["capability"])
    print("  所在原子：%s（domain=%s）" % (res["atom"], res.get("domain")))
    print("  判定：%s" % ("已接线" if res["wired"] else "未接线（不在任何调用点、也不在任何组合里）"))
    print("  调用点 %d 处：%s" % (len(res["call_sites"]), "、".join(res["call_sites"]) or "无"))
    if res["used_by_chains"]:
        print("  被组合用到：")
        for c in res["used_by_chains"]:
            print("    - %s 第%d步" % (c["name"], c["order"]))
    else:
        print("  被组合用到：无")
    print("  依赖它的原子：%s" % ("、".join(res["dependents"]) or "（无，直接由编排层调）"))


def _pg_chain(res):
    """看一条组合的每一步：能力 → 原子 → 静态入参 → 是否解析得到。"""
    if not res.get("chains"):
        print("❌ 没有匹配的组合")
        return
    for c in res["chains"]:
        if not c.get("steps"):
            print("  %s  %s" % (c["name"], c["id"]))
            continue
        print("组合 %s" % c["name"])
        print("  定义：%s ｜ ID：%s" % (c["file"], c["id"]))
        for st in c["steps"]:
            ins = st.get("inputs") or {}
            ins_s = " ".join("%s=%s" % (k, v) for k, v in sorted(ins.items())) or "—"
            print("  %d. %-20s %-16s %s  %s"
                  % (st["order"], st["capability"], st["atom"] or "未解析",
                     "✅" if st["resolved"] else "❌", ins_s))
        print("")
    if res.get("matched") == 0:
        print("（以上是全部组合，供按名字挑）")


def _pg_summary(res):
    """总览：家底 + 四问 + 组合序列。"""
    cs = res["call_sites"]
    qs = res.get("questions") or {}
    print("原子能力图谱（从代码现算，不读缓存）")
    print("  原子 %d ｜ 能力 %d ｜ 调用点 %d（看得见 %d / 看不见 %d，%.1f%%）"
          % (res["atoms"], res["capabilities"], cs["total"], cs["visible"],
             cs["total"] - cs["visible"], cs["blind_ratio"] * 100))
    print("  组合 %d 条 %d 步 ｜ 可达 %s"
          % (res["chains"]["total"], res["chains"]["steps"],
             "、".join("%s %d" % (k, v) for k, v in (res["chains"]["by_reach"] or {}).items()) or "—"))
    if qs:
        print("  四问：" + " ｜ ".join("%s %s %s" % (q, qs[q]["name"], qs[q]["level"].upper())
                                      for q in ("Q1", "Q2", "Q3", "Q4") if q in qs))
    if res["findings_by_rule"]:
        print("  规则命中：" + "、".join("%s %d" % (k, v)
                                        for k, v in sorted(res["findings_by_rule"].items())))
    print("\n组合：")
    for c in res["chains"]["items"]:
        print("  %-14s %d 步  %-14s %s%s" % (c["name"][:14], c["steps"], c.get("reach") or "—",
                                            " → ".join(c.get("seq") or []) or "?",
                                            "（动态，静态抽不出）" if c["dynamic"] else ""))
    print("\n用法：graph --cap <能力名> 反查影响面 ｜ graph --chain <名字> 看某条组合的每一步")


def _print_graph(res, args):
    """graph 子命令的人读输出。三种查询各走一个小函数。"""
    if not res.get("ok"):
        print("❌ " + str(res.get("error")))
        if res.get("near"):
            print("   相近的能力：" + "、".join(res["near"]))
        return
    if args.cap:
        _pg_cap(res)
    elif args.chain is not None or res.get("matched") is not None:
        _pg_chain(res)
    else:
        _pg_summary(res)


def _add_json(p):
    p.add_argument("--json", action="store_true", help="输出 JSON")


# 宪法例外: 子命令分发器，一分支一子命令；拆成字典分发会把 20 个子命令的参数解析散到别处，更难查
def _cli():
    ap = argparse.ArgumentParser(description="CodeAgent 统一入口（融合后的新 CodeAgent 大整体）")
    sub = ap.add_subparsers(dest="cmd")

    # 统一子命令（每个子命令自带 --json，避免顶层 flag 与子命令/存量位置参数冲突）
    p = sub.add_parser("status", help="运行时全貌"); _add_json(p)
    p = sub.add_parser("graph", help="原子能力图谱：能力/组合可追踪查询")
    p.add_argument("--cap", default=None, help="反查某条能力：所在原子、调用点、被哪些组合用到")
    p.add_argument("--chain", default=None, help="按名字前缀查组合的步骤（不给则列全部组合）")
    _add_json(p)
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
    p = sub.add_parser("chain", help="组装链（组合定义在模板表，默认走「全流程/综合任务」）")
    p.add_argument("--task", required=True)
    p.add_argument("--code", default=None); p.add_argument("--language", default="python")
    p.add_argument("--auto", action="store_true",
                   help="按任务关键词自动选链（选不中才退模型兜底）")
    p.add_argument("--template", default=None, help="指定模板表里的组合名，如 改缺陷/修回归")
    _add_json(p)
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
    # 重组合新增：dep-scan / fuzz / reg-guard / guard
    p = sub.add_parser("dep-scan", help="依赖漏洞SCA+污点扫描")
    p.add_argument("target"); p.add_argument("--osv", action="store_true")
    p.add_argument("--remote", action="store_true"); _add_json(p)
    p = sub.add_parser("fuzz", help="属性模糊测试")
    p.add_argument("path"); p.add_argument("--funcname", default=None)
    p.add_argument("--iterations", type=int, default=40); _add_json(p)
    p = sub.add_parser("reg-guard", help="回归护栏(快照/增量测试选择)")
    p.add_argument("--action", default="snapshot", choices=["snapshot", "affected"])
    p.add_argument("--path", default=None); p.add_argument("--func", default=None); _add_json(p)
    p = sub.add_parser("guard", help="安全·质量组装链(review+dep-scan+fuzz协同)")
    p.add_argument("target"); p.add_argument("--mode", default="code"); _add_json(p)

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
                "project", "deliver", "dep-scan", "fuzz", "reg-guard", "guard", "graph"}
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
    elif args.cmd == "graph":
        res = _graph_query(args)
        if args.json:
            _emit({"graph": res}, True)
        else:
            _print_graph(res, args)
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
        _emit({"chain": ca.chain(args.task, code=code, language=args.language,
                                 auto=args.auto, template=args.template)}, args.json)
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
    elif args.cmd == "dep-scan":
        _emit({"dep_scan": ca.dep_scan(args.target, osv_query=args.osv,
                                       allow_remote=args.remote)}, args.json)
    elif args.cmd == "fuzz":
        _emit({"fuzz": ca.fuzz(args.path, funcname=args.funcname,
                               iterations=args.iterations)}, args.json)
    elif args.cmd == "reg-guard":
        _emit({"reg_guard": ca.reg_guard(action=args.action, path=args.path,
                                         funcs=[args.func] if args.func else None)}, args.json)
    elif args.cmd == "guard":
        _emit({"guard": ca.guard_chain(args.target, mode=args.mode)}, args.json)
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
