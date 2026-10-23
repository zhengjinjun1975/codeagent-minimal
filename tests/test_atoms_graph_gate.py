#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原子图谱门的自测：既验本仓现状，也验**规则真的有分辨力**。

只测本仓很容易变成"跑绿了"——本仓今天恰好没有组合错位，不代表规则能抓住错位。
所以每条规则都用一个迷你仓库做负向验证：植入缺陷，看它是否如期变红。
二期新增的 G14–G19 尤其要成对测：只测"能红"会做出爱误报的门，
所以每条都配一条"恢复后不红"的正向断言。
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
    """模板表是组合的唯一来源，抽不到说明路径或解析坏了。"""
    tpls = [c for c in graph["chains"] if c["kind"] == "template"]
    assert len(tpls) >= 5, "组合模板抽得太少：%d" % len(tpls)
    for c in tpls:
        assert c["steps"], "模板 %s 抽不出步骤" % c["name"]
        for st in c["steps"]:
            assert st["resolved"], "%s 的 %s 解析不到原子" % (c["name"], st["capability"])
            assert st["atom"], "%s 的 %s 没有归属原子" % (c["name"], st["capability"])


def test_no_hardcoded_chain_left_in_code(graph):
    """组合只允许一个源（模板表）。代码里那条写死的 5 步链已经搬进模板表，
    所以这里不该再有"带步骤的代码内组合"（G15）。"""
    assert "G15_IMPLICIT_CHAIN" not in _rules(graph), \
        [f["subject"] for f in graph["findings"] if f["rule"] == "G15_IMPLICIT_CHAIN"]
    with_steps = [c for c in graph["chains"] if c["kind"] == "code" and c["steps"]]
    assert not with_steps, "代码里还有写死的组合：%s" % [c["chain_id"] for c in with_steps]


def test_default_chain_came_from_template_table(graph):
    """旧版写死的 5 步链必须原样出现在模板表里（步骤名与顺序都不许变），
    否则 `chain()` 与历史行为分叉。"""
    default = [c for c in graph["chains"] if c.get("default")]
    assert len(default) == 1, "模板表里应当正好有一条 default:true 的组合，实得 %d" % len(default)
    seq = [(st.get("step"), st["capability"]) for st in default[0]["steps"]]
    assert seq == [("think", "plan.think"), ("gen", "plan.gen"),
                   ("review", "codereview.review"), ("test", "test.run"),
                   ("evolve", "evolve.refine")], seq


def _is_bad_placeholder(v):
    """`$` 开头但不是四种合法形态之一 —— 那在运行时只会被当字面量原样传给原子。"""
    if not (isinstance(v, str) and v.startswith("$")) or v == core._EXPR_MARK:
        return False
    return not core._PLACEHOLDER_RE.match(v)


def _all_chain_inputs(graph):
    """图里所有组合步骤的静态入参，扁平化成 (组合名, 第几步, 键, 值)。"""
    for ch in graph["chains"]:
        for st in ch["steps"]:
            for k, v in core._walk_inputs(st.get("inputs") or {}):
                yield ch["name"], st.get("order"), k, v


def _bad_placeholders(graph):
    return ["%s 第%d步 %s=%r" % (name, order, k, v)
            for name, order, k, v in _all_chain_inputs(graph) if _is_bad_placeholder(v)]


def test_chain_inputs_use_registered_placeholders(graph):
    """模板入参里的 `$...` 必须是运行时真能解析的形态（$task / $upstream_files /
    $seed.x / $step.name.field）。这一条把"模板解析器"和"门的校验"绑在一起：
    任何一边单独改口径，这里就红。"""
    assert not _bad_placeholders(graph), _bad_placeholders(graph)


def test_capability_index_covers_all(graph):
    """反查索引必须覆盖每条声明能力，否则"查影响面"会漏。"""
    caps = {c for n in graph["nodes"] for c in n["provides"]}
    assert set(graph["capability_index"]) == caps


def test_chain_only_capabilities_counted_as_wired(graph):
    """只经组合可达的能力不能被判成未接线 —— 那会把主干能力当成没人用。"""
    called = {c["capability"] for c in graph["call_sites"] if c["capability"]}
    wired = {n["id"] for n in graph["nodes"] if n["wired"]}
    for n in graph["nodes"]:
        only_chain = [c for c in n["provides"] if c not in called]
        if only_chain and any(graph["capability_index"][c]["chains"] for c in only_chain):
            assert n["id"] in wired, "%s 只经组合可达，却被判成未接线" % n["id"]


