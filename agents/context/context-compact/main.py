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

# 上下文腐烂对策核心（同目录，纯 stdlib）：工具输出卸载 + 技能渐进披露
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import context_harness as ctxh

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
    """对步骤列表应用 max_tokens 预算：**真裁到预算内**（不是只打标记）。

    三级处置（借鉴 Codex compaction 的降级思路）：
      放得下        → 原样保留
      放不下        → 压成关键字段（summary/score/verdict...），标记 _truncated_by_budget
      压完还放不下  → 丢（能塞下小占位就留占位，塞不下就只计数）——**保证返回总量 ≤ 预算**
    action 供上游决策：keep（没动）/ compact（压过）/ offload（丢过，建议改走 context.offload 落盘）。

    改造前的问题：超限只往条目标一个 _truncated_by_budget 就原样返回，`used_tokens` 照样超预算
    ——预算等于没生效（实测 max_tokens=100 时 overshoot 一直 >0）。
    """
    steps = list(steps or [])
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        used = sum(estimate_tokens(str(s)) for s in steps)
        return {"steps": steps, "used_tokens": used, "budget": max_tokens,
                "overshoot": max(0, used - (max_tokens if isinstance(max_tokens, int) else 0)),
                "compacted": 0, "dropped": 0, "dropped_capabilities": [],
                "action": "keep", "enforced": False,
                "reason": "max_tokens 非法(≤0/非整数)：未裁剪"}

    out, used, compacted, dropped, dropped_caps = [], 0, 0, 0, []
    for st in steps:
        t = estimate_tokens(str(st))
        if used + t <= max_tokens:                      # 放得下
            out.append(st)
            used += t
            continue
        small = compact(st)                             # 压成关键字段
        if isinstance(small, dict):
            small["_truncated_by_budget"] = True
        ts = estimate_tokens(str(small))
        if used + ts <= max_tokens:
            out.append(small)
            used += ts
            compacted += 1
            continue
        cap = st.get("capability") if isinstance(st, dict) else None
        stub = {"_dropped_by_budget": True, "_capability": cap,
                "_was_tokens": t} if cap else {"_dropped_by_budget": True, "_was_tokens": t}
        tstub = estimate_tokens(str(stub))
        if used + tstub <= max_tokens:                  # 留个占位，让上游知道丢了什么
            out.append(stub)
            used += tstub
        dropped += 1
        if cap:
            dropped_caps.append(cap)

    action = "offload" if dropped else ("compact" if compacted else "keep")
    return {"steps": out, "used_tokens": used, "budget": max_tokens, "overshoot": 0,
            "compacted": compacted, "dropped": dropped,
            "dropped_capabilities": dropped_caps, "action": action, "enforced": True}


def _item_key(x):
    """条目身份键（一处定义在 context_harness.item_key，别两份口径）。"""
    return ctxh.item_key(x)


def _handle_of(x):
    """可恢复句柄（一处定义在 context_harness.handle_of）。"""
    return ctxh.handle_of(x)


