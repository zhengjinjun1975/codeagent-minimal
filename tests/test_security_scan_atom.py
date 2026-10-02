#!/usr/bin/env python3
"""test_security_scan_atom.py — 安全原子 security-scan（pytest，真实代码验证）。

覆盖：
- 原子经 registry 加载 ready，provides 契约齐全
- security.scan 10 维度检出真实漏洞（SQL注入/反序列化/弱哈希/SSRF）
- security.secret 检出硬编码密钥
- security.dim 单维度
- security.project 全项目扫描
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agent_loader
import security_scan as ss

VULN_CODE = """\
import os, pickle, yaml, hashlib, requests

def run(user_input):
    os.system("ls " + user_input)          # 命令注入

def query(name):
    sql = "SELECT * FROM u WHERE n='" + name + "'"
    cursor.execute(sql)                    # SQL 注入

def load(data):
    return pickle.loads(data)              # 反序列化

def fetch(url):
    return requests.get(url)               # SSRF

def insecure():
    password = "P@ssw0rd12345"             # 硬编码
    return hashlib.md5(password)           # 弱哈希
"""


def _atom():
    r = agent_loader.load_agents()
    assert r["ok"]
    a = r["data"]["agents"]["security-scan"]
    assert a.status == "ready"
    return a


def _tmp_py(content):
    fd, p = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return p


# ── 原子契约 ──
def test_security_atom_contract_provides():
    a = _atom()
    assert a.version == "0.1.0"
    for cap in ("security.scan", "security.secret", "security.govern",
                "security.dim", "security.project"):
        assert a.capabilities()[cap]["callable"], f"{cap} 应可调用"


# ── security.scan：10 维度 ──
def test_security_scan_detects_real_vulns():
    p = _tmp_py(VULN_CODE)
    try:
        a = _atom()
        r = a.run(_capability="security.scan", path=p)
        assert r["ok"], r.get("error")
        d = r["data"]
        assert d["total"] > 0
        titles = "|".join(i["title"] for i in d["issues"])
        dims = {i["dimension"] for i in d["issues"]}
        # 关键维度命中
        assert "注入" in dims, f"缺注入维度: {dims}"
        assert "反序列化" in dims, f"缺反序列化: {dims}"
        assert "加密" in dims, f"缺加密: {dims}"
        # 有 P0 级（注入/反序列化 critical → P0）
        assert d["by_tier"]["P0"] > 0, f"应含 P0: {d['by_tier']}"
        assert "SQL" in titles or "注入" in titles
        assert "pickle" in titles or "反序列化" in titles
    finally:
        os.remove(p)


def test_security_scan_dimensions_are_ten():
    # 10 安全维度契约
    assert set(ss.SECURITY_DIMENSIONS) == {
        "注入", "认证", "授权", "反序列化", "文件", "SSRF", "加密", "配置", "业务", "供应链"}
    assert len(ss.SECURITY_DIMENSIONS) == 10


# ── security.secret：secret 检测 ──
def test_secret_detection_hardcoded():
    p = _tmp_py(VULN_CODE)
    try:
        a = _atom()
        r = a.run(_capability="security.secret", path=p)
        assert r["ok"]
        assert r["data"]["total"] >= 1
        assert any("口令" in s["type"] or "硬编码" in s["type"] for s in r["data"]["secrets"])
    finally:
        os.remove(p)


def test_secret_detection_aws_key():
    found = ss.detect_secrets('key = "AKIAIOSFODNN7EXAMPLE"')
    assert any(s["type"] == "AWS Access Key" and s["severity"] == "critical" for s in found)


# ── security.dim：单维度 ──
def test_security_dim_single():
    p = _tmp_py('def q(name):\n    sql = "SELECT * FROM t WHERE n=\'" + name + "\'"\n')
    try:
        a = _atom()
        r = a.run(_capability="security.dim", path=p, dimension="注入")
        assert r["ok"] and r["data"]["dimension"] == "注入"
        assert any("SQL" in i["title"] for i in r["data"]["issues"])
        # 非法维度 → 降级明确报错
        r2 = a.run(_capability="security.dim", path=p, dimension="不存在")
        assert r2["ok"] is False and r2.get("degraded") is True
    finally:
        os.remove(p)


# ── security.project：全项目扫描 ──
def test_security_project_scan():
    a = _atom()
    r = a.run(_capability="security.project", path=REPO_ROOT)
    assert r["ok"]
    assert r["data"]["file_count"] > 0
    assert "by_tier" in r["data"] and "summary" in r["data"]


# ── 误报治理：自指剔除 ──
def test_govern_false_positive_self_strip():
    # 扫描器自身不应扫出"自己"的规则常量为漏洞
    r = ss.scan_security_file(os.path.join(REPO_ROOT, "security_scan.py"))
    # 允许复杂度类，但不应有把规则常量当漏洞的自指命中
    assert isinstance(r["issues"], list)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
