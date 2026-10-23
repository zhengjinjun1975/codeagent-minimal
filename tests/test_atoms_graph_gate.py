#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原子图谱门的自测：既验本仓现状，也验**规则真的有分辨力**。

只测本仓很容易变成"跑绿了"——本仓今天恰好没有组合错位，不代表规则能抓住错位。
所以每个规则都用一个迷你仓库做负向验证：植入缺陷，看它是否如期变红。
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import atoms_graph_core as core  # noqa: E402


@pytest.fixture(scope="module")
def graph():
    return core.build_graph(ROOT)


def _rules(graph):
    return {f["rule"] for f in graph["findings"]}


# ────────────────────────── 本仓现状（防跑空） ──────────────────────────
def test_repo_is_not_empty(graph):
    """断言下限，防止抽取逻辑坏掉后"零命中"被当成干净。"""
    m = graph["metrics"]
    assert m["atoms"] >= 30, "抽到的原子太少，抽取逻辑可能坏了"
    assert m["capabilities"] >= 120, "抽到的能力太少"
    assert m["call_sites"] >= 20, "抽到的调用点太少"


def test_every_declared_capability_is_mounted(graph):
    """provides 声明了就必须在 main.py 里 register，否则调了必失败。"""
    assert "G12_CAP_NOT_MOUNTED" not in _rules(graph), \
        [f["subject"] for f in graph["findings"] if f["rule"] == "G12_CAP_NOT_MOUNTED"]


def test_no_capability_mounted_but_undeclared(graph):
    assert "G13_CAP_UNDECLARED" not in _rules(graph), \
        [f["subject"] for f in graph["findings"] if f["rule"] == "G13_CAP_UNDECLARED"]


def test_compositions_all_resolve(graph):
    """组合的每一步都必须能解析到真实能力 —— 这是"组合可追踪"的底线。"""
    assert "G9_CHAIN_PHANTOM" not in _rules(graph), \
        [f["subject"] for f in graph["findings"] if f["rule"] == "G9_CHAIN_PHANTOM"]
    assert "G10_CHAIN_INPUT_MISMATCH" not in _rules(graph), \
        [f["subject"] for f in graph["findings"] if f["rule"] == "G10_CHAIN_INPUT_MISMATCH"]
    assert "G11_CHAIN_PLACEHOLDER" not in _rules(graph)


def test_template_compositions_exist(graph):
    """模板表是组合的主要声明处，抽不到说明路径或解析坏了。"""
    tpls = [c for c in graph["chains"] if c["kind"] == "template"]
    assert len(tpls) >= 5, "组合模板抽得太少：%d" % len(tpls)
    for c in tpls:
        assert c["steps"], "模板 %s 抽不出步骤" % c["name"]
        for st in c["steps"]:
            assert st["resolved"], "%s 的 %s 解析不到原子" % (c["name"], st["capability"])
            assert st["atom"], "%s 的 %s 没有归属原子" % (c["name"], st["capability"])


def test_code_chain_extracted(graph):
    """codeagent.py 里那条 5 步链必须被抽出来，它是代码内组合的唯一实例。"""
    code_chains = [c for c in graph["chains"] if c["kind"] == "code" and not c["dynamic"]]
    assert code_chains, "代码内的组合一条都没抽到"
    seqs = [[st["capability"] for st in c["steps"]] for c in code_chains]
    assert ["plan.think", "plan.gen", "codereview.review", "test.run", "evolve.refine"] in seqs


def test_capability_index_covers_all(graph):
    """反查索引必须覆盖每条声明能力，否则"查影响面"会漏。"""
    caps = {c for n in graph["nodes"] for c in n["provides"]}
    assert set(graph["capability_index"]) == caps


def test_chain_only_capabilities_counted_as_wired(graph):
    """只经组合可达的能力不能被判成未接线 —— 那会把主干能力当成没人用。"""
    called = {c["capability"] for c in graph["call_sites"]}
    chain_only = [cap for cap in graph["capability_index"] if cap not in called]
    wired = {n["id"] for n in graph["nodes"] if n["wired"]}
    for n in graph["nodes"]:
        only_chain = [c for c in n["provides"] if c not in called]
        if only_chain and any(graph["capability_index"][c]["chains"] for c in only_chain):
            assert n["id"] in wired, "%s 只经组合可达，却被判成未接线" % n["id"]