def compaction_cost(before=None, after=None):
    """压缩代价核算（P0-3，依据 arXiv 2608.16370《What Does Context Compression Cost an Agent?》）。

    论文的要点：**任务完成率会掩盖压缩代价**——被压掉的内容后面往往要"重新取回"，
    那笔额外工具调用没人记账，于是压缩改动的评测会假绿。这里做确定性记账（纯函数、不调模型）：

      - 丢掉的条目按**有没有可恢复句柄**分三类：带落盘路径(file) / 带能力句柄(capability) / 真丢(无)
      - recoverable = 前两类（取回下限各 1 步）；lost = 真丢（无法取回，带走了多少 token 也记上）
      - reacquisition_hops = recoverable × 1（有句柄）；inline 不动记 0
      - verdict: no-compaction / fact-preserving / mixed / lossy

    前置约定：`after` 与 `before` **按位置对齐**（budget_trim 就是用 stub 原地替换，天然对齐）；
    长度不一致时退化为按键匹配（键 = capability/label/step），无法确认的条目按"缺失"计入。

    判据用途：同一个用例上，"带句柄的丢" 必须比 "无句柄的丢" 得分**严格更好**——
    否则这一维没有分辨力，等于给评测门加了个只会说绿的花架子。
    """
    before = list(before or [])
    after = list(after or [])
    if not before:
        return {"ok": True, "data": {"verdict": "no-compaction", "before": 0, "after": len(after),
                                     "inline": 0, "compacted": 0, "dropped": 0,
                                     "recoverable": 0, "lost": 0, "lost_tokens": 0,
                                     "reacquisition_hops": 0,
                                     "note": "压缩前为空，无从比较"}}
    aligned = len(after) == len(before)
    by_key = {_item_key(a): a for a in after} if not aligned else {}

    n_inline = n_compacted = n_dropped = n_lost = n_hops = n_lost_tokens = 0
    details = []
    for i, b in enumerate(before):
        a = after[i] if aligned and i < len(after) else by_key.get(_item_key(b))
        if a is None:
            # 压完直接没了、连占位都没有 → 真丢（最差：上游不知道自己少了什么）
            n_dropped += 1
            n_lost += 1
            n_lost_tokens += int((b or {}).get("_was_tokens", 0) or 0) if isinstance(b, dict) else 0
            details.append({"item": _item_key(b), "kind": "lost-no-placeholder"})
            continue
        if isinstance(a, dict) and a.get("_dropped_by_budget"):
            kind, ref = _handle_of(a)
            n_dropped += 1
            if kind:
                n_hops += 1
                details.append({"item": _item_key(b), "kind": "recoverable-%s" % kind, "ref": ref[:120]})
            else:
                n_lost += 1
                n_lost_tokens += int(a.get("_was_tokens", 0) or 0)
                details.append({"item": _item_key(b), "kind": "lost"})
            continue
        if isinstance(a, dict) and a.get("_truncated_by_budget"):
            n_compacted += 1                       # 压小但仍在本体，暂无需重取
            details.append({"item": _item_key(b), "kind": "compacted"})
            continue
        n_inline += 1

    recoverable = n_dropped - n_lost
    if n_dropped == 0 and n_compacted == 0:
        verdict = "no-compaction"
    elif n_dropped == 0:
        verdict = "compacted-inline"                # 压小但仍在本体，暂无需重取
    elif n_lost == 0:
        verdict = "fact-preserving"                # 丢了但有句柄可取回
    elif recoverable > 0:
        verdict = "mixed"
    else:
        verdict = "lossy"                          # 只丢不保：事实证明不了、也取不回来
    return {"ok": True, "data": {
        "verdict": verdict, "before": len(before), "after": len(after),
        "inline": n_inline, "compacted": n_compacted, "dropped": n_dropped,
        "recoverable": recoverable, "lost": n_lost, "lost_tokens": n_lost_tokens,
        "reacquisition_hops": n_hops, "detected_by": "position" if aligned else "key",
        "details": details[:20]}}


