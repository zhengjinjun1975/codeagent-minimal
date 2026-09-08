#!/usr/bin/env python3
"""test_capability_contract.py — 3 个遗留契约修复的真实数据断言（pytest）。

覆盖自审遗留 FAIL（P2）的 3 个 capability 契约：
1. dispatch.template — data 应含 `template` 字段（对齐 outputs），非裸字符串
2. plan.think      — data 应含 `plan` 字段（对齐 outputs），非顶层中文段落
3. task-state.track — 裸调用默认 action="new"（对齐 CLI 与 track 语义），
                        data["action"] 与所执行 action 一致

契约约定：原子 `run()` 返回 {ok, data} 信封；data 为 dict，字段对齐该原子
outputs 声明。全部断言用真实调用 + 真实返回结构验证。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agent_loader


def _agents():
    r = agent_loader.load_agents()
    assert r["ok"], f"registry 加载失败: {r.get('error')}"
    return r["data"]["agents"]


# ══════════ 1. dispatch.template：data["template"] 契约 ══════════
def test_dispatch_template_contract():
    agents = _agents()
    r = agents["code-dispatch"].run(_capability="dispatch.template",
                                    background="bg", goal="goal", constraint="c",
                                    redline="r", deliverable="d")
    assert r["ok"], r.get("error")
    d = r["data"]
    # 契约：data 为 dict 且含 template 字段（对齐 outputs），而非裸字符串
    assert isinstance(d, dict), f"dispatch.template data 应为 dict，实得 {type(d)}"
    assert "template" in d, f"data 缺 template 字段: keys={list(d.keys())}"
    tpl = d["template"]
    assert isinstance(tpl, str) and "## 背景" in tpl and "## 产出" in tpl
    # 传入的背景/目标确实注入模板
    assert "bg" in tpl and "goal" in tpl and "d" in tpl


# ══════════ 2. plan.think：data["plan"] 契约 ══════════
def test_plan_think_contract():
    agents = _agents()
    r = agents["code-plan"].run(_capability="plan.think",
                                task="实现 add(a,b)", language="python")
    assert r["ok"], r.get("error")
    d = r["data"]
    # 契约：data 含 plan 键（对齐 outputs），plan 为 5 段方案 dict
    assert isinstance(d, dict), f"plan.think data 应为 dict，实得 {type(d)}"
    assert "plan" in d, f"data 缺 plan 键: keys={list(d.keys())}"
    plan = d["plan"]
    assert isinstance(plan, dict), f"data['plan'] 应为 dict，实得 {type(plan)}"
    # 5 段：背景/目标/约束/红线/产出
    for seg in ("背景", "目标", "约束", "红线", "产出"):
        assert seg in plan, f"plan 缺段落 {seg}: keys={list(plan.keys())}"
    # 元数据字段对齐 outputs
    for k in ("assumptions", "files_needed", "constraint_chain", "questions"):
        assert k in d, f"data 缺元数据字段 {k}: keys={list(d.keys())}"
    assert d["plan"]["files_needed"] == d["files_needed"]


# ══════════ 3. task-state.track：默认 action="new" 且 data["action"] 一致 ══════════
def test_taskstate_track_contract_new_default():
    agents = _agents()
    # 裸调用（不传 action）→ 契约默认 new，与 CLI(--action new)/track 语义一致
    r = agents["task-state"].run(_capability="taskstate.track",
                                 task="契约测试任务-契约")
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["action"] == "new", f"裸调用默认 action 应为 new，实得 {d['action']}"
    assert d["status"] == "tracked", d.get("status")
    assert d["task_id"] and d["file"]
    assert os.path.exists(d["file"]), f"状态文件未真实落盘: {d['file']}"


def test_taskstate_track_contract_explicit_action():
    agents = _agents()
    # 显式 action=set → data["action"] 与所执行 action 一致
    r = agents["task-state"].run(_capability="taskstate.track",
                                 task="契约测试任务-set",
                                 action="set", state="running", progress="40%")
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["action"] == "set", f"data['action'] 应等于所执行 action=set，实得 {d['action']}"
    assert d["status"] == "tracked"
    assert d["progress"] == "40%"
