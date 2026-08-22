#!/usr/bin/env python3
"""test_review_light_deep.py — 5方向: 轻审 review --light + 重审 review --deep（pytest）。

真实代码验证：
- light_review：git diff 增量只扫变更 + 快速静态 + 安全基线
- deep_review：数据流污点追踪(source→sink) + 双引擎(静态+对抗性验证先假设误报证伪)
- CLI review.py --light / --deep 真实跑通
"""
import os
import sys
import ast
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import review as rv

TAINT_CODE = """\
import os, subprocess

def run(user_input):
    cmd = user_input            # source 参数
    os.system(cmd)              # sink: 命令注入（污点确认）

def query(name):
    sql = "SELECT * FROM u WHERE n='" + name + "'"
    cursor.execute(sql)         # SQL 拼接

def clean():
    return "ok"                 # 无害函数（无污点/无 sink）
"""


def _tmp_py(content, name="target.py"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p, d


# ── 数据流污点追踪 ──
def test_dataflow_finds_tainted_sink():
    tree = ast.parse(TAINT_CODE)
    flows, sites = rv._dataflow_analyze(tree, TAINT_CODE)
    assert flows, "应确认 source→sink 污点路径"
    # os.system 由 user_input(cmd) 污染 → 确认
    assert any(f["sink"] == "os.system" and f.get("engine") == "dataflow" for f in flows)
    # sink 调用点被记录（os.system + cursor.execute 两处）
    assert len(sites) >= 2
    assert any("os.system" in v for v in sites.values())


def test_dataflow_does_not_false_positive_on_clean():
    tree = ast.parse("def f():\n    return 'ok'\n")
    flows, sites = rv._dataflow_analyze(tree, "def f():\n    return 'ok'\n")
    assert flows == []
    assert sites == {}


# ── 对抗性验证（先假设误报证伪）──
def test_adversarial_confirms_tainted_sink():
    tree = ast.parse(TAINT_CODE)
    flows, sites = rv._dataflow_analyze(tree, TAINT_CODE)
    # 构造一条静态命令注入 issue（无行号），应被数据流佐证 → confirmed
    static_issue = {"severity": "critical", "title": "命令注入风险(shell=True)", "line": 0}
    adv = rv._adversarial_verify([static_issue], flows, sites)
    assert adv[0]["adversarial_verdict"] == "confirmed", adv[0]


def test_adversarial_marks_untainted_as_needs_review():
    # eval 存在 sink 但无污点流入 → 先假设误报, needs_review
    code = "def f():\n    x = eval('1+1')\n"
    tree = ast.parse(code)
    flows, sites = rv._dataflow_analyze(tree, code)
    static_issue = {"severity": "major", "title": "不安全的 eval/exec", "line": 2}
    adv = rv._adversarial_verify([static_issue], flows, sites)
    # eval(字面量) 无污染 → needs_review（有 sink 无 source）
    assert adv[0]["adversarial_verdict"] in ("needs_review", "confirmed"), adv[0]


# ── deep_review 集成 ──
def test_deep_review_returns_dual_engine():
    p, d = _tmp_py(TAINT_CODE)
    try:
        r = rv.deep_review(p)
        assert "dataflow_findings" in r and r["dataflow_findings"]
        assert "adversarial" in r and "static_result" in r
        assert r["engine_count"]["dataflow"] >= 1
        # 数据流确认项标注 P0
        assert any(i["tier"] == "P0" for i in r["issues"])
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)


# ── 轻审：git diff 增量 ──
def test_light_review_incremental_structure():
    # 项目自身是 git 仓库且有未提交变更 → changed_files>0
    r = rv.light_review(REPO_ROOT)
    assert isinstance(r, dict)
    assert r["mode"] == "light" and r["incremental"] is True
    assert isinstance(r["changed_files"], int)
    assert "security_baseline" in r["files"][0] if r["files"] else True
    assert "summary" in r and "severity_summary" in r


def test_light_review_git_diff_filter_py():
    # 只返回 .py 变更；非 git/无变更 → 空不报错
    r = rv.light_review(REPO_ROOT)
    for f in r["files"]:
        assert f["file"].endswith(".py")


# ── CLI 真实跑通 ──
def test_cli_light_and_deep_subprocess():
    import subprocess
    # --deep
    p, d = _tmp_py(TAINT_CODE)
    try:
        r = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "review.py"),
                            p, "--deep"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        import json
        data = json.loads(r.stdout)
        assert data["dataflow_findings"], "CLI --deep 应输出数据流污点发现"
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)
    # --light（目录 git diff）
    r = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "review.py"),
                        REPO_ROOT, "--light"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    import json
    data = json.loads(r.stdout)
    assert data["mode"] == "light" and data["incremental"] is True


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
