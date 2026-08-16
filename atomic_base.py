#!/usr/bin/env python3
"""atomic_base.py — CodeAgent 原子智能体基类（开源侧，无第三方依赖）。

统一接口：`call` / `run` / `describe`。
生命周期：`discovered` → `loaded` → `ready`。
能力注册：`register()` 挂载能力函数，`capabilities()` 自省。
信封：所有返回统一 `{ok, data}`，异常 → `{ok:false, error, degraded:true}`。

铁律：本基类只做壳（调度/信封/生命周期），不碰核心算法。核心模块被 `run()`
内的能力函数 import 调用，一行不改。
"""

import traceback
from datetime import datetime, timezone


class AtomicAgent:
    """原子智能体基类。子类在 `__init__` 里声明身份并注册能力。

    子类必须提供类属性：name / version / domain / description；
    可选：open_source / depends_on / provides / inputs / outputs。
    """

    # ── 身份（子类覆盖）────────────────────────────
    name = "atomic-agent"
    version = "0.0.0"
    domain = "generic"
    description = ""
    open_source = True
    provides = []            # 暴露能力（如 "impact.analyze"）
    depends_on = []          # 依赖的原子 provides（本库内，open_source:true）
    inputs = []              # run() 入参名清单
    outputs = []             # data 里返回的字段名清单

    # ── 运行时状态 ────────────────────────────────
    _status = "discovered"   # discovered → loaded → ready
    _capabilities = None     # {能力名: callable}
    _loaded_at = None
    _manifest = None         # 若经 loader 加载，保存原始 manifest dict

    # ─────────────────────────────────────────────
    def __init__(self, manifest: dict = None):
        self._capabilities = {}
        self._manifest = manifest or {}
        # 允许 manifest 覆盖类属性（loader 加载时注入）
        for k in ("name", "version", "domain", "description", "open_source",
                  "provides", "depends_on", "inputs", "outputs"):
            if k in self._manifest:
                setattr(self, k, self._manifest[k])
        self._status = "loaded"

    # ── 生命周期 ─────────────────────────────────
    @property
    def status(self):
        return self._status

    def load(self):
        """从 loaded → ready：挂载默认能力（若子类未注册）。"""
        self._register_defaults()
        self._status = "ready"
        self._loaded_at = datetime.now(timezone.utc).isoformat()
        return self

    def unload(self):
        self._status = "loaded"
        return self

    def _register_defaults(self):
        """子类可覆盖：在 ready 前把能力挂上。默认不注册任何能力。"""
        pass

    # ── 能力注册 / 自省 ──────────────────────────
    def register(self, capability: str, fn: callable):
        """注册一个能力：capability 形如 'impact.analyze'，fn 为 (kwargs)->dict 函数。"""
        if not callable(fn):
            raise TypeError(f"capability {capability} 需要可调用对象")
        self._capabilities[capability] = fn
        return self

    def capabilities(self) -> dict:
        """自省：返回 {能力名: {"callable": bool, "declared": bool}}。"""
        out = {}
        for cap in self.provides:
            out[cap] = {
                "declared": True,
                "callable": cap in self._capabilities,
            }
        for cap in self._capabilities:
            if cap not in out:
                out[cap] = {"declared": False, "callable": True}
        return out

    # ── 信封 ─────────────────────────────────────
    @staticmethod
    def _envelope(ok: bool, data=None, error=None, degraded=False):
        """统一 {ok, data} 信封。"""
        if ok:
            return {"ok": True, "data": data if data is not None else {}}
        # 修复 P2-2：用 `is not None` 而非 `or {}`，避免合法 falsy 数据（0/""/[]）被吞
        return {"ok": False, "data": data if data is not None else {},
                "error": error or "unknown error", "degraded": degraded}

    # ── 统一接口 ─────────────────────────────────
    def call(self, capability: str, **kwargs) -> dict:
        """调用某个能力，包 {ok, data} 信封；异常捕获 → {ok:false, error, degraded:true}。
        修复 P2-3：仅允许 `ready` 状态执行；`loaded` 需先 `load()`（否则能力未挂载，
        返回明确的「未 ready」而非误导性的「能力未注册」。"""
        fn = self._capabilities.get(capability)
        if fn is None:
            return self._envelope(False, error=f"能力未注册: {capability}", degraded=True)
        if self._status != "ready":
            return self._envelope(False, degraded=True,
                                  error=f"原子未 ready（当前 {self._status}），需先 load()")
        try:
            result = fn(**kwargs)
            if isinstance(result, dict) and "ok" in result and "data" in result:
                # 能力已返回信封 → 透传
                return result
            return self._envelope(True, data=result)
        except Exception as e:  # 失败降级：绝不抛给上层
            return self._envelope(
                False, error=f"{type(e).__name__}: {e}",
                degraded=True, data={"trace": traceback.format_exc(limit=3)})

    def run(self, **kwargs) -> dict:
        """主入口。默认调用主能力：provides[0] 对应 `run` 入参的 capability 名。"""
        if not self.provides:
            return self._envelope(False, error="原子未声明 provides", degraded=True)
        cap = kwargs.pop("_capability", self.provides[0])
        return self.call(cap, **kwargs)

    def describe(self) -> dict:
        """统一自省描述。"""
        return {
            "name": self.name,
            "version": self.version,
            "domain": self.domain,
            "description": self.description,
            "open_source": self.open_source,
            "status": self._status,
            "loaded_at": self._loaded_at,
            "provides": list(self.provides),
            "depends_on": list(self.depends_on),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "capabilities": self.capabilities(),
            "manifest": self._manifest or {},
        }


def run_cli(agent: "AtomicAgent", run_args: dict = None, description: str = ""):
    """原子 CLI 公共样板（修复 P2-9：收敛各 main.py 重复的 __main__ argparse 头）。

    用法（各原子 main.py）:
        if __name__ == "__main__":
            sys.exit(run_cli(agent, run_args={
                "capability": {"default": agent.provides[0], "choices": list(agent.provides)},
                "path": {},
            }))
    参数: run_args 为 {argname: argparse.add_argument 的 kwargs}; run_args 会合并进
    _capability 调用。加载→describe→run→JSON 输出；失败退出 1。
    """
    import argparse
    import json
    import sys as _sys

    ap = argparse.ArgumentParser(description=description or f"{agent.name} 原子 CLI 自测入口")
    args_spec = run_args or {}
    for a_name, a_kw in args_spec.items():
        ap.add_argument("--" + a_name, **a_kw)
    ns = ap.parse_args()
    agent.load()
    cap = getattr(ns, "capability", None) or agent.provides[0]
    if cap not in agent.capabilities():
        print(f"原子 {agent.name} 无能力 {cap}; 可选={list(agent.provides)}")
        return 1
    kw = {k: v for k, v in vars(ns).items() if k != "capability"}
    print(f"══ {agent.name} 原子自测 ══ v{agent.version} status={agent.describe()['status']}")
    r = agent.run(_capability=cap, **{k: v for k, v in kw.items() if v is not None})
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"] and not r.get("degraded"):
        return 1
    return 0
