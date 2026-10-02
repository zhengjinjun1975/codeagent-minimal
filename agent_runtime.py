#!/usr/bin/env python3
"""agent_runtime.py — CodeAgent 统一运行时（开源侧，无第三方依赖）。

这是「吸收 OpenCode 能力后融合成新 CodeAgent 大整体」的统一运行时核心。

设计目标（融合，非零散原子）：
- 单一 AgentRuntime：一个运行时统一注册 + 统一调度全部原子（39 原子，数量随 registry 动态）。
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
import atexit
import shutil
import tempfile
import threading
import time

# ── 组装链的临时工作区（test.run 跑真文件用的）────────────────────────
# 以前每个 test.run 步骤 mkdtemp 一个目录、从不清理：实测积到 293 个（每跑一次漏一个）。
# 现在留最近 _FLOW_WS_KEEP 个（结果里的 files_tested 路径还有效），更早的建新的时顺手删；
# 进程退出再把剩下的清掉。ponytail: 上限是"同进程内最多留 N 个"，够用；真要看历史产物就
# 用 CODEAGENT_FLOW_WS 指定固定目录（那时不清理，交给你管）。
_FLOW_WS_KEEP = 20
_FLOW_WS = []
_FLOW_WS_ENV = "CODEAGENT_FLOW_WS"


def _new_flow_ws():
    fixed = os.environ.get(_FLOW_WS_ENV)
    if fixed:
        os.makedirs(fixed, exist_ok=True)
        return fixed
    d = tempfile.mkdtemp(prefix="codeagent_flow_")
    _FLOW_WS.append(d)
    while len(_FLOW_WS) > _FLOW_WS_KEEP:
        shutil.rmtree(_FLOW_WS.pop(0), ignore_errors=True)
    return d


def _cleanup_flow_ws():
    while _FLOW_WS:
        shutil.rmtree(_FLOW_WS.pop(), ignore_errors=True)


atexit.register(_cleanup_flow_ws)

import cost_ledger                                                # noqa: E402  成本回归账本（P2）

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agent_loader


# ── ③ 子代理派活深度上限（仿 prime-agent/rlm 默认 2 层, 可覆盖）──────────
# 架构审计: 本仓库派活是"单层能力路由"(AgentRuntime 统一注册全部原子作为子代理,
# 原子不 import 运行时、不互相委托 → 无真实递归)。此深度门是**加壳不改核心**的
# 防御性护栏: 加在唯一派活漏斗 run_capability 上, 若未来某原子获得委托其他原子的
# 能力, 可防止失控递归; 默认不改变任何现存扁平派活行为(每个顶层调用深度恒为 1)。
# 可覆盖顺序: 参数 max_depth(本派发+后代继承) > env CODEAGENT_MAX_DEPTH > 默认 2。
_DEFAULT_MAX_SUBAGENT_DEPTH = 2
_dispatch_stack = threading.local()          # .depth:list[int] .cap:list[int]


def _resolve_subagent_depth(override, inherited, instance_default):
    if override is not None:
        return int(override)
    if inherited is not None:
        return int(inherited)                # 继承外层派发的覆盖
    if instance_default is not None:
        return int(instance_default)         # 运行时级默认(env CODEAGENT_MAX_DEPTH 注入)
    return _DEFAULT_MAX_SUBAGENT_DEPTH


# 开源版不接本机数据采集旁路（采样模块属本机环境，缺了不影响主流程）。


def _try_event_log(runtime, capability, inputs, result):
    """事件流旁路：每次能力调用记一条事件（与数据飞轮旁路同位、同款 fail-open）。

    ① 事件能力自身不再入流，避免递归；
    ② 载荷只记入参名与成败，不记大 payload——事件流自己不能成为上下文腐烂源。
    """
    try:
        if capability.startswith("event."):
            return
        name = runtime._cap_index.get("event.append")
        atom = runtime.agents.get(name) if name else None
        if atom is None:
            return
        ok = isinstance(result, dict) and bool(result.get("ok"))
        atom.call("event.append", session_key="runtime", kind=capability,
                  payload={"inputs": sorted(inputs.keys()), "ok": ok},
                  outcome="ok" if ok else "fail")
    except Exception:
        pass


# ── 信封 ─────────────────────────────────────
def _ok(data):
    return {"ok": True, "data": data}


def _fail(error, degraded=True, data=None):
    return {"ok": False, "data": data or {}, "error": error, "degraded": degraded}


# ── 链组装小工具（P1-4 编排按需编译用，确定性、无依赖）──
def _chain_of(caps):
    """能力名序列 → run_chain 吃的链步骤。step 取点号后半段，重名追加序号保唯一。"""
    out, seen = [], {}
    for c in caps:
        s = str(c).split(".")[-1]
        seen[s] = seen.get(s, 0) + 1
        out.append({"step": s if seen[s] == 1 else "%s-%d" % (s, seen[s]), "capability": c})
    return out


def _first_json_object(text):
    """从模型回复里抠第一个 {...}（容忍前后散文/代码块围栏）。抠不到返回 ""。"""
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if 0 <= i < j else ""


def _resolve_step_inputs(spec, data, task):
    """把链步骤里的**静态入参规格**解析成真入参（模板表用，P1-4）。

    支持两个占位符：`$task` → 本次任务文本；`$upstream_files` → 上游第一个「files 为非空 dict」
    的产出（文件名字典）。其它字面量原样传。上游没有产物时 `$upstream_files` 解析成 None
    （该步会如实降级，不编一个假产物出来）。
    """
    upstream = None
    for _v in (data or {}).values():
        if isinstance(_v, dict) and isinstance(_v.get("files"), dict) and _v["files"]:
            upstream = _v["files"]
            break
    out = {}
    for k, v in (spec or {}).items():
        if v == "$task":
            out[k] = task or ""
        elif v == "$upstream_files":
            out[k] = upstream
        else:
            out[k] = v
    return out


# ── ① 运行预算熔断（2026-09-21，P0-2）─────────────────────────────────
# 为什么需要：`budget_trim`（context-compact）管的是**上下文窗口**，不是"跑了多少步 /
# 同一动作重复多少次 / 调了多少次模型"。社区真实案例：4-agent 死循环连跑 11 天烧掉
# $47,000；企业 96% 的 LLM 支出超预算。宁可如实停机并留下状态，不可静默烧钱。
# 覆盖约定与上面的派活深度门一致：参数 budget > env CODEAGENT_RUN_BUDGET(JSON) > 默认。
_DEFAULT_RUN_BUDGET = {"max_steps": 32, "max_repeats": 3, "max_model_calls": 24}


class RunBudget:
    """一次组装链的运行预算（确定性计数，纯标准库）。

    - max_steps      链上最多执行多少步（别名 max_calls，与闭源 CodeMode 的 budget 口径一致）
    - max_repeats    同一 (能力 + 入参签名) 最多重复几次
    - max_model_calls 模型调用上限（按 capability 含 'llm'/'gen'/'think' 计）

    step() 每次返回 (允许? , 熔断原因)。计数只增不减，snapshot() 供审计落盘。
    """

    def __init__(self, max_steps=None, max_repeats=None, max_model_calls=None, enabled=True):
        cfg = dict(_DEFAULT_RUN_BUDGET)
        raw = os.environ.get("CODEAGENT_RUN_BUDGET")
        if raw:
            try:
                cfg.update({k: v for k, v in json.loads(raw).items() if k in cfg})
            except Exception:
                pass                                  # 配错了就退回默认，不因此炸链
        for k, v in (("max_steps", max_steps), ("max_repeats", max_repeats),
                     ("max_model_calls", max_model_calls)):
            if v is not None:
                cfg[k] = int(v)
        self.limits = cfg
        self.enabled = bool(enabled)
        self.steps = 0
        self.model_calls = 0
        self.repeats = {}                             # 签名 -> 次数
        self.stopped_by = None

    @staticmethod
    def signature(capability, inputs):
        """(能力 + 入参) 的稳定签名：只取可 JSON 化的键值，排序保证同参同签名。"""
        try:
            body = json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            body = str(inputs)
        return "%s|%s" % (capability, body[:400])

    def step(self, capability, inputs):
        """判一步能否执行。计数器只统计**被放行的**步/模型调用与重复次数，
        上限值因此是"实际做到多少"的硬上限（避免 max_repeat_seen 超过 max_repeats 这种假 bug）。"""
        if not self.enabled:
            self.steps += 1
            return True, None
        if self.steps + 1 > self.limits["max_steps"]:
            self.stopped_by = "max_steps"
            return False, "步数超上限 %d（max_steps）" % self.limits["max_steps"]
        sig = self.signature(capability, inputs)
        n = self.repeats.get(sig, 0) + 1
        if n > self.limits["max_repeats"]:
            self.stopped_by = "max_repeats"
            return False, ("同一动作重复超上限 %d 次（max_repeats）：%s"
                           % (self.limits["max_repeats"], capability))
        is_model = any(k in capability for k in ("llm", "gen", "think"))
        if is_model and self.model_calls + 1 > self.limits["max_model_calls"]:
            self.stopped_by = "max_model_calls"
            return False, ("模型调用超上限 %d 次（max_model_calls）"
                           % self.limits["max_model_calls"])
        self.steps += 1
        self.repeats[sig] = n
        if is_model:
            self.model_calls += 1
        return True, None

    def snapshot(self):
        return {"enabled": self.enabled, "steps": self.steps,
                "model_calls": self.model_calls, "limits": dict(self.limits),
                "stopped_by": self.stopped_by,
                "max_repeat_seen": max(self.repeats.values()) if self.repeats else 0}


def _resolve_run_budget(budget, instance_default=None):
    """budget 参数：None→用实例默认；dict→按其中的键覆盖；False→显式关闭熔断。"""
    if budget is False:
        return RunBudget(enabled=False)
    if isinstance(budget, dict):
        return RunBudget(max_steps=budget.get("max_steps", budget.get("max_calls")),
                         max_repeats=budget.get("max_repeats"),
                         max_model_calls=budget.get("max_model_calls"),
                         enabled=budget.get("enabled", True))
    if instance_default is not None:
        return instance_default
    return RunBudget()


class AgentRuntime:
    """CodeAgent 统一运行时：单一运行时调度全部原子，提供协同 / 依赖 / 冲突 / 降级。

    用法：
        rt = AgentRuntime()                 # 统一加载 39 原子
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
        # ③ 运行时级派活深度默认：env CODEAGENT_MAX_DEPTH 覆盖(否则默认 2)。
        _env_d = os.environ.get("CODEAGENT_MAX_DEPTH")
        self.max_subagent_depth = (int(_env_d) if _env_d and str(_env_d).strip().isdigit()
                                   else _DEFAULT_MAX_SUBAGENT_DEPTH)
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
        self.degraded_reasons = {}                     # 降级必须留原因：只说"degraded"没法查（吃过两次亏）
        for name in self.order:
            m = manifests.get(name)
            if not m:
                self.degraded.append(name)
                self.degraded_reasons[name] = "registry 里没有这个原子的 manifest 条目"
                continue
            adir = os.path.join(HERE, m.get("path", os.path.join("agents", name)))
            lr = agent_loader.load_agent(adir)
            if lr["ok"]:
                self.agents[name] = lr["data"]["agent"]
            else:
                self.degraded.append(name)
                self.degraded_reasons[name] = lr.get("error") or "load_agent 返回 !ok 但没给原因"

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
        caps = self.available_capabilities()
        return {
            "runtime": "codeagent-unified-runtime",
            "atoms": list(self.agents.keys()),
            "count": len(self.agents),
            "order": self.order,
            "degraded": self.degraded,
            "degraded_reasons": getattr(self, "degraded_reasons", {}),
            "conflicts": self.conflicts,
            "local_only": self.local_only,
            "capabilities": caps,
            # harness 能力段：一眼看出中间件是否真的挂上了（缺哪个一目了然）
            "harness": {k: (k in caps) for k in (
                "guard.exit_intercept", "guard.selfcheck", "guard.loop_guard",
                "dispatch.verify_artifact", "dispatch.assert_exit",
                "context.offload", "context.disclose",
                "model.cascade", "event.append")},
        }

    # ── 能力路由（统一调用）──────────────────
    def capability_atom(self, capability):
        """capability → 原子名；未知/降级返回 None。"""
        name = self._cap_index.get(capability)
        if name and name in self.agents:
            return name
        return None

    def run_capability(self, capability, **inputs):
        """统一调用某个能力（路由到对应原子）。未知能力 → 降级信封。
        数据飞轮旁路：真实执行结果统一采样落盘（fail-open，不影响主流程）。

        ③ 派活深度门（加壳, 不改核心路由 _run_capability_impl）：本仓库原子为扁平子代理,
        正常每个顶层调用深度=1；仅当某原子未来获得"委托其他原子"能力形成递归派活时才可能
        叠加深度。深度 > max_depth 时如实返回降级信封(不再下钻), 不假装完成。可覆盖：
          inputs['max_depth'](仅本次+后代) > env CODEAGENT_MAX_DEPTH > 默认 2。
        """
        override = inputs.pop('max_depth', None)            # 参数覆盖(继承给后代)
        st = _dispatch_stack
        depth = (st.depth[-1] if getattr(st, 'depth', None) else 0) + 1
        inherited = st.cap[-1] if getattr(st, 'cap', None) else None
        cap_eff = _resolve_subagent_depth(override, inherited, self.max_subagent_depth)
        if depth > cap_eff:
            return _fail(
                f"子代理派活深度超过上限 {cap_eff} 层(默认2; 可 env CODEAGENT_MAX_DEPTH "
                f"或参数 max_depth 覆盖): 第 {depth} 层委托已拦截, 能力={capability}",
                degraded=True,
                data={"capability": capability, "dispatch_depth": depth,
                      "max_subagent_depth": cap_eff, "budget": "finite"})
        if not getattr(st, 'depth', None):
            st.depth, st.cap = [], []
        st.depth.append(depth)
        st.cap.append(cap_eff)
        # 开源版不做本机分诊门（要外挂本机判决模型，属外部依赖）；能力调用直接执行。
        try:
            res = self._run_capability_impl(capability, **inputs)
        finally:
            st.depth.pop()
            st.cap.pop()
        _try_event_log(self, capability, inputs, res)
        return res

    def subagent_registry(self):
        """③ 子代理注册表（可 list）：列出运行时已注册的全部原子(子代理)及其能力挂载状态。
        原子是库内无状态单例(非 spawn 出的持久会话), 故"回收(reclaim)"无对应物——不需要、
        也无从回收; 本 API 提供透明可审计的注册表视图(list/自省)。"""
        out = []
        for name in self.order:
            a = self.agents.get(name)
            if a is None:
                out.append({"atom": name, "loaded": False, "domain": "", "version": "",
                            "callable_caps": []})
                continue
            caps = []
            try:
                for cap, meta in (a.capabilities() or {}).items():
                    if meta.get("callable"):
                        caps.append(cap)
            except Exception:
                caps = []
            out.append({"atom": name, "loaded": True,
                        "domain": getattr(a, "domain", ""),
                        "version": getattr(a, "version", ""),
                        "provides": list(getattr(a, "provides", []) or []),
                        "callable_caps": sorted(caps)})
        return {"ok": True, "data": {"count": len(out), "degraded": list(self.degraded),
                                     "max_subagent_depth": self.max_subagent_depth,
                                     "subagents": out}}


    def _run_capability_impl(self, capability, **inputs):
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

    # ── 编排按需编译（P1-4）：任务 → 链 ────────────────────
    def select_chain(self, task, rb=None, model_fn=None):
        """任务→链：**规则层优先**（0 次模型调用），未命中才退模型兜底。

        规则层走 dispatch.chain_select 原子（模板表在原子目录的 chain_templates.json）。
        兜底模型调用**先本地**（llm.review，数据不出厂）、再按 self.local_only 决定是否上云端；
        这一次调用**计入运行预算**（rb.step），否则熔断看不见它。
        返回 {ok, data:{chain, source(rule|model), template_name, hits, model_calls, reason}}。
        """
        caps = sorted(self.available_capabilities().keys())
        sel = self.run_capability("dispatch.chain_select", task=task or "", available=caps)
        d = sel.get("data") if isinstance(sel.get("data"), dict) else {}
        if sel.get("ok") and d.get("matched"):
            return _ok({"chain": d.get("chain") or [], "source": "rule",
                        "template_name": d.get("template_name"), "hits": d.get("hits") or [],
                        "model_calls": 0, "reason": d.get("reason")})
        # ── 模型兜底（唯一一次模型调用，计入预算）──
        if rb is not None:
            allowed, why = rb.step("llm.generate", {"task": task or ""})
            if not allowed:
                return _fail("模型兜底选链被预算拦下：" + why, degraded=False,
                             data={"chain": [], "source": "none", "model_calls": 0, "reason": why})
        fn = model_fn or self._model_chain
        try:
            picked = fn(task or "")
        except Exception as e:
            return _fail("模型兜底选链失败：%s: %s" % (type(e).__name__, e), degraded=False,
                         data={"chain": [], "source": "model", "model_calls": 1,
                               "reason": "%s: %s" % (type(e).__name__, e)})
        avail = set(caps)
        chain = _chain_of([c for c in (picked or []) if c in avail])
        if not chain:
            return _fail("模型兜底选链没给出可用能力（候选 %r）" % (list(picked or []),),
                         degraded=False,
                         data={"chain": [], "source": "model", "model_calls": 1,
                               "reason": "模型给的能力名都不在可用集里"})
        return _ok({"chain": chain, "source": "model", "model_calls": 1,
                    "reason": "规则未命中，模型兜底选出 %d 步" % len(chain)})

    def _model_chain(self, task):
        """兜底选链的模型调用：先本地 llm.review（不出厂），失败且允许出网才上 llm.generate。
        拿不到回复 → 抛异常（调用方如实报失败，不编一条链糊过去）。"""
        prompt = ("你是执行链编排器。可用能力（只能用这些）：%s\n任务：%s\n"
                  "只回一个 JSON 对象，形如 {\"chain\": [\"能力名\", \"能力名\"]}，按执行顺序；"
                  "不要解释、不要代码块。" % (", ".join(sorted(self.available_capabilities())), task))
        msgs = [{"role": "user", "content": prompt}]
        r = self.run_capability("llm.review", messages=msgs, temp=0.1)
        if not r.get("ok") and not self.local_only:
            r = self.run_capability("llm.generate", messages=msgs, temp=0.1, local_only=False)
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "选链模型不可用")
        blob = _first_json_object((r.get("data") or {}).get("content") or "")
        if not blob:
            raise ValueError("模型回复里没有 JSON 对象")
        picked = json.loads(blob).get("chain")
        if not isinstance(picked, list):
            raise ValueError("JSON 里 chain 不是数组")
        return [c for c in picked if isinstance(c, str)]

    # ── 原子协同：数据流链（run_flow）────────
    def run_chain(self, chain=None, task=None, seed=None, on_step=None, budget=None,
                  reuse=None, model_fn=None):
        """按能力序列执行原子协同，上一原子 {ok,data} 注入下一原子。

        chain: [{"step":.., "capability":.., "inputs":callable(data,seed)->dict} | "cap.x"]
               **None = 自动选链**（编排按需编译 P1-4）：规则层命中即用（0 次模型调用），
               未命中才退模型兜底；选中结果在 data["selection"] 里可查。
        reuse: {step: 上一轮的信封}。**只重跑被证伪的步骤**（P1-4）：上一轮 ok 的步骤
               原样复用结果与数据、不再执行、不占预算，被证伪的步骤才重跑。
               复用到的步骤在 data["reused"] 里列出。
        budget: 运行预算（步数/重复动作/模型调用上限）。None→默认熔断；dict 覆盖；
                False→显式关闭。超预算**如实停机**（ok=false + stopped_by + 状态落盘），
                绝不静默继续烧钱（P0-2，见 RunBudget 注释）。
        返回 {ok, data:{results:{step:信封}, flow, ok_steps, verdict, budget, selection, reused}}。
        每次运行都会往**成本账本**追加一条（P2 成本回归，见 cost_ledger.py 的注释）：把变量
        （提示措辞 hash / 编排链 / 努力档 / 模型）和成本量一起记下来，才有同口径可比。写账本失败不影响运行。
        """
        t0 = time.time()
        results = {}
        flow = {}
        data = dict(seed or {})
        rb = _resolve_run_budget(budget)
        # ── 编排按需编译：没给链就现场选一条（规则优先，模型只兜底）──
        selection = None
        if chain is None:
            sel = self.select_chain(task, rb=rb, model_fn=model_fn)
            selection = sel.get("data") or {}
            if not sel.get("ok"):
                out = {"task": task, "results": {}, "flow": {}, "ok_steps": [], "chain": [],
                       "budget": rb.snapshot(), "selection": selection,
                       "verdict": "选链失败：" + str(sel.get("error"))}
                return _fail("自动选链失败：" + str(sel.get("error")), degraded=False, data=out)
            chain = selection.get("chain") or []
            if not chain:
                out = {"task": task, "results": {}, "flow": {}, "ok_steps": [], "chain": [],
                       "budget": rb.snapshot(), "selection": selection,
                       "verdict": "选链未产出步骤"}
                return _fail("自动选链未产出任何步骤", degraded=False, data=out)
        reused_steps = []
        fused = None
        for item in chain:
            if isinstance(item, str):
                step = cap = item
                inputs_fn = lambda d, s, _cap=cap: {}
            else:
                step = item.get("step", item.get("capability"))
                cap = item["capability"]
                spec = item.get("inputs")
                # 静态入参规格（模板表写的 dict）走占位符解析；函数式入参照旧
                inputs_fn = (lambda d, s, _sp=spec: _resolve_step_inputs(_sp, d, task)) \
                    if isinstance(spec, dict) else (spec or (lambda d, s: {}))
            # 选择性重跑：上一轮已通过的步骤直接复用，连预算都不占
            prev = (reuse or {}).get(step)
            if isinstance(prev, dict) and prev.get("ok"):
                results[step] = prev
                if isinstance(prev.get("data"), dict):
                    data[step] = prev["data"]
                flow[step] = {"ok": True, "reused": True}
                reused_steps.append(step)
                if on_step:
                    on_step(step, prev, data)
                continue
            try:
                inputs = inputs_fn(data, seed or {})
                if task and cap == "plan.think":
                    inputs.setdefault("task", task)
                if task and cap == "plan.gen":
                    inputs.setdefault("task", task)
                # 运行预算熔断：入参已知才谈得上"同一动作"，故在算完 inputs 之后、执行之前判
                allowed, why = rb.step(cap, inputs)
                if not allowed:
                    fused = {"step": step, "capability": cap, "reason": why}
                    break
                # 组装链真实执行：test.run 环节若上游 gen 产出文件 dict，落盘到临时
                # 工作区并传 path（test 跑真实文件，红绿闭环非空壳）。
                # 修复 P0：不硬编码上游产文件步骤名为 "gen"。gen 步骤可叫 "gen"/"plan.gen"/
                # 任意自定义名，故扫描已产出 data，取第一个「files 为非空 dict(文件名->内容)」
                # 的步骤结果作为被测代码，避免 test.run 无 path → degraded。
                if cap == "test.run" and "path" not in inputs:
                    gen_files = {}
                    for _k, _v in data.items():
                        if isinstance(_v, dict) and isinstance(_v.get("files"), dict) and _v["files"]:
                            gen_files = _v["files"]
                            break
                    if gen_files:
                        tmp = _new_flow_ws()
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
        out = {"task": task, "results": results, "flow": flow,
               "ok_steps": ok_steps, "chain": [c.get("capability") if isinstance(c, dict) else c for c in chain],
               "budget": rb.snapshot(), "selection": selection, "reused": reused_steps,
               "verdict": "全部通过" if len(ok_steps) == len(chain) else f"通过 {len(ok_steps)}/{len(chain)}"}
        if fused:
            # 已执行步数取 rb.steps（计数器只累计被放行的步）——不用 len(results)：
            # results 以步骤名为键，步骤名重复时会互相覆盖（曾把 9 步算成 1 步）。
            done = rb.steps
            out["verdict"] = "熔断停机：" + fused["reason"]
            out["stopped_at"] = fused
            out["completed_steps"] = done
            out["unfinished"] = [c.get("capability") if isinstance(c, dict) else c
                                 for c in chain[done:]]
            self._record_fuse(task or "", fused, rb)
            self._record_cost(task, chain, rb, out, t0, stopped=fused.get("reason", ""))
            return _fail("运行预算熔断：" + fused["reason"], degraded=False, data=out)
        out["completed_steps"] = rb.steps
        self._record_cost(task, chain, rb, out, t0, stopped="")
        return _ok(out)

    def _record_cost(self, task, chain, rb, out, t0, stopped=""):
        """把这次运行的成本记进账本（P2 成本回归，依据 arXiv 2608.01347）。

        记的**不只是花费**，还有产生这笔花费的变量——不然两次运行没法同口径对比：
          prompt=任务原文（存 hash）/ harness=编排链的能力序列 / effort=预算档 / model=选链来源。
        成本量（确定性口径，不含真实模型 token——那要云端用量，见 llm 用量台账）：
          steps=放行的步数 / model_calls=模型调用次数 / tool_calls=真正执行的能力调用（不含复用步）
          est_tokens=链上搬动内容量的估算（len//4，随内容变化，用来抓"悄悄变重"）
        场景名取 env CODEAGENT_COST_SCENARIO（回归门用固定名），默认 "chain"。
        写失败只吞不抛：记账不许影响主流程。
        """
        try:
            caps = [c.get("capability") if isinstance(c, dict) else c for c in (chain or [])]
            payload = sum(len(str(v)) for v in (out.get("results") or {}).values())
            cost_ledger.record(
                os.environ.get("CODEAGENT_COST_SCENARIO") or "chain",
                prompt=task or "", effort=rb.snapshot().get("limits"),
                harness=[str(c) for c in caps],
                model=str((out.get("selection") or {}).get("source") or ""),
                steps=rb.steps, model_calls=rb.model_calls,
                tool_calls=max(0, rb.steps - len(out.get("reused") or [])),
                est_tokens=payload // 4, wall_ms=int((time.time() - t0) * 1000),
                extra={"reused": len(out.get("reused") or []), "stopped": stopped,
                       "stopped_by": rb.stopped_by or ""})
        except Exception:                            # noqa: BLE001  记账失败不配影响运行
            pass

    def _record_fuse(self, task, fused, rb):
        """熔断要留下可查的痕迹：写任务状态（action=set, state=halted）。
        状态面是两侧对账的底座，熔断这种"非正常结束"尤其必须落盘。写失败不影响停机结果。"""
        try:
            self.run_capability("taskstate.track", action="set", task=task or "(未命名任务)",
                                state="halted",
                                progress="运行预算熔断：%s（已跑 %d 步 / 模型 %d 次）"
                                         % (fused["reason"], rb.steps, rb.model_calls),
                                gate="run_budget")
        except Exception:
            pass

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
        errs = []
        if not review.get("ok"):
            errs.append("review: " + str(review.get("error") or "失败"))
        if ds is not None and not ds.get("ok"):
            errs.append("depscan: " + str(ds.get("error") or "失败"))
        if fu is not None and not fu.get("ok"):
            errs.append("fuzz: " + str(fu.get("error") or "失败"))
        if errs:
            msg = "；".join(errs)
            if "No such file" in msg or "FileNotFoundError" in msg:
                msg += "（Windows 下路径写 D:/xxx 或 D:\\xxx，别用 MSYS 的 /d/xxx）"
            d["degraded_items"] = errs
            env = _ok(d)
            env["ok"] = False
            env["degraded"] = True
            env["error"] = msg
            return env
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


# ── 进程内运行时单例（接线用） ──────────────────
_RUNTIME_SINGLETON = None


def get_runtime(**kwargs):
    """模块级运行时单例（懒加载）。供引擎/对话层做 harness 钩子接线，避免每次调用重扫 39 个原子。

    ponytail: 进程内单例，多进程各自持有；若将来需要多租户不同 agents_dir，升级为 keyed cache。
    """
    global _RUNTIME_SINGLETON
    if _RUNTIME_SINGLETON is None:
        _RUNTIME_SINGLETON = AgentRuntime(**kwargs)
    return _RUNTIME_SINGLETON


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
