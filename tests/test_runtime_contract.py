#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行时加固的自测：错误要能分类，编排错误要停链，模板占位符要真能解析。

为什么单独测这一层：静态图谱只能核到源码里字面量写的能力名（本仓 45 处调用点里
33 处可核、12 处只能声明边界）。剩下那一截靠运行时兜底 —— 而兜底如果只是
"再来一个 degraded:true"，等于没有。所以这里逐条钉死：
  ① 能力名不存在 → CAP_UNKNOWN，并给出近似候选
  ② 参数接不住 / 缺必填 → CAP_CONTRACT，degraded=False（改代码才能好，不是业务降级）
  ③ 原子内部抛异常 → ATOM_FAIL / ATOM_TYPE，仍照旧降级
  ④ 链上遇到①② → **当场停机**，不许级联降级把根因冲掉
  ⑤ 模板占位符 $task / $seed.x / $step.name.field 必须真能解析（组合收口的前提）
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import atomic_base  # noqa: E402
from atomic_base import AtomicAgent  # noqa: E402
from agent_runtime import AgentRuntime, _resolve_step_inputs  # noqa: E402


@pytest.fixture(scope="module")
def rt():
    return AgentRuntime()


# ────────────────────────── 错误分类 ──────────────────────────
def test_unknown_capability_has_code_and_candidates(rt):
    r = rt.run_capability("plan.thnik", task="x")
    assert r["ok"] is False
    assert r.get("code") == atomic_base.ERR_UNKNOWN
    assert r["data"].get("near"), "打错能力名却没给近似候选，等于让人自己翻 166 条"
    assert "plan.think" in r["data"]["near"]


def test_wrong_kwarg_is_contract_not_degraded(rt):
    r = rt.run_capability("plan.think", task="x", bogus_key=1)
    assert r["ok"] is False
    assert r.get("code") == atomic_base.ERR_CONTRACT
    assert r.get("degraded") is False, "编排传参错误不该被当成业务降级"
    assert r["data"]["unexpected"] == ["bogus_key"]
    assert "task" in r["data"]["accepted"]


def test_missing_required_is_contract(rt):
    r = rt.run_capability("plan.think")
    assert r.get("code") == atomic_base.ERR_CONTRACT
    assert r["data"]["missing"] == ["task"]


def test_atom_internal_typeerror_is_not_contract():
    """原子内部自己抛的 TypeError 不能算成"参数没传对"，否则会掩盖真 bug。"""
    class Buggy(AtomicAgent):
        name = "buggy"
        version = "0.0.1"
        domain = "buggy"
        provides = ["buggy.boom"]

        def _boom(self, x=1):
            return 1 + "x"           # TypeError 出现在函数体内

        def _register_defaults(self):
            self.register("buggy.boom", self._boom)

    a = Buggy().load()
    r = a.call("buggy.boom")
    assert r["ok"] is False
    assert r.get("code") == atomic_base.ERR_ATOM_TYPE
    assert r.get("degraded") is True


def test_atom_internal_error_is_atom_fail():
    class Fail(AtomicAgent):
        name = "fail"
        version = "0.0.1"
        domain = "fail"
        provides = ["fail.go"]

        def _go(self):
            raise ValueError("业务失败")

        def _register_defaults(self):
            self.register("fail.go", self._go)

    r = Fail().load().call("fail.go")
    assert r["ok"] is False
    assert r.get("code") == atomic_base.ERR_ATOM_FAIL
    assert r.get("degraded") is True


def test_not_ready_has_code():
    class Slow(AtomicAgent):
        name = "slow"
        version = "0.0.1"
        domain = "slow"
        provides = ["slow.go"]

        def _go(self):
            return {}

        def _register_defaults(self):
            self.register("slow.go", self._go)

    a = Slow()                       # 故意不 load()
    r = a.call("slow.go")
    assert r.get("code") == atomic_base.ERR_NOT_READY


