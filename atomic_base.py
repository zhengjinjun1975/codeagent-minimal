#!/usr/bin/env python3
"""atomic_base.py — CodeAgent 原子智能体基类（开源侧，无第三方依赖）。

统一接口：`call` / `run` / `describe`。
生命周期：`discovered` → `loaded` → `ready`。
能力注册：`register()` 挂载能力函数，`capabilities()` 自省。
信封：所有返回统一 `{ok, data}`，异常 → `{ok:false, error, degraded:true}`。

铁律：本基类只做壳（调度/信封/生命周期），不碰核心算法。核心模块被 `run()`
内的能力函数 import 调用，一行不改。
"""

import inspect
import traceback
import contextlib
from datetime import datetime, timezone

# ── 错误码（失败要能分类，不能全糊成 degraded）──────────────────────
# 为什么要有这件：`degraded: true` 把「你参数写错了」和「原子内部真的挂了」混成同一个信封，
# 上层只看到"降级"，根因在日志里消失。编排传参错误会被误当成业务失败，
# 于是链继续往下跑，下游拿不到上游数据再级联降级 —— 最终看到的是第三步失败，
# 真正原因（第一步传了不存在的参数）已经找不到。
# 故按"谁的错"分码：
#   CAP_UNKNOWN   能力名不存在（编排写错名）           → 不重试，改代码
#   CAP_CONTRACT  参数接不住/缺必填（编排写错参）      → 不重试，改代码
#   ATOM_NOT_READY 原子未 load()（编排忘了初始化）     → 修调用顺序
#   ATOM_FAIL     原子内部异常（业务/环境问题）        → 可能重试或降级
#   ATOM_TYPE     原子内部抛 TypeError（不是传参错）   → 原子自己的 bug
ERR_UNKNOWN = "CAP_UNKNOWN"
ERR_CONTRACT = "CAP_CONTRACT"
ERR_NOT_READY = "ATOM_NOT_READY"
ERR_ATOM_FAIL = "ATOM_FAIL"
ERR_ATOM_TYPE = "ATOM_TYPE"

# 契约类错误（改代码才能好，重试无意义，链上应停机而不是级联降级）
CONTRACT_CODES = frozenset({ERR_UNKNOWN, ERR_CONTRACT})

try:
    import msvcrt  # Windows 跨进程文件锁
except ImportError:
    msvcrt = None
try:
    import fcntl  # POSIX 跨进程文件锁
except ImportError:
    fcntl = None