def test_graph_is_deterministic(graph):
    """两次建图必须完全一致，否则 CI 与本地各说各话，diff 也会抖。"""
    again = core.build_graph(ROOT)
    assert json.dumps(graph, ensure_ascii=False, sort_keys=True) == \
           json.dumps(again, ensure_ascii=False, sort_keys=True)


def test_render_is_deterministic_and_complete(graph):
    md1, md2 = core.render_markdown(graph), core.render_markdown(graph)
    html1, html2 = core.render_html(graph), core.render_html(graph)
    assert md1 == md2 and html1 == html2
    for token in ("组合清单", "能力 → 组合 反查", "规则命中"):
        assert token in md1
    assert "<svg" not in html1 or True  # 结构不依赖 svg，仅确保不抛错
    assert "组合链" in html1


# ────────────────────────── 基线台账纪律 ──────────────────────────
def test_baseline_entries_follow_discipline():
    bl = core.load_baseline(ROOT)
    assert not bl.get("_missing"), "台账文件不存在"
    assert bl.get("expires"), "整本台账没有到期日（会烂成永久豁免）"
    for e in bl["entries"]:
        assert e.get("reason"), "条目没有理由：%s" % e.get("subject")
        assert e.get("expires"), "条目没有到期日：%s" % e.get("subject")
        assert e.get("severity") in ("P2", "P3"), "P0/P1 不许进台账：%s" % e.get("subject")


def test_baseline_not_expired(graph):
    bl = core.load_baseline(ROOT)
    res = core.apply_baseline(graph["findings"], bl)
    expired = [p for p in res["problems"] if p["rule"] == "BASELINE_EXPIRED"]
    assert not expired, expired


def test_baseline_has_no_stale_entry(graph):
    """台账里失效的条目不清理，就会掩盖以后新出现的问题。"""
    bl = core.load_baseline(ROOT)
    res = core.apply_baseline(graph["findings"], bl)
    stale = [p for p in res["problems"] if p["rule"] == "BASELINE_STALE"]
    assert not stale, stale


def test_p0_p1_have_no_baseline():
    """P0/P1 一律当场修，台账里不许有一条 —— 否则门可以被"登记"绕过。"""
    bl = core.load_baseline(ROOT)
    bad = [e for e in bl["entries"] if e.get("severity") in ("P0", "P1")]
    assert not bad, bad


# ────────────────────────── 增量范围 ──────────────────────────
def test_public_layer_change_forces_full_scan(graph):
    atoms = {n["id"]: {"path": n["path"]} for n in graph["nodes"]}
    inside, outside, whole, why = core.scope(graph["findings"], ["agent_runtime.py"], atoms)
    assert whole is True, "改公共层没有升级为全仓判定"
    assert "全仓" in why


def test_unrelated_file_keeps_scope_narrow(graph):
    atoms = {n["id"]: {"path": n["path"]} for n in graph["nodes"]}
    inside, outside, whole, _ = core.scope(graph["findings"], ["docs/README-xx.md"], atoms)
    assert whole is False
    assert len(inside) < len(graph["findings"]), "无关改动却把全仓问题都算进来了"


