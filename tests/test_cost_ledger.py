#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cost_ledger.py — 成本回归账本自检（P2，arXiv 2608.01347）。

要证的四件事：① 记录能落盘、能按场景取最近一条；② 坏行不炸（账本是 append-only）；
③ 硬指标变了必须报 worse（步数/模型调用/工具调用）；④ **变量变了必须报"不可比"**——
论文的坑就是拿两次不同口径的运行硬比，比出个假的好结论。
"""
import atexit
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
sys.path.insert(0, LAB)

import cost_ledger as cl                                                  # noqa: E402


def _tmpfile():
    d = tempfile.mkdtemp(prefix="外部编排层-cost-")
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return os.path.join(d, "ledger.jsonl")


def test_record_and_latest():
    p = _tmpfile()
    r = cl.record("s1", prompt="任务甲", effort={"max_steps": 8}, harness=["a.b"],
                  steps=2, model_calls=0, tool_calls=2, est_tokens=30, wall_ms=5, path=p)
    assert r["ok"] and os.path.exists(p)
    cl.record("s1", prompt="任务甲", effort={"max_steps": 8}, harness=["a.b"], steps=3, path=p)
    cl.record("s2", prompt="任务乙", path=p)
    assert cl.latest(p, scenario="s1")["steps"] == 3
    assert len(cl.load(p)) == 3 and len(cl.load(p, scenario="s2")) == 1


def test_bad_lines_are_skipped_not_fatal():
    p = _tmpfile()
    cl.record("s1", prompt="x", path=p)
    with open(p, "a", encoding="utf-8") as f:
        f.write("{坏行\n\n")
    cl.record("s1", prompt="x", path=p)
    assert len(cl.load(p, scenario="s1")) == 2


def test_hard_metric_change_is_a_regression():
    p = _tmpfile()
    base = cl.record("s", prompt="同一句", effort={"m": 1}, harness=["a"], steps=2, path=p)["entry"]
    worse = cl.record("s", prompt="同一句", effort={"m": 1}, harness=["a"], steps=5, path=p)["entry"]
    r = cl.compare(base, worse)
    assert r["comparable"] and r["verdict"] == "worse"
    assert any("steps 2 → 5" in x for x in r["regressions"])


def test_soft_metric_needs_to_clear_absolute_noise_floor():
    """软指标不能只按百分比卡：百毫秒量级的抖动必须被噪声下限挡掉，否则门会随机翻红。"""
    base = {"scenario": "s", "prompt_hash": "p", "harness": ["a"], "effort": {}, "model": "",
            "steps": 2, "model_calls": 0, "tool_calls": 2, "est_tokens": 20, "wall_ms": 93}
    near = dict(base, wall_ms=112)          # +20% 但只多 19ms → 噪声，不算回归
    far = dict(base, wall_ms=900)           # 涨到约 9 倍、多 807ms → 真回归
    assert cl.compare(base, near)["verdict"] == "same", cl.compare(base, near)
    assert cl.compare(base, far)["verdict"] == "worse", cl.compare(base, far)
    assert any("绝对下限" in x for x in cl.compare(base, far)["regressions"])


def test_changed_variable_means_incomparable():
    base = {"scenario": "s", "prompt_hash": cl.prompt_hash("原话"), "harness": ["a"],
            "effort": {"m": 1}, "model": "", "steps": 2, "model_calls": 0, "tool_calls": 2,
            "est_tokens": 10, "wall_ms": 3}
    for key, val in (("prompt_hash", cl.prompt_hash("换了个说法")), ("harness", ["a", "b"]),
                     ("effort", {"m": 2}), ("model", "deepseek")):
        cur = dict(base)
        cur[key] = val
        r = cl.compare(base, cur)
        assert r["comparable"] is False, key
        assert r["verdict"] == "incomparable", key
        assert r["why"], key
    assert cl.compare(base, dict(base))["comparable"] is True


def test_soft_metric_tolerance():
    base = {"prompt_hash": "h", "harness": ["a"], "effort": {}, "model": "",
            "steps": 1, "model_calls": 0, "tool_calls": 1, "est_tokens": 100, "wall_ms": 100}
    cur = dict(base)
    cur["est_tokens"] = 115                       # +15% 在 20% 容差内
    assert cl.compare(base, cur)["verdict"] == "same"
    cur["est_tokens"] = 160                       # +60% 超容差，但只多 60 token：仍被绝对下限挡掉
    assert cl.compare(base, cur)["verdict"] == "same", cl.compare(base, cur)
    cur["est_tokens"] = 400                       # +300% 且多 300 token（>下限 200）→ 真回归
    r = cl.compare(base, cur)
    assert r["verdict"] == "worse" and any("est_tokens" in x for x in r["regressions"]), r


def test_write_failure_is_reported_not_raised():
    """写不进去也不许抛——记账失败不能影响主流程。

    两种真实故障都覆盖：目标是个目录（OSError 系）、路径里带非法字符（ValueError 系）。
    """
    d = os.path.dirname(_tmpfile())
    r = cl.record("s", prompt="x", path=d)                    # 目标是个目录
    assert r["ok"] is False and r["error"]
    r2 = cl.record("s", prompt="x", path=os.path.join("\0bad", "ledger.jsonl"))
    assert r2["ok"] is False and r2["error"]
