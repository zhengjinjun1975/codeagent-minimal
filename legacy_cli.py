#!/usr/bin/env python3
"""legacy_cli.py — 存量脚本迁移兼容层（存量命令 → 原子调用）。

背景：原 review.py / code_agent.py 的存量命令（审查/测试/依赖/细化/派单…）现已
原子化。本 shim 提供**同一套命令接口**，但内部改经 orchestrator 调开源原子，
让存量脚本（smoke_test.py / cron_*）零改动继续跑，同时数据不出厂。

用法（与旧 review.py 命令兼容）：
    python legacy_cli.py <target> --review          # → code-review 原子
    python legacy_cli.py <target> --test            # → code-test 原子
    python legacy_cli.py <target> --dep             # → dep-impact 原子
    python legacy_cli.py <target> --refine JSON     # → code-evolve 原子
    python legacy_cli.py <target> --reuse           # → code-reuse 原子
    python legacy_cli.py <target> --project         # → code-project 原子
    python legacy_cli.py --dispatch-template ...    # → code-dispatch 原子

存量兼容：保留 `review.py`/`code_agent.py` 原入口不动（核心零改动），本文件是
新增的原子化替代入口。
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent_loader


def _load():
    """经 loader 加载全部原子（只读公开接口）。"""
    r = agent_loader.load_agents()
    if not r["ok"]:
        print("加载原子失败:", r["error"])
        sys.exit(1)
    return r["data"]["agents"]


def _call(agents, atom, capability, **kw):
    a = agents.get(atom)
    if a is None:
        return {"ok": False, "error": f"原子未加载: {atom}"}
    return a.run(_capability=capability, **kw)


def main():
    ap = argparse.ArgumentParser(description="CodeAgent 存量命令 → 原子调用（兼容层）")
    ap.add_argument("target", nargs="?", default=None, help="目标文件或目录")
    ap.add_argument("--review", action="store_true", help="静态审查(code-review)")
    ap.add_argument("--test", action="store_true", help="测试闭环(code-test)")
    ap.add_argument("--dep", action="store_true", help="依赖图影响分析(dep-impact)")
    ap.add_argument("--refine", metavar="OUTCOME_JSON", default=None, help="自进化细化(code-evolve)")
    ap.add_argument("--reuse", action="store_true", help="代码复用检索(code-reuse)")
    ap.add_argument("--project", action="store_true", help="项目加载/扫描(code-project)")
    ap.add_argument("--task", default=None, help="refine/派单等需 task 时用")
    ap.add_argument("--dispatch-template", action="store_true", help="派单5段模板(code-dispatch)")
    ap.add_argument("--budget", action="store_true", help="自适应预算(code-dispatch)")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    agents = _load()
    results = {}
    out = {}

    if args.review and args.target:
        r = _call(agents, "code-review", "codereview.review", path=args.target)
        results["review"] = r
        out["review"] = r.get("data", {}) if r.get("ok") else {"error": r.get("error")}
    if args.test and args.target:
        r = _call(agents, "code-test", "test.run", path=args.target, target_dir=os.path.dirname(args.target) or ".")
        results["test"] = r
        out["test"] = r.get("data", {}) if r.get("ok") else {"error": r.get("error")}
    if args.dep and args.target:
        r = _call(agents, "dep-impact", "impact.analyze", path=args.target)
        results["dep"] = r
        out["dep"] = r.get("data", {}) if r.get("ok") else {"error": r.get("error")}
    if args.refine:
        outcome = json.loads(args.refine)
        task = args.task or outcome.get("task", args.target or "task")
        r = _call(agents, "code-evolve", "evolve.refine", task=task, outcome=outcome)
        results["refine"] = r
        out["refine"] = r.get("data", {}) if r.get("ok") else {"error": r.get("error")}
    if args.reuse and args.target:
        r = _call(agents, "code-reuse", "reuse.local", path=args.target)
        results["reuse"] = r
        out["reuse"] = r.get("data", {}) if r.get("ok") else {"error": r.get("error")}
    if args.project and args.target:
        r = _call(agents, "code-project", "project.analyze", path=args.target)
        results["project"] = r
        out["project"] = r.get("data", {}) if r.get("ok") else {"error": r.get("error")}
    if args.dispatch_template:
        r = _call(agents, "code-dispatch", "dispatch.template")
        results["dispatch"] = r
        out["dispatch"] = r.get("data", {}) if r.get("ok") else {"error": r.get("error")}
    if args.budget:
        task = args.task or args.target or "task"
        r = _call(agents, "code-dispatch", "dispatch.budget", task=task, files_needed=1)
        results["dispatch"] = r
        out["dispatch"] = r.get("data", {}) if r.get("ok") else {"error": r.get("error")}

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        for k, v in out.items():
            # 修复 P2-1：原 `k in (r.get("ok")...)` 把字符串 in 布尔集合恒 False，✅ 永不显示
            mark = "✅" if results.get(k, {}).get("ok") else "❌"
            print(f"{mark} [{k}] {json.dumps(v, ensure_ascii=False, default=str)[:200]}")
    # 全失败才退出非0
    ok_any = any(r.get("ok") for r in results.values())
    if results and not ok_any:
        sys.exit(1)


if __name__ == "__main__":
    main()
