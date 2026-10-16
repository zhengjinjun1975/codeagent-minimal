#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_report_gate_sarif.py — 审查报告的「放行判定（质量门）」与「SARIF 导出」单元测试。

为什么单独一个文件：这两块是给**不写代码的审查人**看的结论层，判错等于给错结论，
所以按可复算的方式钉死：给定发现列表 → 判定唯一；给定发现列表 → SARIF 结构合法。

全部纯函数测试，不启服务、不联网、不碰磁盘。
"""
import json
import os
import sys

LAB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lab"))
if LAB not in sys.path:
    sys.path.insert(0, LAB)

import report_gen as rg  # noqa: E402


def _f(level, atom="code-review"):
    return rg._f(level, "a.py", 1, "问题", "建议", atom)


def _secs(ok=True):
    return [{"atom": "code-review", "ok": ok}]


# ── 严重度归一：各原子实际词表 critical/major/minor → P0/P1/P2 ──
def test_severity_mapping_real_vocab():
    assert _f("critical")["level"] == "P0"
    assert _f("major")["level"] == "P1"
    assert _f("minor")["level"] == "P2"
    assert _f("未知词表")["level"] == "P2"      # 不把未知当阻断项
    assert _f("P1")["level"] == "P1"            # 已是 Pn 的原样保留


# ── 判定四档 ──
def test_verdict_pass_when_clean():
    g = rg._gate(_secs(), [])
    assert g["verdict"] == "放行", g


def test_verdict_blocked_on_p0():
    g = rg._gate(_secs(), [_f("critical")])
    assert g["verdict"] == "不放行"
    assert g["counts"]["P0"] == 1


def test_verdict_conditional_when_only_p1():
    g = rg._gate(_secs(), [_f("major"), _f("major")])
    assert g["verdict"] == "有条件放行"
    assert "2 条 P1" in g["reasons"][0]


def test_verdict_undecidable_when_evidence_incomplete():
    """原子没跑成 → 明确判「无法判定」，绝不能默认放行（无假能力）。"""
    g = rg._gate(_secs(ok=False), [])
    assert g["verdict"] == "无法判定"
    assert g["failed_atoms"] == ["code-review"]


def test_p1_never_blocks():
    """P1 只计数不拦截：300 条 P1 也不能判成「不放行」。"""
    g = rg._gate(_secs(), [_f("major")] * 300)
    assert g["verdict"] == "有条件放行"


def test_conditions_have_hard_thresholds_only():
    g = rg._gate(_secs(), [_f("critical")])
    thr = {c["name"]: c["threshold"] for c in g["conditions"]}
    assert thr["阻断缺陷 P0 = 0（critical 级缺陷/漏洞）"] == 0
    assert thr["证据完整性（全部原子执行成功）"] == 0
    assert thr["高风险 P1（仅计数，不拦截）"] == "—"


# ── SARIF 2.1.0 ──
def test_sarif_structure_is_valid():
    findings = [_f("critical"), _f("major", atom="security-scan")]
    s = rg._sarif("pkg", findings, _secs())
    assert s["version"] == "2.1.0"
    assert s["$schema"].endswith("sarif-2.1.0.json")
    run = s["runs"][0]
    assert run["tool"]["driver"]["name"] == "CodeAgent"
    rules = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert rules == {"code-review", "security-scan"}
    lv = {r["ruleId"]: r["level"] for r in run["results"]}
    assert lv["code-review"] == "error"                  # critical → error
    assert lv["security-scan"] == "warning"              # major → warning
    for r in run["results"]:
        assert r["locations"][0]["physicalLocation"]["region"]["startLine"] >= 1
        assert r["partialFingerprints"]["primaryLocationLineHash"]
        assert r["message"]["text"]


def test_sarif_severity_levels_map_to_standard():
    s = rg._sarif("pkg", [_f("critical"), _f("major"), _f("minor")], _secs())
    levels = [r["level"] for r in s["runs"][0]["results"]]
    assert levels == ["error", "warning", "note"]


def test_sarif_fingerprints_are_stable_and_content_addressed():
    """同一问题两次导出指纹一致；不同问题指纹不同（GitHub 去重依赖这个）。"""
    f1 = [_f("major")]
    a = rg._sarif("pkg", f1, _secs())["runs"][0]["results"][0]["partialFingerprints"]
    b = rg._sarif("pkg", f1, _secs())["runs"][0]["results"][0]["partialFingerprints"]
    c = rg._sarif("pkg", [rg._f("major", "b.py", 9, "另一个问题", "", "code-review")],
                  _secs())["runs"][0]["results"][0]["partialFingerprints"]
    assert a == b and a != c


def test_sarif_paths_use_forward_slashes():
    s = rg._sarif("pkg", [rg._f("major", "a\\b\\c.py", 3, "x", "", "code-review")], _secs())
    uri = s["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert "\\" not in uri and uri.endswith("c.py")


# ── 目录目标：逐文件覆盖 + 覆盖率如实标注 ──
def test_py_files_dir_and_file(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "sub"))
        os.makedirs(os.path.join(d, "__pycache__"))
        for n in ("a.py", "b.py", os.path.join("sub", "c.py"), os.path.join("__pycache__", "x.py")):
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
        files, total = rg._py_files(d)
        rel = sorted(os.path.basename(p) for p in files)
        assert rel == ["a.py", "b.py", "c.py"]      # 排除 __pycache__
        assert total == 3
        one, t1 = rg._py_files(os.path.join(d, "a.py"))
        assert len(one) == 1 and t1 == 1
        capped, t2 = rg._py_files(d, limit=2)
        assert len(capped) == 2 and t2 == 3          # 覆盖被截断但总数如实给出


def test_gate_json_serializable():
    """判定要能进 /api/report 的 JSON 响应（不能含集合/对象）。"""
    g = rg._gate(_secs(), [_f("critical")])
    json.dumps(g, ensure_ascii=False)