class ContextCompactAgent(AtomicAgent):
    name = "context-compact"
    version = "0.5.0"
    domain = "context"
    description = ("上下文压缩原子（P1 上下文量化，借鉴Codex context_manager/compaction）: token估算 + 链结果压缩"
                   "(保留summary/score/verdict丢弃大payload) + max_tokens预算**真裁到预算内**(压缩/丢弃+action建议) + 上下文腐烂对策"
                   "(offload工具输出头尾截断+全文落盘, disclose技能渐进披露)"
                   " + 上下文编辑工具化(P1-5): 编辑表(drop/collapse/offload/pin)由agent自己下 + 卸载产物按句柄"
                   "无损取回(store/reveal)。纯stdlib。")
    provides = ["context.estimate", "context.compact", "context.budget",
                "context.offload", "context.disclose", "context.compaction_cost",
                # P1-5 上下文编辑工具化（arXiv 2607.23809）：编辑权交给 agent + 丢掉的无损可取回
                "context.edit", "context.store", "context.reveal"]
    depends_on = []
    inputs = ["data", "text", "steps", "max_tokens", "keep", "cap",
              "content", "label", "keep_head", "keep_tail", "store_dir",
              "registry_desc", "task", "top_k", "before", "after",
              "items", "edits", "handle", "offset", "limit"]
    outputs = ["tokens", "compact", "_compact", "used_tokens", "budget", "overshoot",
               "compacted", "dropped", "dropped_capabilities", "action", "enforced",
               "inlined", "offloaded_path", "original_tokens", "inlined_tokens",
               "loaded", "deferred_count", "verdict", "recoverable", "lost",
               "lost_tokens", "reacquisition_hops",
               "items", "saved", "applied", "skipped", "handles", "stored",
               "tokens_before", "tokens_after", "count", "text", "next_offset", "done"]

    def _register_defaults(self):
        self.register("context.estimate", self._estimate)
        self.register("context.compact", self._compact)
        self.register("context.budget", self._budget)
        # 上下文腐烂对策（薄壳，核心在 context_harness.py）
        self.register("context.offload", self._offload)
        self.register("context.disclose", self._disclose)
        # 压缩代价核算（P0-3：完成率会掩盖"重新取回"的代价）
        self.register("context.compaction_cost", self._compaction_cost)
        # 上下文编辑工具化（P1-5，arXiv 2607.23809）：编辑权交给 agent + 丢掉的无损可取回
        self.register("context.edit", self._edit)
        self.register("context.store", self._store)
        self.register("context.reveal", self._reveal)

    def _compaction_cost(self, before=None, after=None):
        """压缩代价核算入口：把纯函数结果包成统一信封（异常不许穿出去）。"""
        try:
            d = compaction_cost(before=before, after=after)["data"]
        except Exception as e:
            return self._envelope(False, degraded=True,
                                  error="compaction_cost 失败: %s: %s" % (type(e).__name__, e))
        return self._envelope(True, data=d)

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

    # ── 上下文腐烂对策（薄壳：核心实现全在 context_harness.py，一行不改）──
    def _offload(self, content=None, label="tool_output", keep_head=800,
                 keep_tail=400, store_dir=None):
        """工具输出卸载：超长则头尾截断 + 全文落盘，返回 inlined/offloaded_path/tokens。"""
        if content is None:
            return self._envelope(False, degraded=True, error="缺 content 入参")
        return self._envelope(True, data=ctxh.context_offload(
            content, label=label, keep_head=keep_head, keep_tail=keep_tail, store_dir=store_dir))

    # ── 上下文编辑工具化（薄壳：核心在 context_harness.py）──
    def _edit(self, items=None, edits=None, store_dir=None, keep_head=200, keep_tail=120):
        """按编辑表改上下文：drop/collapse/offload/pin/unpin。未命中或不认的 op 一律带原因回报。"""
        if items is None or edits is None:
            return self._envelope(False, degraded=True, error="缺 items / edits 入参")
        try:
            d = ctxh.context_edits(items, edits, store_dir=store_dir,
                                   keep_head=keep_head, keep_tail=keep_tail)
        except Exception as e:
            return self._envelope(False, degraded=True,
                                  error="context_edits 失败: %s: %s" % (type(e).__name__, e))
        return self._envelope(True, data=d)

    def _store(self, store_dir=None):
        """列出已卸载产物（句柄/字节/时间），供 agent 决定取回哪份。"""
        return self._envelope(True, data=ctxh.store_list(store_dir=store_dir))

    def _reveal(self, handle=None, offset=0, limit=4000, store_dir=None):
        """按句柄取回卸载产物的一段（无损取回；只许 store 内的产物）。"""
        if not handle:
            return self._envelope(False, degraded=True, error="缺 handle 入参")
        return self._envelope(True, data=ctxh.store_reveal(handle, offset=offset, limit=limit,
                                                          store_dir=store_dir))

    def _disclose(self, registry_desc=None, task="", top_k=5):
        """技能渐进披露：只放最相关的 top_k 个条目，其余延后。"""
        if registry_desc is None:
            return self._envelope(False, degraded=True, error="缺 registry_desc 入参")
        return self._envelope(True, data=ctxh.disclose(registry_desc, task, top_k))


def _trunc(s, n):
    s = str(s)
    return s[:n] + ("…" if len(s) > n else "")


agent = ContextCompactAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(ContextCompactAgent(), run_args={
        "capability": {"default": "context.estimate", "choices": list(ContextCompactAgent.provides)},
        "text": {}, "data": {}, "steps": {}, "max_tokens": {},
        "content": {}, "label": {}, "task": {}, "top_k": {"type": int}, "store_dir": {},
    }))
