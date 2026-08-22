#!/usr/bin/env python3
"""test_bug_deep_atom.py — 深挖原子 bug-deep（pytest，真实代码验证）。

覆盖：
- 原子契约：registry 加载 ready，provides 齐全
- bugdeep.model 威胁建模先建攻击面（入口点/危险sink/信任边界）
- bugdeep.adv 对抗性审查（先假设误报证伪 + 规则反哺命中）
- bugdeep.poc 自动化 PoC 沙箱跑证据（os.system → exploitable 真实执行证据）
- bugdeep.rule AI 规则反哺闭环（验证漏洞 → 沉淀规则）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agent_loader
import bug_deep as bd

VULN_CODE = """\
import os, pickle

def run(user_input):
    cmd = user_input
    os.system(cmd)          # 命令注入(污点确认)

def load(data):
    return pickle.loads(data)
"""


def _atom():
    r = agent_loader.load_agents()
    assert r["ok"]
    a = r["data"]["agents"]["bug-deep"]
    assert a.status == "ready"
    return a


def _tmp_py(content):
    fd, p = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return p


# ── 原子契约 ──
def test_bugdeep_atom_contract():
    a = _atom()
    assert a.version == "0.1.0"
    for cap in ("bugdeep.model", "bugdeep.adv", "bugdeep.poc", "bugdeep.rule"):
        assert a.capabilities()[cap]["callable"], f"{cap} 应可调用"


# ── 威胁建模：先建攻击面 ──
def test_threat_model_attack_surface():
    p = _tmp_py(VULN_CODE)
    try:
        a = _atom()
        r = a.run(_capability="bugdeep.model", path=p)
        assert r["ok"]
        d = r["data"]
        sinks = [s["name"] for s in d["sinks"]]
        assert "os.system" in sinks and "pickle.loads" in sinks
        assert d["trust_boundaries"], "应识别信任边界"
        assert d["attack_surface"], "应先建攻击面"
        # 入口参数 user_input → os.system 污染
        assert "命令执行" in {s["type"] for s in d["sinks"]}
    finally:
        os.remove(p)


# ── 对抗性审查（先假设误报证伪）──
def test_adversarial_review_verdicts():
    p = _tmp_py(VULN_CODE)
    try:
        a = _atom()
        r = a.run(_capability="bugdeep.adv", path=p)
        assert r["ok"]
        d = r["data"]
        # 至少一条 confirmed（os.system 有污点）或 needs_review
        verdicts = {f["verdict"] for f in d["findings"]}
        assert verdicts & {"confirmed", "needs_review"}, f"应含 confirmed/needs_review: {verdicts}"
        assert "summary" in d and "attack_surface" in d
    finally:
        os.remove(p)


# ── 自动化 PoC 沙箱跑证据 ──
def test_poc_sandbox_os_system_exploitable():
    poc = bd.generate_poc({"sink": "os.system", "title": "os.system"})
    assert poc["poc_code"]
    ev = bd.run_poc_sandbox(poc["poc_code"], timeout=8)
    assert ev["ran"] is True
    assert ev["verdict"] == "exploitable", f"os.system PoC 应可触发, 实得 {ev['verdict']}"
    assert ev["marker_hit"] is True
    assert "evidence" in ev


def test_poc_sandbox_pickle_rce():
    poc = bd.generate_poc({"sink": "pickle.loads", "title": "pickle.loads"})
    ev = bd.run_poc_sandbox(poc["poc_code"], timeout=8)
    assert ev["ran"] is True
    # pickle.__reduce__ → os.system → exploitable 或 crash（破坏性载荷）
    assert ev["verdict"] in ("exploitable", "crash", "no_trigger"), ev["verdict"]


def test_poc_atom_via_registry():
    a = _atom()
    r = a.run(_capability="bugdeep.poc", sink="os.system", title="os.system", run=True)
    assert r["ok"]
    assert r["data"]["sandbox"]["verdict"] == "exploitable"


# ── AI 规则反哺闭环（验证漏洞 → 沉淀规则）──
def test_rule_feedback_loop_sediment():
    rules_file = os.path.join(tempfile.mkdtemp(), "rules.json")
    issue = {"sink": "os.system", "title": "os.system 命令注入"}
    res = bd.sediment_rule(issue, verified=True, evidence="PoC exploitable",
                           rules_file=rules_file)
    assert res["status"] == "added" and res["verified"] is True
    assert os.path.exists(rules_file)
    rules = bd.load_rules(rules_file)
    assert any(r["sink"] == "os.system" and r["verified"] for r in rules)
    # 再次沉淀 → updated
    res2 = bd.sediment_rule(issue, verified=True, rules_file=rules_file)
    assert res2["status"] == "updated"


def test_rule_feedback_close_loop():
    rules_file = os.path.join(tempfile.mkdtemp(), "rules.json")
    a = _atom()
    r = a.run(_capability="bugdeep.rule", sink="os.system", title="os.system",
              verify=True, rules_file=rules_file)
    assert r["ok"]
    d = r["data"]
    assert d["closed"] is True and d["verified"] is True
    assert d["rule"]["status"] in ("added", "updated")
    # 规则已反哺沉淀
    assert any(x["sink"] == "os.system" and x["verified"]
               for x in bd.load_rules(rules_file))


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
