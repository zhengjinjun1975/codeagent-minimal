#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""budget_gate.py — 显式有限预算门（⑤ prime-agent 诚实预算模式, 纯标准库零依赖）。

背景：长任务/自主续跑若不设显式预算，容易"假装成功"——到点静默返回当前结果当完成。
prime-agent(RLM harness) 的做法：autonomous 续跑携带**显式有限预算**(最多 N 次延续 /
轮数 / 累计 token / 墙钟分钟)，任一维度到点即停，并如实暴露"预算耗尽"，绝不假装完成。

本模块落地为一个**显式有限预算对象**，供任一处长循环(如 code_agent_engine.implement 的
loop 优化)包裹使用：
  - 默认 finite：需显式给出至少一个有限维度，否则构造即报错(杜绝"自欺"式无限预算)。
  - 到点即停：调用方每轮真实工作后调 step_iteration()；每轮顶部查 exhausted() 决定是否停，
    给当前一轮收尾机会，绝不假装已完成。
  - 如实暴露：status()/reason() 明确给出 budget_ok / budget_exhausted + 命中的维度与原因；
    可抛 BudgetExhausted 异常供上层捕获后转诚实结果标记。
  - 缓存读不计：只有调用方显式 step_iteration(tokens=..) 的真实工作才消耗预算。

维度(任一到达即 exhausted；None = 该维不设限，但至少须一个有限维)：
  max_iterations     : 续跑/轮数上限(仿 rlm N 次延续)
  max_wallclock_sec  : 墙钟秒上限(仿 rlm 30 分钟墙钟；到点即停)
  max_tokens         : 累计 token 上限(仿 rlm 8 万 token；可选，由调用方上报)