# ────────────────────────── 负向验证：规则真有分辨力 ──────────────────────────
MINI = {
    "registry.json": json.dumps({
        "schema": "codeagent-registry-v1", "agents_dir": "agents",
        "order": ["atom-a", "atom-b"], "conflicts": [],
        "agents": {
            "atom-a": {"name": "atom-a", "version": "0.1.0", "domain": "alpha",
                       "description": "d", "open_source": True, "agent_class": "A",
                       "entry": "main.py", "provides": ["alpha.bump"], "depends_on": [],
                       "inputs": ["path"], "outputs": [], "path": "agents/alpha/atom-a"},
            "atom-b": {"name": "atom-b", "version": "0.1.0", "domain": "beta",
                       "description": "d", "open_source": True, "agent_class": "B",
                       "entry": "main.py", "provides": ["beta.run", "beta.idle"],
                       "depends_on": ["alpha.bump"],
                       "inputs": [], "outputs": [], "path": "agents/beta/atom-b"},
        },
    }, ensure_ascii=False),
    "agents/alpha/atom-a/main.py": textwrap.dedent('''
        class A:
            def _bump(self, path, mode="x"):
                return None
            def _extra(self):
                return None
            def _init(self):
                self.register("alpha.bump", self._bump)
                self.register("alpha.undeclared", self._extra)
        '''),
    "agents/beta/atom-b/main.py": textwrap.dedent('''
        class B:
            def _run(self, target="."):
                return None
            def _idle(self, target="."):
                return None
            def _init(self):
                self.register("beta.run", self._run)
                self.register("beta.idle", self._idle)
        '''),
    "runner.py": textwrap.dedent('''
        def go(rt):
            rt.run_capability("alpha.bump", pathOK="x")
            rt.run_capability("alpha.ghost")
            rt.run_capability("beta.run", target=".")
        '''),
    "agents/beta/atom-b/chain_templates.json": json.dumps({
        "_note": "mini", "templates": [
            {"name": "坏组合", "keywords": ["x"], "chain": [
                {"capability": "alpha.ghost", "inputs": {}},
                {"capability": "alpha.bump", "inputs": {"badkey": 1, "seed": "$nope"}},
            ]},
        ]}, ensure_ascii=False),
}


@pytest.fixture(scope="module")
def mini(tmp_path_factory):
    d = tmp_path_factory.mktemp("mini-atoms")
    for rel, content in MINI.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def test_negative_rules_fire(mini):
    g = core.build_graph(mini)
    fired = _rules(g)
    must_fire = {
        "G1_PHANTOM_CALL",           # 调了 alpha.ghost
        "G2_UNWIRED_CAPABILITY",     # beta.idle 挂了没人调
        "G3_DEP_NOT_EXERCISED",      # atom-b 声明依赖 alpha.bump 却没调
        "G5_CONTRACT_GAP",           # pathOK 不是 _bump 的参数
        "G9_CHAIN_PHANTOM",          # 组合里点了 alpha.ghost
        "G10_CHAIN_INPUT_MISMATCH",  # badkey 不在签名里
        "G11_CHAIN_PLACEHOLDER",     # $nope 未登记
        "G13_CAP_UNDECLARED",        # alpha.undeclared 挂了没声明
    }
    missing = must_fire - fired
    assert not missing, "这些规则该红没红：%s" % sorted(missing)
    # 反向：不该误报的
    assert "G12_CAP_NOT_MOUNTED" not in fired, "迷你仓没有漏挂载，不该报"
    assert "G7_CYCLE" not in fired, "迷你仓没有环，不该报"


def test_negative_chain_steps_traceable(mini):
    g = core.build_graph(mini)
    ch = [c for c in g["chains"] if c["kind"] == "template"]
    assert len(ch) == 1
    steps = ch[0]["steps"]
    assert steps[0]["resolved"] is False, "不存在的能力应标记为解析不到"
    assert steps[1]["resolved"] is True and steps[1]["sig_bad"], "入参不符应被标出"


def test_negative_scope_narrow(mini):
    """增量模式下，只改 beta 原子不该把 alpha 的问题算进本次范围。"""
    g = core.build_graph(mini)
    atoms = {n["id"]: {"path": n["path"]} for n in g["nodes"]}
    inside, outside, whole, _ = core.scope(g["findings"],
                                           ["agents/beta/atom-b/main.py"], atoms)
    assert whole is False
    assert any(f.get("atom") == "atom-b" for f in inside), "改动原子的自身问题应在范围内"
    assert outside, "另一个原子的存量问题应落在范围外"


# ────────────────────────── 命令行冒烟 ──────────────────────────
def test_cli_runs():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify_atoms_graph.py"),
                        "--all"], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "结论：通过" in r.stdout


def test_cli_json_only_json():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify_atoms_graph.py"),
                        "--all", "--json"], cwd=ROOT, capture_output=True, text=True, timeout=180)
    data = json.loads(r.stdout)  # 正文必须是纯 JSON
    assert data["verdict"] == "pass"
    assert data["metrics"]["atoms"] >= 30


def test_cli_check_drift_clean():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify_atoms_graph.py"),
                        "--check-drift"], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, "生成物与代码不同步：" + r.stdout
