#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cost_ledger.py — 成本回归账本（P2，依据 arXiv 2608.01347）。

论文的要点：端到端花费 = 提示措辞 × 推理投入 × harness 策略 × 模型 × 任务难度，**任一变量变一下，
结论就可能反过来**（改个措辞就能改变验证行为；换了 harness 之后原来的省 token 手段可能失效）。
所以我们缺的不是"再记一个数字"，而是**把变量一起记下来、能按同口径对比**：

  · 每次运行记一条：scenario / prompt_hash / effort / harness / model + 成本量（步数、模型调用、
    工具调用、估算 token、墙钟耗时）。
  · 对比时先看**变量是否同口径**：prompt_hash 或 harness 变了 → 明说"不可比"，别拿两次不同的
    东西比出个好看的结论（这正是论文说的坑）。
  · 硬指标（步数 / 模型调用 / 工具调用）**要求逐字相等**；软指标（估算 token / 耗时）给比例容差。

纯 stdlib、不调模型、写失败不许影响主流程。
"""
import hashlib
import json
import os
import time

DEFAULT_REL = os.path.join(".taskstate", "cost_ledger.jsonl")
HARD_METRICS = ("steps", "model_calls", "tool_calls")
SOFT_METRICS = ("est_tokens", "wall_ms")
OFF_VALUES = ("0", "off", "false", "no")


def ledger_path(path=None):
    if path:
        return path
    env = os.environ.get("CODEAGENT_COST_LEDGER")
    if env and env.strip().lower() not in ("", "on", "1", "true", "yes"):
        return env.strip()
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, DEFAULT_REL)


def prompt_hash(text):
    """提示措辞的指纹：措辞一改，前后两次就没有可比性（论文的核心变量之一）。"""
    if text is None:
        return ""
    return hashlib.sha1(str(text).encode("utf-8", "replace")).hexdigest()[:12]


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def record(scenario, prompt=None, effort=None, harness=None, model=None,
           steps=0, model_calls=0, tool_calls=0, est_tokens=0, wall_ms=0,
           extra=None, path=None, ts=None):
    """追加一条成本记录。返回 {ok, entry, path}；**写失败也返回 ok=False 而不抛**。"""
    entry = {"ts": ts or int(time.time()), "scenario": str(scenario or ""),
             "prompt_hash": prompt_hash(prompt),
             "effort": effort if isinstance(effort, dict) else (str(effort) if effort else ""),
             "harness": harness if isinstance(harness, (list, str)) else "",
             "model": str(model or ""),
             "steps": int(_num(steps)), "model_calls": int(_num(model_calls)),
             "tool_calls": int(_num(tool_calls)), "est_tokens": int(_num(est_tokens)),
             "wall_ms": int(_num(wall_ms))}
    if isinstance(extra, dict):
        entry["extra"] = extra
    p = ledger_path(path)
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:                           # noqa: BLE001
        # 记账是旁路：**任何**失败都不许影响主流程（实测 os.makedirs 会抛 ValueError 而不是
        # OSError，比如路径里带非法字符）——所以这里兜住所有异常，只如实回报。
        return {"ok": False, "error": "写账本失败: %s" % e, "entry": entry, "path": p}
    return {"ok": True, "error": "", "entry": entry, "path": p}


def load(path=None, scenario=None):
    """读账本。坏行跳过（账本是 append-only 的，宁可少一条也不许整个读挂）。"""
    p = ledger_path(path)
    out = []
    if not os.path.exists(p):
        return out
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and (scenario is None or e.get("scenario") == scenario):
                    out.append(e)
    except OSError:
        return []
    return out


def latest(path=None, scenario=None):
    rows = load(path, scenario=scenario)
    return rows[-1] if rows else None


# 软指标的**绝对噪声下限**：百毫秒量级的测量全被进程噪声（导入/磁盘缓存/调度）主导，
# 只按百分比卡容差会随机翻红（实测：wall_ms 93→112ms 涨 20% 卡线，而硬指标一个没动）。
# 所以软指标要"超容差**且**超绝对下限"才算回归。
SOFT_FLOORS = {"wall_ms": 150, "est_tokens": 200}


def compare(base, cur, soft_tol=0.2, floors=None):
    """比两次运行。**先判变量同不同口径**，再比成本。

    返回 {comparable, why, verdict, hard, soft, regressions[]}：
      comparable=False → 变量变了（提示措辞/harness/努力档/模型），这次别下结论，只能当新基准。
      verdict: same / better / worse
    """
    base, cur = base or {}, cur or {}
    reasons = []
    for k, label in (("prompt_hash", "提示措辞"), ("harness", "编排链"), ("effort", "努力档"),
                     ("model", "模型")):
        b, c = base.get(k), cur.get(k)
        if b != c:
            reasons.append("%s不同（%s → %s）" % (label, b, c))
    comparable = not reasons
    hard, soft, regressions = {}, {}, []
    for k in HARD_METRICS:
        b, c = int(_num(base.get(k))), int(_num(cur.get(k)))
        hard[k] = {"base": b, "cur": c, "delta": c - b, "same": b == c}
        if c != b:
            regressions.append("%s %d → %d（硬指标要求相等）" % (k, b, c))
    fl = dict(SOFT_FLOORS)
    fl.update(floors or {})
    for k in SOFT_METRICS:
        b, c = int(_num(base.get(k))), int(_num(cur.get(k)))
        ratio = (c - b) / b if b else (0.0 if c == 0 else 1.0)
        floor = int(_num(fl.get(k, 0)))
        over = bool(b) and ratio > soft_tol and (c - b) > floor
        soft[k] = {"base": b, "cur": c, "delta": c - b, "ratio": round(ratio, 3),
                   "over": over, "floor": floor}
        if over:
            regressions.append("%s %d → %d（+%.0f%%，超容差 %.0f%% 且超绝对下限 %d）"
                               % (k, b, c, ratio * 100, soft_tol * 100, floor))
    if not comparable:
        verdict = "incomparable"
    elif regressions:
        verdict = "worse"
    elif any(v.get("delta", 0) < 0 for v in list(hard.values()) + list(soft.values())):
        verdict = "better"
    else:
        verdict = "same"
    return {"comparable": comparable, "why": "；".join(reasons), "verdict": verdict,
            "hard": hard, "soft": soft, "regressions": regressions}


def summarize(rows, scenario=None):
    """按场景汇总（每个场景取最近一条），便于人工看账本。"""
    out = {}
    for r in rows:
        s = r.get("scenario")
        if scenario and s != scenario:
            continue
        out[s] = r
    return out
