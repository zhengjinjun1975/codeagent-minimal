#!/usr/bin/env python3
"""test_arch_review_atom.py — 架构原子 arch-review（pytest，真实代码验证）。

覆盖：
- 原子契约：registry 加载 ready，provides 齐全
- archreview.layers 分层审查（by_layer/violations 结构 + 真实项目）
- archreview.boundary 边界审查
- archreview.surface 攻击面清单
- archreview.intent 设计意图比对（声明 vs 实现）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agent_loader
import arch_review as ar


def _atom():
    r = agent_loader.load_agents()
    assert r["ok"]
    a = r["data"]["agents"]["arch-review"]
    assert a.status == "ready"
    return a


# ── 原子契约 ──
def test_archreview_atom_contract():
    a = _atom()
    assert a.version == "0.1.0"
    for cap in ("archreview.layers", "archreview.boundary",
                "archreview.surface", "archreview.intent"):
        assert a.capabilities()[cap]["callable"], f"{cap} 应可调用"


# ── 分层审查 ──
def test_layered_analysis_structure():
    a = _atom()
    r = a.run(_capability="archreview.layers", path=REPO_ROOT)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["files"] > 0
    assert "by_layer" in d and "layers" in d and "violations" in d
    assert d["summary"]
    # 依赖边结构完整
    for e in d["edges"]:
        for k in ("from", "to", "from_layer", "to_layer", "ok"):
            assert k in e, f"边缺 {k}"


def test_layered_direction_violation_detected():
    # 构造：service 层被 api 层依赖（向下合法），但 service 依赖更深的未分层才违规。
    # 用临时目录模拟分层：api/ + service/，api 依赖 service 合法，service 依赖 api 违规
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "api"))
        os.makedirs(os.path.join(td, "service"))
        with open(os.path.join(td, "api", "handler.py"), "w", encoding="utf-8") as f:
            f.write("from service.logic import x\n")
        with open(os.path.join(td, "service", "logic.py"), "w", encoding="utf-8") as f:
            f.write("from api.handler import y\n")  # 向上依赖违规
        r = ar.layered_analysis(td)
        assert r["violation_count"] >= 1, f"应检出依赖方向违规: {r['summary']}"
        assert any("向上依赖" in v["reason"] for v in r["violations"])


# ── 边界审查 ──
def test_boundary_analysis():
    a = _atom()
    r = a.run(_capability="archreview.boundary", path=REPO_ROOT)
    assert r["ok"]
    d = r["data"]
    assert d["files"] > 0
    assert "boundaries" in d and "entry_total" in d
    assert d["summary"]


# ── 攻击面清单 ──
def test_attack_surface_inventory():
    a = _atom()
    r = a.run(_capability="archreview.surface", path=REPO_ROOT)
    assert r["ok"]
    d = r["data"]
    assert d["files"] > 0
    assert "inventory" in d and d["total"] > 0, "应盘点出攻击面入口"
    assert "summary" in d


# ── 设计意图比对（声明 vs 实现）──
def test_design_intent_compare():
    a = _atom()
    r = a.run(_capability="archreview.intent", path=REPO_ROOT)
    assert r["ok"]
    d = r["data"]
    assert "declared_count" in d and "implemented_count" in d
    assert "missing" in d and "summary" in d


def test_design_intent_identifies_implemented_symbols():
    # 代码中定义的函数应在 implemented 集
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "mod.py"), "w", encoding="utf-8") as f:
            f.write("def compute_sum(a, b):\n    return a + b\n")
        with open(os.path.join(td, "README.md"), "w", encoding="utf-8") as f:
            f.write("# 项目\n- 提供 compute_sum 能力\n")
        r = ar.design_intent_compare(td)
        assert "compute_sum" in r["implemented"]
        assert r["implemented_count"] >= 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