def test_kwargs_capability_skips_precheck():
    """接受 **kwargs 的能力不做参数预检（拦了会误伤），但要能被识别出来。"""
    class Wide(AtomicAgent):
        name = "wide"
        version = "0.0.1"
        domain = "wide"
        provides = ["wide.go"]

        def _go(self, **kw):
            return {"got": sorted(kw)}

        def _register_defaults(self):
            self.register("wide.go", self._go)

    a = Wide().load()
    assert a.accepts("wide.go")["has_kwargs"] is True
    r = a.call("wide.go", anything=1)
    assert r["ok"] is True and r["data"]["got"] == ["anything"]


def test_accepts_reports_real_signature(rt):
    acc = rt.agents[rt.capability_atom("plan.think")].accepts("plan.think")
    assert acc["available"] is True
    assert "task" in acc["params"]


# ────────────────────────── 链上停机（不级联） ──────────────────────────
def test_chain_halts_on_contract_error(rt):
    chain = [
        {"step": "ok", "capability": "plan.think", "inputs": {"task": "$task"}},
        {"step": "bad", "capability": "plan.thnik", "inputs": {}},
        {"step": "never", "capability": "test.select",
         "inputs": {"project_root": ".", "run": False}},
    ]
    r = rt.run_chain(chain, task="停机测试")
    assert r["ok"] is False
    assert r.get("code") == atomic_base.ERR_UNKNOWN
    assert r["data"]["halted_at"]["step"] == "bad"
    assert "never" not in r["data"]["results"], "契约错误后还在往下跑，根因会被级联失败冲掉"
    assert r["data"]["verdict"].startswith("契约错误停机")


def test_chain_bad_kwarg_halts_too(rt):
    chain = [{"step": "bad", "capability": "plan.think", "inputs": {"task": "x", "nope": 1}}]
    r = rt.run_chain(chain, task="x")
    assert r.get("code") == atomic_base.ERR_CONTRACT
    assert r["data"]["halted_at"]["capability"] == "plan.think"


# ────────────────────────── 模板占位符 ──────────────────────────
def test_placeholders_resolve():
    data = {"gen": {"files": {"a.py": "x"}}, "review": {"score": 9, "issues": ["i"]}}
    spec = {"task": "$task", "code": "$upstream_files", "language": "$seed.language",
            "outcome": {"score": "$step.review.score", "issues": "$step.review.issues",
                        "task": "$task"},
            "literal": 1}
    out = _resolve_step_inputs(spec, data, "做点事", {"language": "python", "code": None})
    assert out["task"] == "做点事"
    assert out["code"] == {"a.py": "x"}
    assert out["language"] == "python"
    assert out["outcome"] == {"score": 9, "issues": ["i"], "task": "做点事"}
    assert out["literal"] == 1


def test_unknown_placeholder_passes_through_literally():
    """认不出的形态原样传（由 G11 在提交时报），运行时**不猜**。"""
    out = _resolve_step_inputs({"x": "$nope"}, {}, "t", {})
    assert out["x"] == "$nope"


def test_missing_upstream_is_none_not_fake():
    out = _resolve_step_inputs({"code": "$upstream_files"}, {}, "t", {})
    assert out["code"] is None


# ────────────────────────── 组合只有一个源 ──────────────────────────
def test_default_chain_comes_from_template(rt):
    chain, err = rt.default_chain()
    assert chain, err
    assert [(c.get("step"), c["capability"]) for c in chain] == [
        ("think", "plan.think"), ("gen", "plan.gen"), ("review", "codereview.review"),
        ("test", "test.run"), ("evolve", "evolve.refine")]


def test_default_chain_by_name(rt):
    chain, err = rt.default_chain("改缺陷/修回归")
    assert chain, err
    caps = [c if isinstance(c, str) else c["capability"] for c in chain]
    assert caps == ["plan.think", "code.implement", "test.run", "codereview.review"]


def test_default_chain_unknown_name_fails_loudly(rt):
    chain, err = rt.default_chain("不存在的组合")
    assert chain is None and "没有名为" in err


def test_auto_select_reaches_template(rt):
    sel = rt.select_chain("修复一个报错")
    assert sel["ok"], sel.get("error")
    assert sel["data"]["template_name"] == "改缺陷/修回归"