def test_every_chain_is_reachable(graph):
    """每条声明的组合都必须有入口。（一期 6 条模板在 CLI 上一条都走不到，门却全绿）"""
    assert "G14_UNREACHABLE_CHAIN" not in _rules(graph), \
        [f["subject"] for f in graph["findings"] if f["rule"] == "G14_UNREACHABLE_CHAIN"]
    for ch in graph["chains"]:
        if ch.get("dynamic"):
            continue
        assert ch["reach"] in ("cli-reachable", "api-only"), \
            "%s 的判定是 %s" % (ch["name"], ch["reach"])


def test_entries_are_consistent(graph):
    """入口清单的 evidence 必须在代码里找得到（改了接线没改清单 → G19）。"""
    assert "G19_ENTRY_STALE" not in _rules(graph), \
        [f["detail"] for f in graph["findings"] if f["rule"] == "G19_ENTRY_STALE"]


def test_no_unsealed_dispatcher(graph):
    """转发型包装器必须声明边界，否则盲区变成不可审计的黑盒。"""
    assert "G18_DISPATCH_UNSEALED" not in _rules(graph), \
        [f["subject"] for f in graph["findings"] if f["rule"] == "G18_DISPATCH_UNSEALED"]


def test_evidence_levels_are_counted(graph):
    """证据等级必须覆盖所有调用点：四个档加起来要等于调用点总数，不许有漏网的。"""
    m = graph["metrics"]
    assert sum(m["evidence"].values()) == m["call_sites"]
    assert m["call_sites_static"] == m["evidence"]["verified"] + m["evidence"]["name-only"]
    assert 0.0 <= m["blind_ratio"] <= 1.0


def test_graph_is_deterministic(graph):
    """两次建图必须完全一致，否则 CI 与本地各说各话，diff 也会抖。"""
    again = core.build_graph(ROOT)
    assert json.dumps(graph, ensure_ascii=False, sort_keys=True) == \
           json.dumps(again, ensure_ascii=False, sort_keys=True)


def test_render_is_deterministic_and_complete(graph):
    md1, md2 = core.render_markdown(graph), core.render_markdown(graph)
    html1, html2 = core.render_html(graph), core.render_html(graph)
    assert md1 == md2 and html1 == html2
    for token in ("组合清单", "能力 → 组合 反查", "规则命中", "入口与可达性", "自知"):
        assert token in md1, "Markdown 少了一节：%s" % token
    assert "自知" in html1 and "组合链" in html1


def test_questions_are_four_not_one(graph):
    """门不许把判定压成布尔：四个问题各有一档。"""
    qs = graph["metrics"]["questions"]
    for q in ("Q1", "Q2", "Q3", "Q4"):
        assert qs[q]["level"] in ("green", "yellow", "red")
    assert qs["overall"] in ("pass", "fail")


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


def test_signature_snapshot_exists_and_matches(graph):
    """签名快照必须存在且与实现一致（缺失 = 签名变了没人知道）。"""
    snap = core.load_signatures(ROOT)
    assert not snap.get("_missing"), "config/atoms_signatures.json 不存在"
    breaking = [d for d in graph["signature_changes"] if d["severity"] in ("P0", "P1")]
    assert not breaking, breaking


def test_shrink_only_removes_dead_entries(graph):
    """自动收缩只摘"确实不再触发"的条目，不许误伤仍有效的条目。"""
    bl = core.load_baseline(ROOT)
    kept, removed = core.shrink_baseline(bl, graph["findings"])
    assert len(kept) == len(bl["entries"]), "还有效的条目被摘掉了"
    assert not removed
    fake = list(bl["entries"]) + [{"key": "G2_UNWIRED_CAPABILITY|不存在的原子 · x.y",
                                  "rule": "G2_UNWIRED_CAPABILITY", "subject": "x",
                                  "severity": "P2", "reason": "r", "expires": "2027-01-31"}]
    kept2, removed2 = core.shrink_baseline({"entries": fake}, graph["findings"])
    assert len(removed2) == 1 and removed2[0]["subject"] == "x"


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


