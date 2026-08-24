#!/usr/bin/env python3
"""test_codeagent_p0p2_new.py — 代码能力 P0-P2 新增原子测试（pytest）。

覆盖新增 6 原子（27→33）：
  P0  极简风格审查     minimalist-style: 纯标准库/不过度依赖/不炫技/可独立部署
  P0  覆盖你项目        atomicity-audit: manifest/registry/断链
  P0  本体审查          ontology-review: factory-ontology 链路 + 数据质量
  P1  本地化            localized: 数据不出厂 + 模型降级链本地主 + 本地路由
  P2  领域代码审查      domain-review: 跨仓库import依赖拓扑 + 工业阀门领域规则

真实数据铁律：全部用真实文件/真实配置/真实规则判定，非空壳。
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agent_loader

NEW_ATOMS = ["minimalist-style", "atomicity-audit", "ontology-review",
             "localized", "domain-review"]


def _agents():
    r = agent_loader.load_agents()
    assert r["ok"], r.get("error")
    return r["data"]["agents"]


def test_new_atoms_registered_and_ready():
    agents = _agents()
    for name in NEW_ATOMS:
        assert name in agents, f"新原子 {name} 未注册"
        assert agents[name].status == "ready", f"{name} 非 ready"
    assert len(agents) == 37, f"应为 37 原子（32 基础 + 4 工具原子 + git-ops），实得 {len(agents)}"


# ══════════ P0 极简风格审查 ══════════
def test_minimalist_style_pure_stdlib():
    a = _agents()["minimalist-style"]
    r = a.run(_capability="minimal.deps", code="import os\nimport sys\nimport json\nimport pathlib")
    assert r["ok"], r.get("error")
    assert r["data"]["third_party"] == []
    assert r["data"]["independent_deployable"] is True
    r2 = a.run(_capability="minimal.independent", code="import os\nimport json")
    assert r2["ok"] and r2["data"]["independent_deployable"] is True


def test_minimalist_style_flags_third_party_and_abs_path():
    a = _agents()["minimalist-style"]
    # 第三方依赖 + 硬编码绝对路径 → 非极简
    r = a.run(_capability="minimal.style",
              code='import requests\nimport numpy as np\n\ndef f():\n    p = "E:/data/x.csv"\n    return p')
    assert r["ok"], r.get("error")
    d = r["data"]
    assert "requests" in d["third_party"] or "numpy" in d["third_party"], "未检出第三方依赖"
    assert d["score"] < 100, "非极简代码不应满分"
    assert d["verdict"] != "极简合规"


# ══════════ P0 原子化审查（覆盖 codeagent-minimal 自身） ══════════
def test_atomicity_audit_all_green():
    a = _agents()["atomicity-audit"]
    for cap in ("atomicity.manifest", "atomicity.registry", "atomicity.breaks"):
        r = a.run(_capability=cap)
        assert r["ok"], f"{cap}: {r.get('error')}"
        assert r["data"]["ok"] is True, f"{cap} 检出问题: {r['data'].get('verdict')}"


def test_atomicity_audit_catches_mismatch():
    a = _agents()["atomicity-audit"]
    # 构造 registry 与磁盘不一致 → 应检出 missing_in_registry
    import tempfile
    d = tempfile.mkdtemp()
    bad = os.path.join(d, "registry.json")
    json.dump({"schema": "codeagent-registry-v1", "agents": {}, "order": [],
               "conflicts": []}, open(bad, "w", encoding="utf-8"))
    r = a.run(_capability="atomicity.registry", registry_path=bad)
    assert r["ok"], r.get("error")
    assert r["data"]["mismatch"], "空 registry 应检出缺失新原子"


# ══════════ P0 本体审查（factory-ontology） ══════════
def test_ontology_chain_reviews_real_link():
    a = _agents()["ontology-review"]
    r = a.run(_capability="ontology.chain")
    assert r["ok"], r.get("error")
    assert r["data"]["links"], "链路审查无 links"
    assert all("ok" in l for l in r["data"]["links"])


def test_ontology_quality_runs():
    a = _agents()["ontology-review"]
    r = a.run(_capability="ontology.quality")
    assert r["ok"], r.get("error")
    assert "verdict" in r["data"] and "issues" in r["data"]


# ══════════ P1 本地化 ══════════
def test_localized_data_not_leak_detects_network():
    a = _agents()["localized"]
    r = a.run(_capability="local.audit", code="import requests\nimport os")
    assert r["ok"] and not r["data"]["data_not_leak"]
    assert any("requests" in s["signal"] for s in r["data"]["signals"])
    r2 = a.run(_capability="local.audit", code="import os\nimport json")
    assert r2["ok"] and r2["data"]["data_not_leak"] is True


def test_localized_chain_local_first():
    a = _agents()["localized"]
    # 指向不可达本地端点 + local_only → 拒绝上云(数据不出厂)
    r = a.run(_capability="local.chain", local_only=True,
              candidates=[("ghost", "127.0.0.1", 1)], timeout=0.3)
    assert r["ok"], r.get("error")
    assert r["data"]["local_up"] == []
    assert r["data"]["local_only"] is True
    assert "拒绝云端" in r["data"]["verdict"], f"local_only 应拒绝上云: {r['data']['verdict']}"


# ══════════ P2 领域代码审查 ══════════
def test_domain_imports_dependency_topology():
    a = _agents()["domain-review"]
    r = a.run(_capability="domain.imports", repos=[REPO_ROOT])
    assert r["ok"], r.get("error")
    assert r["data"]["topology"] and "summary" in r["data"]
    assert r["data"]["repos"], "未识别仓库"


def test_domain_valve_rules_detect_violations():
    a = _agents()["domain-review"]
    bad = [
        {"id": "V1", "type": "ball", "pressure_rating": 16,
         "operating_pressure": 15.0, "fail_position": "float"},   # 失效位置非法 + 压力裕度不足
        {"id": "V2", "type": "gate", "operating_temp": 220, "temp_max": 150},  # 超温 + 缺必填
    ]
    r = a.run(_capability="domain.valve", data=bad)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["records"] == 2 and d["violations"], "应检出阀门规则违规"
    assert d["by_sev"]["P0"] >= 1, "P0 级违规缺失"
    # 合规样例 → 无违规
    good = [{"id": "V3", "type": "ball", "pressure_rating": 16,
             "operating_pressure": 10, "fail_position": "close",
             "operating_temp": 100, "temp_max": 150, "material": "316L"}]
    r2 = a.run(_capability="domain.valve", data=good)
    assert r2["ok"] and r2["data"]["violations"] == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
