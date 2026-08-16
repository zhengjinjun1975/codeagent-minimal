#!/usr/bin/env python3
"""test_codeagent_atomic_p1.py — CodeAgent 原子化重构 P1 测试（pytest）。

覆盖：
1. 12 原子 load（registry 全加载，status=ready）
2. 每个原子 run 真实数据（12 原子真实执行）
3. 组装链验收（think→gen→review→test→evolve，真实代码任务，test 跑真实文件）
4. assembler（闭源，在 E:/code_agent）按需组装 DAG + 冲突检测
5. 存量迁移兼容（legacy_cli 经原子调用）

双绿目标：本文件绿 + 现有 test_review.py + P0 test 不破坏。
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agent_loader


# ══════════ 1. 14 原子全加载（融合后新 CodeAgent 大整体：12 原子 + MCP + SKILL） ══════════
EXPECTED_ATOMS = {
    "dep-impact", "llm-router", "code-test",          # P0
    "code-review", "code-evolve", "code-memory",      # P1
    "code-plan", "code-dispatch", "code-reuse",       # P1
    "code-project", "task-state", "code-deliver",     # P1
    "mcp-client", "code-skill",                       # 融合新增（吸收 OpenCode）
}

def _agents():
    r = agent_loader.load_agents()
    assert r["ok"], f"registry 加载失败: {r.get('error')}"
    assert r["data"]["degraded"] == [], f"有原子加载降级: {r['data']['degraded']}"
    return r["data"]["agents"]


def test_fourteen_atoms_load_ready():
    agents = _agents()
    assert set(agents) == EXPECTED_ATOMS, f"原子集合不符(应为 14): {set(agents)}"
    assert len(agents) == 14, f"融合后应为 14 原子，实得 {len(agents)}"
    for name, a in agents.items():
        assert a.status == "ready", f"{name} 状态非 ready: {a.status}"


# ══════════ 2. 每个原子 run 真实数据 ══════════
def test_each_atom_runs_real_data():
    agents = _agents()
    target = os.path.join(REPO_ROOT, "sample_target.py")
    src = open(target, encoding="utf-8").read()

    runs = [
        # (原子, 能力, kwargs)
        ("code-review", "codereview.review", {"path": target, "use_llm": False}),
        ("code-evolve", "evolve.refine", {"task": "add", "outcome": {"score": 80, "issues": []}, "auto_sediment": False}),
        ("code-memory", "memory.save", {"findings": [{"severity": "info", "title": "t", "suggestion": "补边界值"}], "task": "add"}),
        ("code-plan", "plan.think", {"task": "实现 add", "language": "python"}),
        ("code-plan", "plan.gen", {"task": "add", "spec": {"add": "1+1"}}),
        ("code-dispatch", "dispatch.template", {}),
        ("code-dispatch", "dispatch.budget", {"task": "add", "files_needed": 1}),
        ("code-dispatch", "dispatch.conflict", {"tasks": [{"name": "A", "files": ["x.py"]}, {"name": "B", "files": ["x.py"]}]}),
        ("code-reuse", "reuse.atom", {}),
        ("code-project", "project.load", {"path": REPO_ROOT}),
        ("code-project", "project.analyze", {"path": REPO_ROOT}),
        ("task-state", "taskstate.track", {"task": "test", "action": "set", "progress": "ok"}),
        ("code-deliver", "deliver.report", {"chain": ["think"], "outputs": {"think": {"ok": True, "data": {"summary": "s"}}}}),
        ("dep-impact", "impact.analyze", {"path": REPO_ROOT, "impact": "dep_report", "transitive": True}),
        ("code-test", "test.gen", {"code": {"sample_target.py": src}}),
        # 融合新增（吸收 OpenCode）
        ("mcp-client", "mcp.tools", {"server": "demo", "local_only": True}),
        ("code-skill", "skill.list", {}),
        ("code-skill", "skill.export", {"name": "test-skill", "description": "d",
                                        "content": "# t\n1. 实测"}),
    ]
    for atom, cap, kw in runs:
        a = agents[atom]
        r = a.run(_capability=cap, **kw)
        assert r["ok"], f"{atom}.{cap} 失败: {r.get('error')} / {json.dumps(r.get('data',{}), default=str)[:200]}"


def test_code_test_real_run():
    agents = _agents()
    target = os.path.join(REPO_ROOT, "sample_target.py")
    r = agents["code-test"].run(_capability="test.run", path=target,
                                target_dir=REPO_ROOT, do_mutation=False)
    assert r["ok"] and r["data"]["smoke"]["ok"]
    assert r["data"]["smoke"]["ok"] is True  # 冒烟通过; 边界红属预期信号


# ══════════ 3. 组装链验收（闭源 orchestrator） ══════════
def test_assembly_chain_real_run():
    if not os.path.exists(r"E:/code_agent/orchestrator.py"):
        return  # 闭源侧不存在则跳过（P1 验收仍可独立跑）
    sys.path.insert(0, r"E:/code_agent")
    from orchestrator import Orchestrator
    orc = Orchestrator()
    real_code = {"calc.py": 'def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n\nif __name__ == "__main__":\n    print(add(2, 3), mul(2, 3))\n'}
    r = orc.run_chain("实现加法与乘法函数 add/mul", code=real_code)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["verdict"] == "全部通过", d["verdict"]
    assert d["results"]["test"]["data"]["red_green"]["green"]
    # P2-11: run_chain 内临时工作区用 TemporaryDirectory，run 结束 finally 已 cleanup，
    # 因此对 runtime.test_path 做 os.path.exists 必为 False（与 verify_chain 对齐修正）。
    # 改断言 test.files_tested —— test 逐个落盘并真实执行的文件路径，证明「test 跑真实文件」。
    ft = d["results"]["test"]["data"].get("files_tested", [])
    assert ft, "test 未跑真实文件(files_tested 为空)"
    assert os.path.basename(ft[0]) in real_code, f"test 未跑 gen 落盘文件: {ft[0]}"
    assert "kept" in d["results"]["evolve"]["data"]


# ══════════ 4. assembler（闭源）按需组装 + 冲突检测 ══════════
def test_assembler_dag_and_conflicts():
    if not os.path.exists(r"E:/code_agent/assembler.py"):
        return
    sys.path.insert(0, r"E:/code_agent")
    import assembler
    r = assembler.assemble(["plan.think", "plan.gen", "codereview.review", "test.run", "evolve.refine"])
    assert r["ok"]
    d = r["data"]
    assert d["independent"] is True, d["conflicts"]
    assert "code-plan" in d["order"] and "code-review" in d["order"]
    # 冲突检测应安全（14 原子无重复能力/缺依赖）
    c = assembler.detect_conflicts()
    assert c["ok"] and c["data"]["safe"] is True, c["data"]["conflicts"]
    assert len(c["data"]["atoms"]) == 14


# ══════════ 5. 存量迁移兼容 ══════════
def test_legacy_cli_routes_through_atoms():
    target = os.path.join(REPO_ROOT, "sample_target.py")
    sys.path.insert(0, REPO_ROOT)
    import legacy_cli
    # 直接调用内部路由（不经 argparse）
    agents = legacy_cli._load()
    r = legacy_cli._call(agents, "code-review", "codereview.review", path=target)
    assert r["ok"] and "score" in r["data"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