# ────────────────────────── 负向验证：迷你仓 ──────────────────────────
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
            {"name": "坏组合", "keywords": ["x"], "default": True, "chain": [
                {"capability": "alpha.ghost", "inputs": {}},
                {"capability": "alpha.bump", "inputs": {"badkey": 1, "seed": "$nope"}},
            ]},
        ]}, ensure_ascii=False),
    "config/atoms_entries.json": json.dumps({
        "schema": "codeagent-atoms-entries-v1",
        "entries": [{"id": "cli:t", "kind": "cli", "refs": ["tpl:*"],
                     "evidence": {"file": "runner.py", "symbol": "go", "token": "run_capability"}}],
    }, ensure_ascii=False),
}


def _make_mini(dst):
    """铺一个迷你仓：先把签名快照按当前实现落好，免得 G17 缺失干扰负向断言。"""
    for rel, content in MINI.items():
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    snap = core.build_graph(dst)["signatures"]
    (dst / "config" / "atoms_signatures.json").write_text(
        json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return dst


@pytest.fixture(scope="module")
def mini(tmp_path_factory):
    return _make_mini(tmp_path_factory.mktemp("mini-atoms"))


@pytest.fixture()
def mini_copy(tmp_path):
    """每个负向用例一份干净副本，互不污染。"""
    return _make_mini(tmp_path / "mini")


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
    assert "G14_UNREACHABLE_CHAIN" not in fired, "入口清单指向 tpl:*，不该报不可达"
    assert "G17_SIGNATURE_BREAKING" not in fired, "快照刚生成，不该报签名变更"
    assert "G19_ENTRY_STALE" not in fired, "入口 evidence 是对的，不该报"


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


# ── G14：组合没有入口 → 红；补回入口 → 绿（成对验证，防爱误报的门）──
def test_g14_fires_without_entries(mini_copy):
    (mini_copy / "config" / "atoms_entries.json").unlink()
    g = core.build_graph(mini_copy)
    hits = [f for f in g["findings"] if f["rule"] == "G14_UNREACHABLE_CHAIN"]
    assert hits, "入口清单删了却不报不可达 —— 那 H1 那个洞就还在"
    assert g["metrics"]["entries_missing"] is True


def test_g14_quiet_with_entries(mini_copy):
    g = core.build_graph(mini_copy)
    assert not [f for f in g["findings"] if f["rule"] == "G14_UNREACHABLE_CHAIN"]
    assert g["metrics"]["chains_by_reach"].get("cli-reachable") == 1


# ── G15：代码里写死第二套组合 → 红 ──
def test_g15_fires_on_hardcoded_chain(mini_copy):
    (mini_copy / "hard.py").write_text(textwrap.dedent('''
        def run(rt):
            steps = [
                {"capability": "alpha.bump", "inputs": {"path": "."}},
                {"capability": "beta.run", "inputs": {"target": "."}},
            ]
            return rt.run_chain(steps)
        '''), encoding="utf-8")
    g = core.build_graph(mini_copy)
    hits = [f for f in g["findings"] if f["rule"] == "G15_IMPLICIT_CHAIN"]
    assert hits, "代码里写死的组合没报 —— 组合源分叉就没人知道"
    # 登记后应当放行（P1 不能用台账豁免，但可以"承认第二套存在"）
    (mini_copy / "config" / "atoms_chains_allow.json").write_text(
        json.dumps({"allow": [hits[0]["chain_id"]], "_note": "测试用"},
                   ensure_ascii=False), encoding="utf-8")
    g2 = core.build_graph(mini_copy)
    assert not [f for f in g2["findings"] if f["rule"] == "G15_IMPLICIT_CHAIN"], \
        "登记过的代码内组合仍被报红"


# ── G17：签名破坏 ──
def test_g17_fires_on_removed_param(mini_copy):
    p = mini_copy / "config" / "atoms_signatures.json"
    snap = json.loads(p.read_text(encoding="utf-8"))
    snap["capabilities"]["alpha.bump"]["params"] = ["path", "mode", "extra_old"]
    p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    g = core.build_graph(mini_copy)
    kinds = {d["kind"] for d in g["signature_changes"] if d["severity"] in ("P0", "P1")}
    assert kinds, "签名参数对不上却没报"
    assert any(f["rule"] == "G17_SIGNATURE_BREAKING" for f in g["findings"])


def test_g17_ignores_optional_addition(mini_copy):
    """新增可选参数是向后兼容的，只报不拦（否则门会变成噪声）。"""
    p = mini_copy / "config" / "atoms_signatures.json"
    snap = json.loads(p.read_text(encoding="utf-8"))
    snap["capabilities"]["alpha.bump"]["params"] = ["path"]   # 现状多一个可选 mode
    p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    g = core.build_graph(mini_copy)
    assert not [f for f in g["findings"] if f["rule"] == "G17_SIGNATURE_BREAKING"], \
        "新增可选参数不该拦"
    assert any(d["kind"] == "added_optional" for d in g["signature_changes"])


# ── G18：未声明边界的转发器 ──
def test_g18_fires_on_unsealed_dispatch(mini_copy):
    (mini_copy / "dispatch_unsealed.py").write_text(textwrap.dedent('''
        def forward(rt, cap, **kw):
            return rt.run_capability(cap, **kw)
        '''), encoding="utf-8")
    g = core.build_graph(mini_copy)
    hits = [f for f in g["findings"] if f["rule"] == "G18_DISPATCH_UNSEALED"]
    assert hits, "黑盒转发器没报 —— 盲区会无声长大"
    assert any(c["evidence"] == "blind" for c in g["call_sites"])


def test_g18_quiet_after_sealing(mini_copy):
    (mini_copy / "dispatch_sealed.py").write_text(textwrap.dedent('''
        def forward(rt, cap, **kw):
            # dispatch-sealed: alpha.
            return rt.run_capability(cap, **kw)
        '''), encoding="utf-8")
    g = core.build_graph(mini_copy)
    assert not [f for f in g["findings"] if f["rule"] == "G18_DISPATCH_UNSEALED"]
    sealed = [c for c in g["call_sites"] if c["evidence"] == "sealed"]
    assert sealed and sealed[0]["seal"] == "alpha."


# ── G19：入口声明过期 ──
def test_g19_fires_on_wrong_ref_and_missing_symbol(mini_copy):
    p = mini_copy / "config" / "atoms_entries.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["entries"].append({"id": "cli:bad-ref", "kind": "cli",
                         "refs": ["tpl:不存在.json#不存在的组合"],
                         "evidence": {"file": "runner.py", "symbol": "go", "token": "run_capability"}})
    d["entries"].append({"id": "cli:bad-symbol", "kind": "cli", "refs": [],
                         "evidence": {"file": "runner.py", "symbol": "no_such_fn", "token": "x"}})
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    g = core.build_graph(mini_copy)
    hits = [f for f in g["findings"] if f["rule"] == "G19_ENTRY_STALE"]
    assert len(hits) >= 2, "手敲的 ref 与失效符号都该被抓住：%s" % [h["detail"] for h in hits]


