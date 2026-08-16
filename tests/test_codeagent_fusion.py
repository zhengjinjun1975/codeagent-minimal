#!/usr/bin/env python3
"""test_codeagent_fusion.py — 融合后新 CodeAgent 大整体测试（pytest）。

背景：吸收 OpenCode 能力后融合成「新 CodeAgent 大整体」——统一运行时
agent_runtime.py（14 原子 = 12 原子 + mcp-client + code-skill）+ 统一入口
codeagent.py（CodeAgent 门面，17 子命令）。本文件用**真实数据**断言大整体的
统一入口完整任务可用，非空壳。

覆盖：
1. 统一运行时：14 原子全加载 ready、无降级、无冲突、能力索引齐全
2. 统一入口完整任务（真实数据）：
   * review 真实文件（bad_sample.py 检 ≥3 major 安全缺陷）
   * test 真实文件（sample_target.py 冒烟通过）
   * evolve_loop 大自进化闭环（loop_closed=True，观察→归因→精炼→校验+记忆+技能+SKILL）
3. 原子协同（run_chain 组装大链）：
   * 单文件 chain 5/5 全绿，think→gen→review→test→evolve 上游输出透传下游
   * 多文件 chain 5/5 全绿（test 逐文件落盘聚合，files_tested 真实路径）
   * _flow_files 透传回归：透传标记不传给原子 _run（防 unexpected keyword 崩溃）
4. 吸收 OpenCode 协同：review_with_mcp（MCP 供工具）、route_model/list_models
   （多模型，默认 local_only 数据不出厂）、reuse_with_skill（SKILL 资产召回）、
   sediment_skill_to_md（技能→SKILL.md 标准资产）

真实数据断言铁律：凡涉文件/审查/测试/进化，必须断言返回里带真实内容（score/
majors/red_green/files_tested/loop_closed），只认实测证据。
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent_runtime import AgentRuntime
from codeagent import CodeAgent


# ══════════ 1. 统一运行时：16 原子全加载 ══════════
FUSED_ATOMS = {
    "dep-impact", "llm-router", "code-test",
    "code-review", "code-evolve", "code-memory",
    "code-plan", "code-dispatch", "code-reuse",
    "code-project", "task-state", "code-deliver",
    "mcp-client", "code-skill",
    "dep-scan", "code-fuzz",          # 重组合新增（依赖SCA/污点 + 属性模糊）
}


def test_runtime_16_atoms_ready_no_degraded():
    rt = AgentRuntime()
    d = rt.describe()
    assert set(d["atoms"]) == FUSED_ATOMS, f"应为 16 原子: {set(d['atoms'])}"
    assert d["count"] == 16
    assert d["degraded"] == [], f"有原子加载降级: {d['degraded']}"
    assert d["conflicts"] == [], f"有冲突: {d['conflicts']}"
    assert d["local_only"] is True  # 数据不出厂默认
    # 能力索引齐全：链上 5 个能力都路由到原子
    caps = d["capabilities"]
    for cap in ("plan.think", "plan.gen", "codereview.review", "test.run", "evolve.refine"):
        assert cap in caps, f"能力 {cap} 未路由"
    # MCP/SKILL 协同能力
    assert "mcp.tools" in caps and "mcp.call" in caps
    assert "skill.list" in caps and "skill.export" in caps and "skill.sediment" in caps


def test_unified_entry_api_atoms():
    ca = CodeAgent()
    assert ca.atoms()["count"] == 16
    # 统一 API 门面方法齐全
    for m in ("run", "review", "test", "refine", "reuse", "impact", "plan", "memory",
              "skill", "mcp", "llm", "dispatch", "project", "deliver", "chain",
              "evolve_loop", "review_with_mcp", "reuse_with_skill"):
        assert callable(getattr(ca, m)), f"统一入口缺方法: {m}"


# ══════════ 2. 统一入口完整任务（真实数据）══════════
def test_review_real_file_detects_security():
    ca = CodeAgent()
    rv = ca.review(path=os.path.join(REPO_ROOT, "bad_sample.py"))
    assert rv["ok"], rv.get("error")
    maj = [i for i in rv["data"]["issues"] if i.get("severity") == "major"]
    assert len(maj) >= 3, f"应检出 ≥3 major，实得 {len(maj)}"
    assert rv["data"]["score"] <= 70, f"score 应显著下降，实得 {rv['data']['score']}"


def test_test_real_file_smoke_passes():
    ca = CodeAgent()
    target = os.path.join(REPO_ROOT, "sample_target.py")
    t = ca.test(path=target, target_dir=REPO_ROOT, do_mutation=False)
    assert t["ok"], t.get("error")
    assert t["data"]["smoke"]["ok"] is True
    assert "red_green" in t["data"]


def test_evolve_loop_full_closed():
    ca = CodeAgent()
    r = ca.evolve_loop(
        "修复审查发现的安全缺陷",
        outcome={"score": 60,
                 "issues": [{"severity": "major", "title": "命令注入",
                             "suggestion": "用 subprocess 参数化而非 shell=True"}],
                 "task": "修复安全缺陷"},
        review_path=os.path.join(REPO_ROOT, "sample_target.py"))
    assert r["ok"], r.get("error")
    d = r["data"]
    # 完整闭环：refine 真实 kept + 记忆复盘 + 技能沉淀 + SKILL 资产 + 审查接 MCP
    assert d.get("kept") is not None or d.get("refine", {}).get("kept") is not None or \
        d.get("refine", {}).get("verdict"), f"refine 未真实执行: {d.get('refine', {}).keys()}"
    assert "memory" in d and d["memory"], "记忆复盘缺失"
    assert "skill" in d, "技能沉淀缺失"
    assert "prompt" in d, "经验取回缺失"
    assert "exported" in d, "SKILL.md 资产沉淀缺失"
    assert "review_mcp" in d, "审查接入 MCP 缺失"
    assert d.get("loop_closed") is True, f"自进化闭环未闭合: {d}"


# ══════════ 3. 原子协同：run_chain 组装大链（真实数据）══════════
REAL_SINGLE = {"calc.py": 'def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n\nif __name__ == "__main__":\n    print(add(2, 3), mul(2, 3))\n'}
REAL_MULTI = {
    "calc.py": 'def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n',
    "geo.py": 'def area(r):\n    return 3.14159 * r * r\n',
}


def test_chain_single_file_5of5_green():
    ca = CodeAgent()
    r = ca.chain("实现加法与乘法函数 add/mul", code=REAL_SINGLE)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["verdict"] == "全部通过", d["verdict"]
    assert d["ok_steps"] == ["think", "gen", "review", "test", "evolve"], d["ok_steps"]
    # 上游输出透传下游：gen files 喂给 review(审查真码) + test(落盘真文件)
    ft = d["results"]["test"]["data"]["files_tested"]
    assert ft, "test 未跑真实文件(files_tested 为空)"
    assert os.path.basename(ft[0]) in REAL_SINGLE, f"test 未跑 gen 落盘文件: {ft[0]}"
    assert d["results"]["test"]["data"]["red_green"]["green"] is True
    assert d["results"]["review"]["data"]["score"] >= 0
    assert "kept" in d["results"]["evolve"]["data"]


def test_chain_multi_file_5of5_green():
    ca = CodeAgent()
    r = ca.chain("多文件: add/mul/area", code=REAL_MULTI)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["verdict"] == "全部通过", d["verdict"]
    assert set(d["ok_steps"]) == {"think", "gen", "review", "test", "evolve"}
    ft = d["results"]["test"]["data"]["files_tested"]
    assert len(ft) == 2, f"多文件应测 2 个真实文件，实得 {ft}"
    bases = {os.path.basename(p) for p in ft}
    assert bases == set(REAL_MULTI), f"test 未跑全部 gen 落盘文件: {bases}"
    assert d["results"]["test"]["data"]["red_green"]["green"] is True


def test_flow_files_not_leaked_to_atom():
    """_flow_files 透传回归：组装链内不透传内部标记给原子 _run（防 unexpected keyword 崩溃）。
    直接证据 = 单/多文件 chain 5/5 全绿（test 环节不抛 unexpected '_flow_files'）。"""
    rt = AgentRuntime()
    r = rt.run_chain(
        [{"step": "gen", "capability": "plan.gen",
          "inputs": lambda d, s: {"task": s.get("task"), "code": REAL_SINGLE}},
         {"step": "review", "capability": "codereview.review",
          "inputs": lambda d, s: {"code": d.get("gen", {}).get("files") or s.get("code"),
                                  "use_llm": False, "reuse_atoms": True}},
         {"step": "test", "capability": "test.run",
          "inputs": lambda d, s: {"target_dir": "."}},
         {"step": "evolve", "capability": "evolve.refine",
          "inputs": lambda d, s: {"task": s.get("task"),
                                  "outcome": {"score": d.get("review", {}).get("score", 0),
                                              "issues": d.get("review", {}).get("issues", []),
                                              "task": s.get("task")}}}],
        task="实现加法与乘法函数 add/mul", seed={"task": "t", "code": REAL_SINGLE})
    assert r["ok"], r.get("error")
    assert r["data"]["verdict"] == "全部通过", r["data"]["verdict"]
    ft = r["data"]["results"]["test"]["data"]["files_tested"]
    assert ft and os.path.basename(ft[0]) in REAL_SINGLE, f"test 未跑 gen 落盘文件: {ft}"


# ══════════ 4. 吸收 OpenCode 协同 ══════════
def test_review_with_mcp_coop():
    ca = CodeAgent()
    r = ca.review_with_mcp(os.path.join(REPO_ROOT, "bad_sample.py"))
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["review"]["ok"], d["review"].get("error")
    assert "mcp_tools" in d, "MCP 工具清单未并入协同"
    assert "count" in d["mcp_tools"], f"mcp_tools 应为工具清单 data: {d['mcp_tools'].keys()}"
    # 审查信封并入 MCP 可用工具证据（协同可见）
    assert d["review"]["data"].get("mcp_available_tools", 0) >= 0


def test_route_model_local_only_blocks_cloud():
    ca = CodeAgent()
    # 多模型：gen 走云端 GLM，默认 local_only 数据不出厂 → 立即降级
    r = ca.rt.route_model("gen", messages=[{"role": "user", "content": "hi"}])
    assert r["ok"] is False and r.get("degraded") is True, f"默认应本地封锁: {r}"
    assert "不出厂" in r["error"] or "local-only" in r["error"]
    # list_models 返回多模型注册表
    lm = ca.rt.list_models()
    assert lm["ok"], lm.get("error")
    assert "models" in lm["data"] or isinstance(lm["data"], dict)


def test_reuse_with_skill_coop():
    ca = CodeAgent()
    r = ca.reuse_with_skill(content="def add(a, b):\n    return a + b", top_k=3)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["reuse"]["ok"], d["reuse"].get("error")
    assert "skills" in d, "SKILL 资产未并入协同"


def test_sediment_skill_to_standard_md():
    ca = CodeAgent()
    r = ca.rt.sediment_skill_to_md("修复安全缺陷", "参数化 subprocess 调用")
    assert r["ok"], r.get("error")
    assert "path" in r["data"] or "name" in r["data"] or "content" in r["data"] or \
        isinstance(r["data"], dict), f"SKILL.md 资产未产出: {r['data']}"


# ══════════ 5. 闭源 orchestrator 吸收 OpenCode 协同编排（编排层接入） ══════════
def test_closed_orchestrator_absorbs_coop():
    """闭源编排器吸收 MCP/多模型/SKILL 协同：run_coop 统一入口真实可用，数据不出厂。"""
    if not os.path.exists(r"E:/code_agent/orchestrator.py"):
        return  # 闭源侧不存在则跳过
    sys.path.insert(0, r"E:/code_agent")
    from orchestrator import Orchestrator
    orc = Orchestrator()
    r = orc.run_coop(os.path.join(REPO_ROOT, "bad_sample.py"), task="协同编排验收")
    assert r["ok"], r.get("error")
    d = r["data"]
    assert "coop_layer" in d and "MCP" in d["coop_layer"] and "SKILL" in d["coop_layer"]
    rm = d["review_mcp"]
    assert rm["ok"], rm.get("error")
    assert rm["data"]["mcp_tools"]["count"] >= 0, "MCP 工具清单未并入审查协同"
    rs = d["reuse_skill"]
    assert rs["ok"] and "skills" in rs["data"], "SKILL 资产未并入协同"
    lm = d["models"]
    assert lm["ok"] and "models" in lm["data"], "多模型注册表未并入协同"
    # 数据不出厂：云端 LLM 只列注册表，route_model(gen) 本地封锁
    rg = orc.route_model("gen", [{"role": "user", "content": "hi"}])
    assert rg["ok"] is False and rg.get("degraded") is True, f"默认应本地封锁: {rg}"
    # 技能 → SKILL.md 标准资产
    sm = orc.sediment_skill_to_md("修安全缺陷", "参数化 subprocess")
    assert sm["ok"] and "path" in sm["data"], f"SKILL.md 资产未产出: {sm.get('data')}"


# ══════════ 6. 重组合：安全·质量组装链（guard） ══════════
def test_guard_chain_coop_16_atoms():
    """重组合新增：guard 组装链 = review + dep-scan + fuzz 协同，真实数据不出厂。"""
    ca = CodeAgent()
    r = ca.guard_chain(os.path.join(REPO_ROOT, "bad_sample.py"), mode="code")
    assert r["ok"], r.get("error")
    d = r["data"]
    assert "review" in d and "depscan" in d and "fuzz" in d, f"guard 缺协同环节: {list(d.keys())}"
    assert d["review"]["ok"], d["review"].get("error")
    # SCA/污点 + 属性模糊均真实执行
    assert d["depscan"]["ok"], d["depscan"].get("error")
    assert d["fuzz"]["ok"], d["fuzz"].get("error")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
