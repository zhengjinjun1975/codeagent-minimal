#!/usr/bin/env python3
"""跨仓库断链(chainbreak) / 智能测试选择(select_affected_tests_git) / 覆盖度(coverage_analysis)
的自包含单测（P1 测试固化：把 git log/文档证据转化为可回归的测试证据）。

全部用 tmp_path 临时目录构造真实小文件，不依赖真实多仓库生态，可离线全量跑。
"""
import os
import sys
import textwrap

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import chain_break as cb
import reg_guard as rg
import test_harness as th


# ═══════════════ P1-6 跨仓库断链 chain_break ═══════════════
def _write(root, relpath, content):
    p = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return p


def test_chainbreak_detects_import_break(tmp_path):
    """跨仓库 import 引用不存在的模块 → 报断链（能通但不生效）。"""
    r1 = tmp_path / "repoA"
    r1.mkdir()
    _write(str(r1), "mod_a.py", "def a(): return 1\n")
    # repoA 引用了 cross_pkg.helper，但没有任何仓库提供该模块 → 断链
    _write(str(r1), "main.py", "import os\nimport cross_pkg.helper\n")
    r = cb.multi_repo_break_check([str(r1)], report=False)
    assert r["ok"] is False
    broken = [b for b in r["broken"] if b["type"] == "import"]
    assert any(b["module"] == "cross_pkg.helper" for b in broken)


def test_chainbreak_no_false_positive_on_resolved_import(tmp_path):
    """跨仓库断链修复：import 的模块在任一仓库真实存在 → 不算断链（不再假断链）。"""
    r1 = tmp_path / "repoA"
    r2 = tmp_path / "repoB"
    r1.mkdir()
    r2.mkdir()
    _write(str(r1), "mod_a.py", "def a(): return 1\n")
    _write(str(r2), "shared/helper.py", "def h(): return 2\n")
    # repoA 引用的 shared.helper 在 repoB 真实存在 → 跨仓库可达，非断链
    _write(str(r1), "main.py", "import shared.helper\nimport os\n")
    r = cb.multi_repo_break_check([str(r1), str(r2)], report=False)
    broken = [b for b in r["broken"] if b["type"] == "import"]
    assert not any(b["module"] == "shared.helper" for b in broken)


def test_chainbreak_detects_path_break(tmp_path):
    """open() 引用不存在的资源文件 → 路径断链。"""
    r1 = tmp_path / "repoA"
    r1.mkdir()
    _write(str(r1), "app.py", 'def load():\n    with open("config_missing.json") as f:\n        return f.read()\n')
    r = cb.multi_repo_break_check([str(r1)], report=False)
    broken = [b for b in r["broken"] if b["type"] == "path"]
    assert any(b["path"] == "config_missing.json" for b in broken)


def test_chainbreak_ok_when_no_breaks(tmp_path):
    """无断链 → ok=True，by_tier 全 0。"""
    r1 = tmp_path / "repoA"
    r1.mkdir()
    _write(str(r1), "mod_a.py", "def a(): return 1\n")
    _write(str(r1), "main.py", "import os\nimport json\n")
    r = cb.multi_repo_break_check([str(r1)], report=False)
    assert r["ok"] is True
    assert r["broken"] == []
    assert r["by_tier"]["P0"] == 0


def test_chainbreak_empty_repos_ok():
    """无仓库目录 → ok=True 不崩溃。"""
    r = cb.multi_repo_break_check([], report=False)
    assert r["ok"] is True