def test_g19_fires_when_wiring_removed(mini_copy):
    """入口声明的证据 token 在代码里消失 = 接线断了，必须红（不是沉默）。"""
    (mini_copy / "runner.py").write_text(
        "def go(rt):\n    return None\n", encoding="utf-8")
    g = core.build_graph(mini_copy)
    hits = [f for f in g["findings"] if f["rule"] == "G19_ENTRY_STALE"]
    assert hits, "接线被删了，入口清单却没报过期"


# ────────────────────────── 命令行冒烟 ──────────────────────────
def _cli(*args, timeout=240):
    return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify_atoms_graph.py")]
                          + list(args), cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def test_cli_runs():
    r = _cli("--all")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "结论：通过" in r.stdout
    assert "四问" in r.stdout


def test_cli_json_only_json():
    r = _cli("--all", "--json")
    data = json.loads(r.stdout)  # 正文必须是纯 JSON
    assert data["verdict"] == "pass"
    assert data["metrics"]["atoms"] >= 30
    for q in ("Q1", "Q2", "Q3", "Q4"):
        assert q in data, "JSON 里没有 %s，判定被压成布尔了" % q


def test_cli_check_drift_clean():
    r = _cli("--check-drift")
    assert r.returncode == 0, "生成物与代码不同步：" + r.stdout


def test_cli_impact_tool():
    r = _cli("--impact", "test.run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "影响面" in r.stdout and "受影响入口" in r.stdout


def test_cli_shrink_dry_run_does_not_write():
    r = _cli("--shrink", "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "台账" in r.stdout