@contextlib.contextmanager
def exclusive_file_lock(path):
    """跨进程排它锁（粒度 = 传入的那个文件）。

    append 型原子都要“读旧值 + 追加”这一步原子，多进程同时干会撞号甚至丢行。
    实现：锁文件与目标同目录，Windows 用 msvcrt.locking，POSIX 用 fcntl.flock。
    """
    f = open(path + ".lock", "a+b")
    try:
        if msvcrt is not None:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        elif fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if msvcrt is not None:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


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
    def _envelope(ok: bool, data=None, error=None, degraded=False, code=None):
        """统一 {ok, data} 信封。code 只在失败且能分类时出现（成功信封不带，免得噪声）。"""
        if ok:
            return {"ok": True, "data": data if data is not None else {}}
        # 修复 P2-2：用 `is not None` 而非 `or {}`，避免合法 falsy 数据（0/""/[]）被吞
        out = {"ok": False, "data": data if data is not None else {},
               "error": error or "unknown error", "degraded": degraded}
        if code:
            out["code"] = code
        return out

    # ── 参数契约（bind 预检）──────────────────────
    def accepts(self, capability: str) -> dict:
        """返回某能力**实际能接住的参数**，供调用前核对。

        拿不到签名时 `available=False`，调用方不据此拦截（不猜、不误伤）。
        `has_kwargs=True` 表示该能力接受任意键，预检跳过（接不住才是有意义的判定）。
        """
        fn = self._capabilities.get(capability)
        if fn is None:
            return {"available": False, "reason": "能力未注册"}
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError) as e:  # fail-open: 内省不到签名就不拦截，交运行时兜底
            return {"available": False, "reason": "签名不可内省: %s: %s" % (type(e).__name__, e)}
        names, required, has_kw = [], [], False
        for name, p in sig.parameters.items():
            if p.kind is p.VAR_KEYWORD:
                has_kw = True
                continue
            if p.kind is p.VAR_POSITIONAL:
                continue
            names.append(name)
            if p.default is p.empty:
                required.append(name)
        return {"available": True, "params": names, "required": required, "has_kwargs": has_kw}

    def _precheck(self, capability: str, kwargs: dict):
        """调用前的参数契约核对。返回 None = 放行，否则返回失败信封。

        这是**编排错误唯一能在执行前被拦住的位置**：静态图谱只管源码里的字面量调用，
        包装器转发（能力名来自变量）它看不见；这一层在运行时把那个盲区补上。
        """
        acc = self.accepts(capability)
        if not acc.get("available") or acc.get("has_kwargs"):
            return None
        unexpected = sorted(set(kwargs) - set(acc["params"]))
        if unexpected:
            return self._envelope(
                False, degraded=False, code=ERR_CONTRACT,
                error="参数接不住：%s.%s 不接受 %s（可用 %s）"
                      % (self.name, capability, unexpected, acc["params"]),
                data={"capability": capability, "atom": self.name,
                      "unexpected": unexpected, "accepted": acc["params"]})
        missing = sorted(set(acc["required"]) - set(kwargs))
        if missing:
            return self._envelope(
                False, degraded=False, code=ERR_CONTRACT,
                error="缺必填参数：%s.%s 需要 %s" % (self.name, capability, missing),
                data={"capability": capability, "atom": self.name,
                      "missing": missing, "required": acc["required"]})
        return None

    # ── 统一接口 ─────────────────────────────────
    def call(self, capability: str, **kwargs) -> dict:
        """调用某个能力，包 {ok, data} 信封；异常捕获 → {ok:false, error, degraded:true}。
        修复 P2-3：仅允许 `ready` 状态执行；`loaded` 需先 `load()`（否则能力未挂载，
        返回明确的「未 ready」而非误导性的「能力未注册」。

        加固（2026-10-23）：调用前做参数契约核对，把「传错参数」从 degraded 里分出来
        （code=CAP_CONTRACT，degraded=False）。不这么做，编排错误会被当成业务降级，
        链继续跑、下游级联失败，根因被埋在 data.trace 第三行。"""
        fn = self._capabilities.get(capability)
        if self._status != "ready":
            # 先判状态再看能力表：`_register_defaults` 在 load() 里才跑，
            # 没 load 的原子能力表是空的 —— 先查表会把"忘了 load"误报成"能力不存在"，
            # 那正好把调用顺序问题指错了方向。
            return self._envelope(False, degraded=True, code=ERR_NOT_READY,
                                  error=f"原子未 ready（当前 {self._status}），需先 load()")
        if fn is None:
            return self._envelope(False, degraded=True, code=ERR_UNKNOWN,
                                  error=f"能力未注册: {capability}",
                                  data={"capability": capability, "atom": self.name})
        bad = self._precheck(capability, kwargs)
        if bad is not None:
            return bad
        return self._invoke(fn, capability, kwargs)

    def _invoke(self, fn, capability, kwargs):
        """真正执行 + 异常分类。单独抽出来是为了让 call() 只做判定（宪法门会数圈复杂度）。"""
        try:
            result = fn(**kwargs)
        except TypeError as e:
            # 边界上的 TypeError = 参数绑不上（tb 只有本帧）；函数体内的 TypeError 是原子自己的 bug。
            # 这个区分靠 traceback 深度，不做类型猜测（猜错会掩盖真 bug）。
            tb = e.__traceback__
            at_boundary = tb is not None and tb.tb_next is None
            return self._envelope(
                False, degraded=not at_boundary, code=ERR_CONTRACT if at_boundary else ERR_ATOM_TYPE,
                error=f"{type(e).__name__}: {e}",
                data={"trace": traceback.format_exc(limit=3),
                      "capability": capability, "atom": self.name})
        except Exception as e:  # 失败降级：绝不抛给上层
            return self._envelope(
                False, error=f"{type(e).__name__}: {e}", code=ERR_ATOM_FAIL,
                degraded=True, data={"trace": traceback.format_exc(limit=3)})
        if isinstance(result, dict) and "ok" in result and "data" in result:
            return result                     # 能力自己已返回信封 → 透传
        return self._envelope(True, data=result)

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