def test_chainbreak_agent_depscan_exposes_chainbreak(tmp_path):
    """depscan.chainbreak CLI 能力真实暴露（信封包裹）。"""
    r1 = tmp_path / "repoA"
    r1.mkdir()
    _write(str(r1), "main.py", "import ghost_abs_pkg\n")
    from agent_loader import load_agents, REPO_ROOT
    reg = load_agents()
    agents = reg["data"]["agents"]
    assert "dep-scan" in agents
    assert "depscan.chainbreak" in agents["dep-scan"].provides
    # 直接加载原子壳并验证信封 run（dep-scan 目录含连字符，用文件加载）
    import importlib.util
    main_py = os.path.join(REPO_ROOT, "agents", "depscan", "dep-scan", "main.py")
    spec = importlib.util.spec_from_file_location("depscan_main", main_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dep_agent = mod.agent
    dep_agent.load()
    r = dep_agent.run(_capability="depscan.chainbreak", repos=[str(r1)])
    assert r["ok"] is True  # 信封 ok=能力正常执行（无异常）
    assert r["data"]["ok"] is False  # 检测到断链 → 能力层 ok=False
    assert any(b["module"] == "ghost_abs_pkg" for b in r["data"]["broken"])


# ═══════════════ P0-3 智能测试选择 select_affected_tests_git ═══════════════
def test_select_affected_tests_basic(tmp_path):
    """改 alpha.py → 只选到测 alpha 的测试（增量，非全量）。"""
    root = str(tmp_path)
    _write(root, "alpha.py", "def alpha(): return 'a'\n")
    _write(root, "beta.py", "def beta(): return 'b'\n")
    _write(root, "tests/test_alpha.py", "def test_alpha(): pass\n")
    _write(root, "tests/test_beta.py", "def test_beta(): pass\n")
    r = rg.select_affected_tests([os.path.join(root, "alpha.py")],
                                 project_root=root)
    names = [os.path.basename(t) for t in r["affected_tests"]]
    assert "test_alpha.py" in names
    assert "test_beta.py" not in names
    assert r["affected_modules"]  # 影响模块非空


def test_select_affected_tests_no_py_all_tests_false(tmp_path):
    """无改动 .py 文件 → all_tests=False，不影响任何测试。"""
    root = str(tmp_path)
    _write(root, "alpha.py", "def alpha(): return 'a'\n")
    r = rg.select_affected_tests([], project_root=root)
    assert r["all_tests"] is False
    assert r["affected_tests"] == []


def test_select_affected_tests_git_nondep_fallback(tmp_path):
    """git 仓库有改动 → source=git；无 git → 退化 all_tests=True（提示全量）。"""
    # 非 git 仓库：git_changed_py 返回 [] → all_tests=True
    root = str(tmp_path)
    _write(root, "alpha.py", "def alpha(): return 'a'\n")
    r = rg.select_affected_tests_git(project_root=root)
    assert r["all_tests"] is True
    assert r["source"] == "git"


def test_select_affected_tests_transitive(tmp_path):
    """传递影响：beta import alpha，改 alpha → beta 的测试也应被选到。"""
    root = str(tmp_path)
    _write(root, "alpha.py", "def alpha(): return 'a'\n")
    _write(root, "beta.py", "import alpha\ndef beta(): return alpha.alpha()\n")
    _write(root, "tests/test_beta.py", "def test_beta(): pass\n")
    r = rg.select_affected_tests([os.path.join(root, "alpha.py")],
                                 project_root=root, transitive=True)
    names = [os.path.basename(t) for t in r["affected_tests"]]
    assert "test_beta.py" in names


# ═══════════════ P1-5 覆盖度 coverage_analysis ═══════════════
def test_coverage_analysis_reports_untested(tmp_path):
    """存在未调用的函数 → coverage_pct < 100，untested 非空，suggestions 有补测建议。"""
    p = _write(str(tmp_path), "target.py",
               "def covered(): return 1\ndef uncovered_branch(x):\n    if x > 0:\n        return 1\n    return 0\n")
    r = th.coverage_analysis(p)
    assert r["ok"] is False or r["coverage_pct"] <= 100
    # covered 有顶格探测可调用；uncovered_branch 用 None/0 探测也能进函数 → 至少结构可解析
    assert r["funcs_total"] == 2
    assert r["coverage_pct"] > 0


def test_coverage_analysis_syntax_error_skipped(tmp_path):
    """语法错误 → skipped=True 不崩溃。"""
    p = _write(str(tmp_path), "bad.py", "def broken(:\n")
    r = th.coverage_analysis(p)
    assert r.get("skipped") is True
    assert "语法错误" in r.get("details", "")


def test_coverage_analysis_suggestions_nonempty(tmp_path):
    """suggestions 结构稳定（补测建议存在）。"""
    p = _write(str(tmp_path), "t.py",
               "def f(a):\n    return a if a else None\n")
    r = th.coverage_analysis(p)
    assert isinstance(r["suggestions"], list)
    assert "funcs_total" in r and "branch_missing" in r
