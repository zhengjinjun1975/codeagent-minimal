#!/usr/bin/env python3
"""context-compact 原子壳（open_source:true）——上下文压缩（P2）。

借鉴 Codex `context_manager` + `compaction`（token 估算 + 多种压缩策略 + token 预算），
用纯 stdlib 实现精简版：token 估算 + 链结果压缩（保留 summary/score/verdict，丢弃大 payload）+
max_tokens 预算裁剪。契合 codeagent 组装链 run_chain 把全量 {ok,data} 传给下游导致的膨胀。

能力（纯 stdlib，数据不出厂）：
  context.estimate — 估算文本 token 数（ASCII/4 + 非ASCII按字符，启发式）
  context.compact  — 压缩单条链结果（保留关键字段, 截断长串, 丢弃大 payload）
  context.budget   — 对步骤列表应用 max_tokens 预算，超限裁剪/降级
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

_KEEP_KEYS = ("summary", "verdict", "score", "decision", "total", "ok",
              "granted", "safe", "rc", "tier", "degraded")


def estimate_tokens(text) -> int:
    """启发式 token 估算：ASCII 每 ~4 字符 1 token，非ASCII(中日韩等) 每字符 ~1 token。"""
    if text is None:
        return 0
    if isinstance(text, (dict, list)):
        text = str(text)
    s = str(text)
    if not s:
        return 0
    ascii_chars = sum(1 for ch in s if ord(ch) < 128)
    other = len(s) - ascii_chars
    return max(1, ascii_chars // 4 + other)


def compact(data, keep=None, cap=200):
    """压缩单条链结果：保留关键字段, 截断长串, 丢弃大 payload。"""
    keep = keep or _KEEP_KEYS
    if data is None:
        return None
    if isinstance(data, dict):
        out = {k: data[k] for k in keep if k in data}
        if not out:
            # 无关键字段 → 保留前几个标量字段 + 总大小标注
            out = {k: data[k] for k in list(data)[:6]
                   if not isinstance(data[k], (dict, list))}
        for k in list(out.keys()):
            if isinstance(out[k], str) and len(out[k]) > cap:
                out[k] = out[k][:cap] + "…"
        out["_compact"] = True
        return out
    if isinstance(data, (list, tuple)):
        return {"_compact": True, "_items": len(data),
                "_tokens": estimate_tokens(str(data))}
    s = str(data)
    return s[:cap] + ("…" if len(s) > cap else "")


def budget_trim(steps, max_tokens):
    """对步骤列表应用 max_tokens 预算：累计超限后丢弃/截断。"""
    steps = list(steps or [])
    used = 0
    out = []
    for st in steps:
        t = estimate_tokens(str(st))
        if used + t > max_tokens:
            st = dict(st) if isinstance(st, dict) else {"_": st}
            st["_truncated_by_budget"] = True
            st["_budget_tokens"] = t
        out.append(st)
        used += t
    return {"steps": out, "used_tokens": used, "budget": max_tokens,
            "overshoot": max(0, used - max_tokens)}


class ContextCompactAgent(AtomicAgent):
    name = "context-compact"
    version = "0.1.0"
    domain = "context"
    description = ("上下文压缩原子（P2，借鉴Codex context_manager/compaction）: token估算 + 链结果压缩"
                   "(保留summary/score/verdict丢弃大payload) + max_tokens预算裁剪。防组装链上下文膨胀。纯stdlib。")
    provides = ["context.estimate", "context.compact", "context.budget"]
    depends_on = []
    inputs = ["data", "text", "steps", "max_tokens", "keep", "cap"]
    outputs = ["tokens", "compact", "_compact", "used_tokens", "budget", "overshoot"]

    def _register_defaults(self):
        self.register("context.estimate", self._estimate)
        self.register("context.compact", self._compact)
        self.register("context.budget", self._budget)

    def _estimate(self, text=None):
        return {"tokens": estimate_tokens(text), "text": _trunc(text, 50)}

    def _compact(self, data=None, keep=None, cap=200):
        if data is None:
            return self._envelope(False, degraded=True, error="缺 data 入参")
        return {"compact": compact(data, keep=keep, cap=cap),
                "tokens": estimate_tokens(str(data))}

    def _budget(self, steps=None, max_tokens=4000):
        if steps is None:
            return self._envelope(False, degraded=True, error="缺 steps 入参")
        return budget_trim(steps, max_tokens)


def _trunc(s, n):
    s = str(s)
    return s[:n] + ("…" if len(s) > n else "")


agent = ContextCompactAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(ContextCompactAgent(), run_args={
        "capability": {"default": "context.estimate", "choices": list(ContextCompactAgent.provides)},
        "text": {}, "data": {}, "steps": {}, "max_tokens": {},
    }))
