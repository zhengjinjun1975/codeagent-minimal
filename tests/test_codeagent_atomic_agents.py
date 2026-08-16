#!/usr/bin/env python3
"""test_atomic_agents.py — CodeAgent 原子化重构 P0 测试（pytest）。

覆盖：
1. agent_loader：manifest 校验（name==目录/缺 entry/缺字段）、依赖拓扑排序、
   冲突检测、失败降级
2. AtomicAgent 基类：生命周期 discovered→loaded→ready、能力注册、{ok,data} 信封、
   异常捕获降级
3. 3 原子 registry 加载 + 真实数据 run（impact 离线 / code-test 离线 / llm local-only 门）
双绿目标：本文件绿 + 现有 test_review.py 不破坏。
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import atomic_base
import agent_loader


# ══════════ 1. agent_loader：manifest 校验 ══════════

def _write_manifest(adir, data):
    os.makedirs(adir, exist_ok=True)
    with open(os.path.join(adir, "manifest.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(data, f, ensure_ascii=False)
    # 空 entry 壳，让 _load_manifest 只校验结构
    with open(os.path.join(adir, "main.py"), "w", encoding="utf-8") as f:
        f.write("")


def _valid_manifest(name="foo"):
    return {"name": name, "version": "0.1.0", "entry": "main.py",
            "provides": [name + ".x"], "depends_on": [], "open_source": True}


def test_manifest_name_must_equal_dir():
    with tempfile.TemporaryDirectory() as td:
        adir = os.path.join(td, "actual")
        _write_manifest(adir, _valid_manifest(name="other"))
        try:
            agent_loader._load_manifest(adir)
            assert False, "应抛 name != 目录 错误"
        except ValueError as e:
            assert "!= 目录名" in str(e)


def test_manifest_requires_entry_field():
    with tempfile.TemporaryDirectory() as td:
        adir = os.path.join(td, "foo")
        m = _valid_manifest()
        del m["entry"]
        _write_manifest(adir, m)
        try:
            agent_loader._load_manifest(adir)
            assert False, "应抛缺 entry"
        except ValueError as e:
            assert "entry" in str(e)


def test_manifest_requires_missing_entry_file():
    with tempfile.TemporaryDirectory() as td:
        adir = os.path.join(td, "foo")
        os.makedirs(adir, exist_ok=True)
        import json
        with open(os.path.join(adir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "foo", "version": "0.1.0", "entry": "nonexistent.py",
                       "provides": ["foo.x"]}, f)
        try:
            agent_loader._load_manifest(adir)
            assert False, "应抛 entry 不存在"
        except ValueError as e:
            assert "entry 不存在" in str(e)


def test_valid_manifest_passes():
    with tempfile.TemporaryDirectory() as td:
        adir = os.path.join(td, "foo")
        _write_manifest(adir, _valid_manifest())
        m = agent_loader._load_manifest(adir)
        assert m["name"] == "foo" and m["version"] == "0.1.0"


def test_scan_reports_bad_manifest_without_crash():
    with tempfile.TemporaryDirectory() as td:
        _write_manifest(os.path.join(td, "good"), _valid_manifest("good"))
        _write_manifest(os.path.join(td, "bad"), _valid_manifest("other"))
        r = agent_loader.scan(td)
        assert r["ok"] is True
        assert "good" in r["data"]["manifests"]
        assert r["data"]["errors"]  # bad 被记 errors，不中断


# ══════════ 2. agent_loader：依赖解析（拓扑排序）+ 冲突 ══════════

def test_topological_order_resolves_dependencies():
    # a 无依赖；b 依赖 a.x；c 依赖 b.y → 顺序 a→b→c
    manifests = {
        "a": {"name": "a", "provides": ["a.x"], "depends_on": []},
        "b": {"name": "b", "provides": ["b.y"], "depends_on": ["a.x"]},
        "c": {"name": "c", "provides": ["c.z"], "depends_on": ["b.y"]},
    }
    idx = agent_loader._build_provided_index(manifests)
    order, _ = agent_loader._resolve_order(manifests, idx)
    assert order.index("a") < order.index("b") < order.index("c")


def test_circular_dependency_flagged_and_degraded():
    manifests = {
        "a": {"name": "a", "provides": ["a.x"], "depends_on": ["b.y"]},
        "b": {"name": "b", "provides": ["b.y"], "depends_on": ["a.x"]},
    }
    idx = agent_loader._build_provided_index(manifests)
    order, conflicts = agent_loader._resolve_order(manifests, idx)
    assert conflicts and "依赖环" in conflicts[0]
    # 失败降级：仍补进队尾，保证不抛
    assert set(order) == {"a", "b"}


def test_unknown_dependency_flagged():
    manifests = {"a": {"name": "a", "provides": ["a.x"], "depends_on": ["nope.z"]}}
    idx = agent_loader._build_provided_index(manifests)
    _, conflicts = agent_loader._resolve_order(manifests, idx)
    assert any("nope.z" in c for c in conflicts)


def test_duplicate_capability_detected():
    manifests = {
        "a": {"name": "a", "provides": ["shared.x"], "depends_on": []},
        "b": {"name": "b", "provides": ["shared.x"], "depends_on": []},
    }
    idx = agent_loader._build_provided_index(manifests)
    conflicts = agent_loader._detect_conflicts(manifests, idx)
    assert any("shared.x" in c and "多原子提供" in c for c in conflicts)


# ══════════ 3. AtomicAgent 基类 ══════════

def _sample_agent():
    class A(atomic_base.AtomicAgent):
        name = "demo"
        version = "1.0.0"
        domain = "test"
        provides = ["demo.hello", "demo.boom"]

        def _register_defaults(self):
            self.register("demo.hello", lambda name="world": {"msg": f"hi {name}"})
            self.register("demo.boom", lambda: (_ for _ in ()).throw(RuntimeError("kaboom")))

    return A()


def test_lifecycle_and_register():
    a = _sample_agent()
    assert a.status == "loaded"   # __init__ 后为 loaded
    a.load()
    assert a.status == "ready"
    caps = a.capabilities()
    assert caps["demo.hello"]["callable"] and caps["demo.boom"]["callable"]
    a.unload()
    assert a.status == "loaded"


def test_call_ok_envelope():
    a = _sample_agent().load()
    r = a.call("demo.hello", name="CodeAgent")
    assert r["ok"] is True and r["data"] == {"msg": "hi CodeAgent"}


def test_call_exception_degrades():
    a = _sample_agent().load()
    r = a.call("demo.boom")
    assert r["ok"] is False and r.get("degraded") is True
    assert "kaboom" in r["error"]


def test_call_unregistered_capability_degrades():
    a = _sample_agent().load()
    r = a.call("demo.unknown")
    assert r["ok"] is False and r.get("degraded") is True


def test_run_uses_first_capability_and_describe():
    a = _sample_agent().load()
    r = a.run(name="via-run")
    assert r["ok"] and r["data"]["msg"] == "hi via-run"
    d = a.describe()
    assert d["name"] == "demo" and d["status"] == "ready" and d["version"] == "1.0.0"


# ══════════ 4. registry 加载 3 原子 + 真实 run ══════════

def test_registry_loads_three_atoms_ready():
    r = agent_loader.load_agents()
    assert r["ok"] is True
    agents = r["data"]["agents"]
    assert {"dep-impact", "llm-router", "code-test"} <= set(agents)
    assert r["data"]["degraded"] == []
    for a in agents.values():
        assert a.status == "ready"
    assert r["data"]["order"]  # 拓扑序非空


def test_impact_real_data_run():
    r = agent_loader.load_agents()
    impact = r["data"]["agents"]["dep-impact"]
    res = impact.run(_capability="impact.analyze", path=REPO_ROOT,
                     impact="dep_report", transitive=True)
    assert res["ok"] is True
    assert res["data"]["entities"] > 0
    assert "impact" in res["data"] and "dep_report" in res["data"]["impact"]
    circ = impact.run(_capability="impact.circular", path=REPO_ROOT)
    assert circ["ok"] and "circular_imports" in circ["data"]


def test_code_test_real_data_run():
    r = agent_loader.load_agents()
    test = r["data"]["agents"]["code-test"]
    content = open(os.path.join(REPO_ROOT, "sample_target.py"), encoding="utf-8").read()
    g = test.run(_capability="test.gen", code={"sample_target.py": content})
    assert g["ok"] and g["data"]["test_files"]
    tr = test.run(_capability="test.run",
                  path=os.path.join(REPO_ROOT, "sample_target.py"),
                  target_dir=REPO_ROOT, do_mutation=False)
    assert tr["ok"] and tr["data"]["smoke"]["ok"]
    assert "red_green" in tr["data"]


def test_llm_local_only_gate_no_network():
    r = agent_loader.load_agents()
    llm = r["data"]["agents"]["llm-router"]
    import time
    t0 = time.time()
    res = llm.run(_capability="llm.generate",
                  messages=[{"role": "user", "content": "hi"}], local_only=True)
    assert res["ok"] is False and res.get("degraded") is True
    assert "不出厂" in res["error"] or "local-only" in res["error"]
    assert time.time() - t0 < 2  # 立即返回，不发网络请求


def test_llm_review_degrades_gracefully():
    r = agent_loader.load_agents()
    llm = r["data"]["agents"]["llm-router"]
    res = llm.run(_capability="llm.review",
                  messages=[{"role": "user", "content": "review this"}], temp=0.4)
    # 本地 ornith 未必在跑：无论成败都不应抛、都走信封
    assert isinstance(res, dict) and "ok" in res
    assert res["ok"] is True or res.get("degraded") is True


# ══════════ 5. P0-1 回归：code-review path 分支不把审查结果当源码再分析 ══════════

def test_code_review_path_branch_detects_security():
    """回归护栏：bad_sample.py（命令注入/eval/硬编码密钥 3 major）经 path 分支必须全检出。
    修复前该分支把审查结果 JSON repr 当源码二次分析 → score 91 / 0 major（静默漏报）。"""
    bad = os.path.join(REPO_ROOT, "bad_sample.py")
    r = agent_loader.load_agents()
    a = r["data"]["agents"]["code-review"]
    rr = a.run(_capability="codereview.review", path=bad, use_llm=False)
    assert rr["ok"], f"path 分支审查失败: {rr.get('error')}"
    maj = [i for i in rr["data"]["issues"] if i["severity"] == "major"]
    assert len(maj) >= 3, f"应检出 ≥3 major 安全缺陷，实得 {len(maj)}"
    titles = "|".join(i["title"] for i in maj)
    assert "命令" in titles or "shell" in titles, f"漏报命令注入: {titles}"
    assert "eval" in titles or "exec" in titles, f"漏报 eval/exec: {titles}"
    assert "密钥" in titles or "密码" in titles, f"漏报硬编码密钥: {titles}"
    assert rr["data"]["score"] <= 70, f"score 应显著下降，实得 {rr['data']['score']}"


def test_llm_generate_local_only_default_blocks_network():
    """P0-3 回归：llm.generate local_only 默认 True → 不显式 False 即数据不出厂。"""
    r = agent_loader.load_agents()
    llm = r["data"]["agents"]["llm-router"]
    import time
    t0 = time.time()
    res = llm.run(_capability="llm.generate",
                  messages=[{"role": "user", "content": "hi"}])  # 不传 local_only
    assert res["ok"] is False and res.get("degraded") is True, f"默认应本地封锁: {res}"
    assert time.time() - t0 < 2  # 立即返回，不发网络请求



if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
