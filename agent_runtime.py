#!/usr/bin/env python3
"""agent_runtime.py — CodeAgent 统一运行时（开源侧，无第三方依赖）。

这是「吸收 OpenCode 能力后融合成新 CodeAgent 大整体」的统一运行时核心。

设计目标（融合，非零散原子）：
- 单一 AgentRuntime：一个运行时统一注册 + 统一调度全部原子（16 原子：12 核心 +
  新原子 MCP/SKILL/dep-scan/code-fuzz）。
- 原子协同 / 依赖 / 冲突 / 降级：经 loader 的 manifest 解析 + 拓扑序，本运行时做
  能力级路由（capability → atom）、可选依赖缺省降级、冲突提示。
- 原子间数据流：`run_capability` 支持把上一原子的 `{ok,data}` 输出注入下一原子的
  入参端口，实现链式数据流（协同）。
- 吸收 OpenCode 能力的协同接线：
    * MCP 供工具 → code-review 用（`mcp_tools` / `review_with_mcp`）
    * 多模型路由 → gen/evolve 用（`route_model`，默认 local_only 数据不出厂）
    * SKILL 标准 → reuse 用（`reuse_with_skill`：本地复用 + SKILL.md 资产召回）
- 大自进化闭环：`evolve_loop` = 观察→归因→精炼→校验 + 记忆复盘 + 技能沉淀 +
  SKILL/MCP 生态资产（越用越准，数据不出厂）。

统一入口见 `codeagent.py`（codeagent 命令），本模块是其运行时底座。

铁律：
- 只读原子公开接口（run / capabilities），不 import 原子内部核心。
- 数据不出厂：默认 local_only=True，MCP 默认白名单，云端 LLM 一律封锁。
- 失败降级：任何异常 → {ok:false, error, degraded:true}，绝不抛给上层。
"""

import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent_loader


# ── 信封 ─────────────────────────────────────
def _ok(data):
    return {"ok": True, "data": data}


def _fail(error, degraded=True, data=None):
    return {"ok": False, "data": data or {}, "error": error, "degraded": degraded}