"""
import threading
import time


class BudgetExhausted(Exception):
    """预算耗尽哨兵。reason=人类可读原因, dimension=命中维度(iterations/tokens/wallclock)。"""

    def __init__(self, reason, dimension=None):
        super().__init__(reason)
        self.reason = reason
        self.dimension = dimension


class IterationBudget:
    """显式有限预算门。默认 finite；任一维度到点 → exhausted=True，绝不假装完成。"""

    def __init__(self, max_iterations=None, max_wallclock_sec=None, max_tokens=None,
                 reason_label="task"):
        dims = (max_iterations is not None, max_wallclock_sec is not None,
                max_tokens is not None)
        if not any(dims):
            raise ValueError(
                "budget_gate: 预算必须为显式有限(至少给一个上限)。无上限的预算 = 自欺, 拒绝构造。")
        self.max_iterations = max_iterations
        self.max_wallclock_sec = max_wallclock_sec
        self.max_tokens = max_tokens
        self.reason_label = reason_label
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self.iterations = 0
        self.tokens = 0
        self._exhausted = False
        self._reason = ""
        self._dim = None

    # ── 只读探针 ─────────────────────────────────
    def elapsed(self):
        return time.monotonic() - self._started

    def remaining_iterations(self):
        if self.max_iterations is None:
            return None
        return max(0, self.max_iterations - self.iterations)

    def _evaluate(self):
        """任一有限维到点 → 置 exhausted(首达者胜), 幂等。"""
        if self._exhausted:
            return
        checks = []
        if self.max_iterations is not None and self.iterations >= self.max_iterations:
            checks.append(("iterations",
                           f"续跑/轮数达上限 {self.max_iterations} 轮"))
        if self.max_tokens is not None and self.tokens >= self.max_tokens:
            checks.append(("tokens",
                           f"累计token达上限 {self.max_tokens}"))
        if self.max_wallclock_sec is not None and self.elapsed() >= self.max_wallclock_sec:
            checks.append(("wallclock",
                           f"墙钟达上限 {self.max_wallclock_sec}s({self.max_wallclock_sec/60:.1f}分钟)"))
        if checks:
            self._dim, self._reason = checks[0]
            self._exhausted = True

    def exhausted(self):
        with self._lock:
            self._evaluate()
            return self._exhausted

    def reason(self):
        with self._lock:
            self._evaluate()
            if not self._exhausted:
                return "budget_ok(仍有预算)"
            return f"budget_exhausted[{self._dim}] {self._reason}"

    # ── 消耗预算（只有真实工作调用, 缓存读不计）────
    def step_iteration(self, tokens=0, allow_raise=False):
        """记一轮真实工作(tokens 可选累计)。返回仍有余量则 True；耗尽且 allow_raise → 抛
        BudgetExhausted。到点即停由调用方每轮顶部查 exhausted() 决定，给收尾机会。"""
        with self._lock:
            self.iterations += 1
            self.tokens += int(tokens or 0)
            self._evaluate()
            ex = self._exhausted
            reason, dim = self._reason, self._dim
        if ex and allow_raise:
            raise BudgetExhausted(reason, dim)
        return not ex

    def status(self):
        with self._lock:
            self._evaluate()
            return {
                "budget": "finite",
                "label": self.reason_label,
                "exhausted": self._exhausted,
                "reason": self._reason or "budget_ok",
                "dimension": self._dim,
                "iterations": self.iterations,
                "max_iterations": self.max_iterations,
                "tokens": self.tokens,
                "max_tokens": self.max_tokens,
                "elapsed_sec": round(self.elapsed(), 2),
                "max_wallclock_sec": self.max_wallclock_sec,
                "remaining_iterations": self.remaining_iterations(),
            }


def _selftest():
    """最小自测：预算耗尽如实返回 budget_exhausted, 不假装完成。"""
    import json
    results = {"checks": [], "pass": True}

    def check(name, cond, detail=""):
        results["checks"].append({"name": name, "ok": bool(cond),
                                  "detail": detail if not cond else ""})
        results["pass"] = results["pass"] and bool(cond)

    # 1) 轮数预算到顶 → exhausted[iterations], reason 如实
    b = IterationBudget(max_iterations=3, reason_label="minimal")
    steps = [b.step_iteration() for _ in range(3)]
    s = b.status()
    check("iterations exhausted", s["exhausted"] and s["dimension"] == "iterations",
          f"status={s}")
    check("iterations stop signal", steps == [True, True, False],
          f"steps={steps}")  # 第3轮后返回 False → 到点即停
    check("no-fake-complete", "budget_exhausted" in b.reason(),
          f"reason={b.reason()}")
    # 2) 无上限预算 = 自欺 → 拒绝构造
    try:
        IterationBudget()
        check("reject unbounded", False, "无上限预算被允许(应拒绝)")
    except ValueError:
        check("reject unbounded", True)
    # 3) token 预算到顶
    b2 = IterationBudget(max_iterations=10, max_tokens=100, reason_label="tokens")
    b2.step_iteration(tokens=60)
    b2.step_iteration(tokens=50)  # 累计110 > 100 → exhausted
    s2 = b2.status()
    check("token exhausted", s2["exhausted"] and s2["dimension"] == "tokens",
          f"status={s2}")
    # 4) 墙钟预算到顶(极短) 
    b3 = IterationBudget(max_iterations=100, max_wallclock_sec=0.01, reason_label="clock")
    time.sleep(0.02)
    b3.step_iteration()
    check("wallclock exhausted", b3.exhausted() and b3.status()["dimension"] == "wallclock",
          f"status={b3.status()}")
    # 5) allow_raise → BudgetExhausted
    b4 = IterationBudget(max_iterations=1, reason_label="raise")
    b4.step_iteration()
    try:
        b4.step_iteration(allow_raise=True)
        check("raise BudgetExhausted", False, "未抛 BudgetExhausted")
    except BudgetExhausted as e:
        check("raise BudgetExhausted", e.dimension == "iterations", f"e={e.reason}")

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results["pass"]


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