class AgentRuntime:
    """CodeAgent 统一运行时：单一运行时调度全部原子，提供协同 / 依赖 / 冲突 / 降级。

    用法：
        rt = AgentRuntime()                 # 统一加载 16 原子（12 核心 + MCP/SKILL/dep-scan/code-fuzz）
        rt.run_capability("codereview.review", path=...)
        rt.run_chain([...], task=...)       # 原子协同数据流
        rt.evolve_loop(task, outcome)       # 大自进化闭环
        rt.review_with_mcp(path)            # MCP 供工具 → code-review
        rt.review_with_guard(path)          # dep-scan/fuzz → code-review 安全协同
    """

    def __init__(self, agents_dir=None, registry_path=None, local_only=True,
                 mcp_allow_tools=None):
        self.local_only = local_only
        self.mcp_allow_tools = mcp_allow_tools or []
        # 统一注册：扫描全部原子 + 冲突检测 + 拓扑排序
        r = agent_loader.load_registry(registry_path or agent_loader.REGISTRY_PATH)
        if not r["ok"]:
            self.agents, self.order, self.conflicts = {}, [], [r.get("error", "registry 加载失败")]
            self._cap_index = {}
            return
        manifests = r["data"]["agents"]
        self.conflicts = list(r["data"]["conflicts"])
        self.order = list(r["data"]["order"])
        # 能力 → 原子 索引
        self._cap_index = agent_loader._build_provided_index(manifests)
        # 实例化原子（经公开加载器）
        self.agents = {}
        self.degraded = []
        for name in self.order:
            m = manifests.get(name)
            if not m:
                continue
            adir = os.path.join(HERE, m.get("path", os.path.join("agents", name)))
            lr = agent_loader.load_agent(adir)
            if lr["ok"]:
                self.agents[name] = lr["data"]["agent"]
            else:
                self.degraded.append(name)

    # ── 自省 ─────────────────────────────────
    def atom_names(self):
        return list(self.agents.keys())

    def available_capabilities(self):
        """全部已加载原子的能力清单。"""
        out = {}
        for name, a in self.agents.items():
            for cap, meta in a.capabilities().items():
                if meta.get("callable"):
                    out[cap] = name
        return out

    def describe(self):
        return {
            "runtime": "codeagent-unified-runtime",
            "atoms": list(self.agents.keys()),
            "count": len(self.agents),
            "order": self.order,
            "degraded": self.degraded,
            "conflicts": self.conflicts,
            "local_only": self.local_only,
            "capabilities": self.available_capabilities(),
        }

    # ── 能力路由（统一调用）──────────────────
    def capability_atom(self, capability):
        """capability → 原子名；未知/降级返回 None。"""
        name = self._cap_index.get(capability)
        if name and name in self.agents:
            return name
        return None

    def run_capability(self, capability, **inputs):
        """统一调用某个能力（路由到对应原子）。未知能力 → 降级信封。"""
        name = self.capability_atom(capability)
        if name is None:
            return _fail(f"能力 '{capability}' 无可用原子（候选见 describe().capabilities）")
        a = self.agents[name]
        caps = a.capabilities()
        if capability not in caps or not caps[capability].get("callable"):
            return _fail(f"原子 {name} 能力 {capability} 不可用")
        # 数据不出厂默认：llm.generate 等敏感能力强制 local_only 透传
        if capability in ("llm.generate",):
            inputs.setdefault("local_only", self.local_only)
        return a.run(_capability=capability, **inputs)

    def call_atom(self, atom_name, capability=None, **inputs):
        """按原子名调用其默认/指定能力。"""
        a = self.agents.get(atom_name)
        if a is None:
            return _fail(f"原子未加载: {atom_name}（已加载 {list(self.agents)}）")
        return a.run(_capability=capability, **inputs)

    # ── 原子协同：数据流链（run_flow）────────
    def run_chain(self, chain, task=None, seed=None, on_step=None):
        """按能力序列执行原子协同，上一原子 {ok,data} 注入下一原子。

        chain: [{"step":.., "capability":.., "inputs":callable(data,seed)->dict} | "cap.x"]
        返回 {ok, data:{results:{step:信封}, flow, ok_steps, verdict}}。
        """
        results = {}
        flow = {}
        data = dict(seed or {})
        for item in chain:
            if isinstance(item, str):
                step = cap = item
                inputs_fn = lambda d, s, _cap=cap: {}
            else:
                step = item.get("step", item.get("capability"))
                cap = item["capability"]
                inputs_fn = item.get("inputs") or (lambda d, s: {})
            try:
                inputs = inputs_fn(data, seed or {})
                if task and cap == "plan.think":
                    inputs.setdefault("task", task)
                if task and cap == "plan.gen":
                    inputs.setdefault("task", task)
                # 组装链真实执行：test.run 环节若上游 gen 产出文件 dict，落盘到临时
                # 工作区并传 path（test 跑真实文件，红绿闭环非空壳）。
                if cap == "test.run" and "path" not in inputs:
                    gen_files = data.get("gen", {}).get("files") or {}
                    if gen_files:
                        tmp = tempfile.mkdtemp(prefix="codeagent_flow_")
                        paths = []
                        for fname, fcontent in gen_files.items():
                            fp = os.path.join(tmp, os.path.basename(str(fname)))
                            with open(fp, "w", encoding="utf-8") as f:
                                f.write(fcontent if isinstance(fcontent, str) else str(fcontent))
                            paths.append(fp)
                        inputs["target_dir"] = tmp
                        # 透传标记只留在本层（不传给原子 _run，避免
                        # CodeTestAgent._run() unexpected 'flow_files' 崩溃），
                        # 供事后把「真实落盘文件」写入 files_tested。
                        _flow_files = paths
                        if len(paths) == 1:
                            inputs["path"] = paths[0]
                        else:
                            # 多文件：逐文件跑 test.run 并聚合红绿（非空壳）
                            sub_results = [self.run_capability(cap, path=p, target_dir=tmp)
                                           for p in paths]
                            ok_sub = [s for s in sub_results if s.get("ok")]
                            ggs = [s["data"]["red_green"]["green"] for s in ok_sub
                                   if isinstance(s.get("data"), dict) and "red_green" in s["data"]]
                            all_green = bool(ggs) and len(ok_sub) == len(paths) and all(ggs)
                            if all_green:
                                res = _ok({"red_green": {"red": False, "green": True},
                                           "summary": f"test {len(ok_sub)}/{len(paths)} 文件通过",
                                           "files_tested": paths, "per_file": sub_results})
                            else:
                                res = _fail(f"test 有 {len(paths)-len(ok_sub)} 项失败",
                                            degraded=False,
                                            data={"red_green": {"red": True, "green": False},
                                                  "summary": f"test {len(ok_sub)}/{len(paths)} 文件通过",
                                                  "files_tested": paths, "per_file": sub_results})
                            results[step] = res
                            if res.get("ok") and isinstance(res.get("data"), dict):
                                data[step] = res["data"]
                                flow[step] = {"ok": True, "keys": list(res["data"].keys())}
                            else:
                                flow[step] = {"ok": False, "error": res.get("error")}
                            if on_step:
                                on_step(step, res, data)
                            continue
                res = self.run_capability(cap, **inputs)
                if cap == "test.run" and res.get("ok") and isinstance(res.get("data"), dict) \
                        and locals().get("_flow_files"):
                    res["data"]["files_tested"] = _flow_files
            except Exception as e:
                res = _fail(f"协同异常 {step}: {type(e).__name__}: {e}")
            results[step] = res
            if res.get("ok") and isinstance(res.get("data"), dict):
                data[step] = res["data"]
                flow[step] = {"ok": True, "keys": list(res["data"].keys())}
            else:
                flow[step] = {"ok": False, "error": res.get("error")}
            if on_step:
                on_step(step, res, data)
        ok_steps = [s for s, r in results.items() if r.get("ok")]
        return _ok({"task": task, "results": results, "flow": flow,
                    "ok_steps": ok_steps, "chain": [c.get("capability") if isinstance(c, dict) else c for c in chain],
                    "verdict": "全部通过" if len(ok_steps) == len(chain) else f"通过 {len(ok_steps)}/{len(chain)}"})

    # ── 吸收 OpenCode ①：MCP 供工具 → code-review ──
    def mcp_tools(self, server="demo"):
        """列出 MCP 生态工具（经 mcp-client 原子，默认本地白名单）。"""
        return self.run_capability("mcp.tools", server=server,
                                   local_only=self.local_only,
                                   allow_tools=self.mcp_allow_tools)

    def mcp_call(self, tool, arguments=None, server="demo"):
        """调用 MCP 工具（数据不出厂默认白名单）。"""
        return self.run_capability("mcp.call", tool=tool, arguments=arguments or {},
                                   server=server, local_only=self.local_only,
                                   allow_tools=self.mcp_allow_tools)

    def review_with_mcp(self, path, mode="code"):
        """MCP 供工具 → code-review：审查前拉 MCP 工具清单，把工具结果注入审查上下文。
        返回 {ok, data:{review, mcp_tools}}（协同演示 + 真实可用）。"""
        review = self.run_capability("codereview.review", path=path, mode=mode,
                                     use_llm=False, reuse_atoms=True)
        mt = self.mcp_tools()
        d = {"review": review}
        if mt.get("ok"):
            d["mcp_tools"] = mt["data"]
            # 协同：把 MCP 工具可用的证据并入审查信封（供审计可见）
            if review.get("ok") and isinstance(review.get("data"), dict):
                review["data"]["mcp_available_tools"] = mt["data"].get("count", 0)
        return _ok(d)

    # ── 重组合：dep-scan / code-fuzz / reg-guard 安全·质量协同 ──
    def dep_scan(self, target, osv_query=False, allow_remote=False):
        """统一调用 dep-scan 原子：SCA + taint 一站式。数据不出厂默认。"""
        return self.run_capability("depscan.scan", target=target,
                                   osv_query=osv_query, allow_remote=allow_remote)

    def fuzz(self, path, funcname=None, iterations=40, **kw):
        """统一调用 code-fuzz 原子：覆盖驱动用例生成 + 属性/模糊测试。"""
        if funcname:
            return self.run_capability("fuzz.run", path=path, funcname=funcname,
                                       iterations=iterations, **kw)
        return self.run_capability("fuzz.gen", path=path, **kw)

    def reg_guard(self, action="snapshot", **kw):
        """统一调用回归护栏：action=snapshot → 回归快照；action=affected → 增量测试选择。"""
        cap = {"snapshot": "test.snapshot", "affected": "test.affected"}.get(action, "test.snapshot")
        return self.run_capability(cap, **kw)

    def review_with_guard(self, path, mode="code"):
        """dep-scan/fuzz → code-review 安全协同：审查前跑 SCA+污点+模糊，
        把安全/健壮性证据并入审查信封（codereview.review 结构化 findings）。
        返回 {ok, data:{review, depscan, fuzz, merged}}。数据不出厂。"""
        review = self.run_capability("codereview.review", path=path, mode=mode,
                                     use_llm=False, reuse_atoms=True)
        ds = self.dep_scan(path)
        fu = self.fuzz(path) if os.path.isfile(str(path)) else None
        d = {"review": review, "depscan": ds, "fuzz": fu}
        merged = 0
        if review.get("ok") and isinstance(review.get("data"), dict):
            rd = review["data"]
            rd["depscan_evidence"] = ds.get("data", {}) if ds.get("ok") else {"error": ds.get("error")}
            if ds.get("ok") and isinstance(ds.get("data"), dict):
                rd["security_findings"] = ds["data"].get("taint", {}).get("findings", []) \
                    + [v for v in ds["data"].get("sca", {}).get("vulns", [])]
                merged += len(rd["security_findings"])
            if fu and fu.get("ok") and isinstance(fu.get("data"), dict):
                rd["fuzz_findings"] = fu["data"].get("cases", [])
                merged += len(rd["fuzz_findings"])
            rd["guard_merged"] = merged
        return _ok(d)

    # ── 吸收 OpenCode ②：多模型路由 → gen/evolve ──
    def route_model(self, purpose, messages, **kw):
        """多模型路由：生成走云端 GLM（默认 local_only 封锁），审查走本地 ornith。
        purpose: "gen" → llm.generate, "review" → llm.review, "evolve" → llm.review。
        数据不出厂默认。"""
        cap = {"gen": "llm.generate", "review": "llm.review", "evolve": "llm.review"}[purpose]
        return self.run_capability(cap, messages=messages, **kw)

    def list_models(self):
        """列出多模型注册表（本地/云端标注）。"""
        return self.run_capability("llm.list_models", local_only=self.local_only)

    # ── 吸收 OpenCode ③：SKILL 标准 → reuse ──
    def reuse_with_skill(self, content, path=None, top_k=3):
        """SKILL → reuse：本地代码复用检索 + 跨工具 SKILL.md 资产召回。
        返回 {ok, data:{reuse, skills, count}}（协同演示 + 真实可用）。"""
        reuse = self.run_capability("reuse.local", content=content, path=path, top_k=top_k)
        sk = self.run_capability("skill.list")
        d = {"reuse": reuse}
        if sk.get("ok"):
            d["skills"] = sk["data"]
        return _ok(d)

    def sediment_skill_to_md(self, task, action, name=None):
        """把自进化沉淀技能转 SKILL.md 标准资产（code-skill.export，跨工具复用）。"""
        return self.run_capability("skill.export", name=name or "auto-skill",
                                   description=f"自进化沉淀技能: {task}",
                                   content=f"# {task}\n\n1. {action}")

    # ── 大自进化闭环（完整）──────────────────
    def evolve_loop(self, task, outcome, snapshot=None, memdir=None,
                    auto_sediment=True, export_skill=True, review_path=None):
        """完整自进化闭环：观察→归因→精炼→校验 + 记忆复盘 + 技能沉淀 + SKILL/MCP 资产。

        步骤：
          1. refine     — 观察→归因→精炼→校验(快照回滚)
          2. remember   — 记忆复盘（审查发现沉淀 lessons.json）
          3. sediment   — 技能沉淀（skills.json，越用越准）
          4. self_prompt— 取回历史经验（下次更准）
          5. export_skill — 沉淀技能 → SKILL.md 标准资产（生态可复用）
          6. (可选) review_with_mcp — 审查接入 MCP 工具（资产协同）

        返回 {ok, data:{refine, memory, skill, prompt, exported, review_mcp, loop_closed}}。
        """
        r = self.run_capability("evolve.refine", task=task, outcome=outcome,
                                snapshot=snapshot, memdir=memdir,
                                auto_sediment=auto_sediment)
        if not r["ok"]:
            return _fail(f"自进化 refine 失败: {r.get('error')}", data={"refine": r})
        d = {"refine": r["data"]}
        # 记忆复盘：从 outcome.issues 沉淀 lessons
        issues = outcome.get("issues") or []
        mem = self.run_capability("memory.save", findings=issues,
                                  task=task, memdir=memdir)
        d["memory"] = mem.get("data", {}) if mem.get("ok") else {"error": mem.get("error")}
        # 技能沉淀：refine 的精炼动作
        action = r["data"].get("refinement", "")
        if action:
            sk = self.run_capability("skill.sediment", task=task, action=action,
                                     bucket=r["data"].get("bucket", "P"), memdir=memdir)
            d["skill"] = sk.get("data", {}) if sk.get("ok") else {"error": sk.get("error")}
        # 取回经验（越用越准）
        pr = self.run_capability("evolve.self_prompt", task=task, memdir=memdir, top_k=3)
        d["prompt"] = pr.get("data", {}) if pr.get("ok") else {"error": pr.get("error")}
        # 沉淀技能 → SKILL.md 标准资产
        if export_skill and action:
            ex = self.sediment_skill_to_md(task, action)
            d["exported"] = ex.get("data", {}) if ex.get("ok") else {"error": ex.get("error")}
        # 审查接入 MCP 工具（资产协同）
        if review_path:
            rv = self.review_with_mcp(review_path)
            d["review_mcp"] = rv.get("data", {}) if rv.get("ok") else {"error": rv.get("error")}
        # 闭环校验：refine kept + 记忆 + 技能沉淀
        d["loop_closed"] = bool(r["data"].get("kept")) and "memory" in d and "skill" in d
        d["verdict"] = r["data"].get("verdict")
        return _ok(d)


# ── CLI 自测 ─────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="CodeAgent 统一运行时（融合底座）")
    ap.add_argument("--describe", action="store_true", help="打印运行时全貌")
    ap.add_argument("--run", metavar="CAP", help="运行单个能力")
    ap.add_argument("--path", default=None)
    ap.add_argument("--local-only", action="store_true", default=True, help="数据不出厂（默认）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rt = AgentRuntime(local_only=args.local_only)
    if args.describe or not args.run:
        print(json.dumps(rt.describe(), ensure_ascii=False, indent=2))
    if args.run:
        kw = {}
        if args.path:
            kw["path"] = args.path
        r = rt.run_capability(args.run, **kw)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        if not r.get("ok") and not r.get("degraded"):
            sys.exit(1)
