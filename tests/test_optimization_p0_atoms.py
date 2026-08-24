#!/usr/bin/env python3
"""test_optimization_p0_atoms.py — 综合优化方案 P0 新增 3 原子真实数据 pytest。

覆盖：
- 原子契约：registry 加载 ready，provides 齐全可调用
- impact.method — 方法级影响分析（真实符号反向可达 + 传播路径）
- impact.kind — 边类型分类
- deadcode.scan — 死代码扫描（真实目录，输出结构完整）
- doc.anchor / doc.stale — 文档新鲜度（临时构造真实锚点，验证 ok/stale/unresolved 判别）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import agent_loader
import method_impact
import deadcode
import doc_freshness


def _agents():
    r = agent_loader.load_agents()
    assert r["ok"], f"registry 加载失败: {r.get('error')}"
    return r["data"]["agents"]


# ══════════ 原子契约 ══════════
def test_p0_atoms_registered_ready():
    agents = _agents()
    for name in ("method-impact", "deadcode", "doc-freshness"):
        assert name in agents, f"registry 缺 {name}"
        a = agents[name]
        assert a.status == "ready", f"{name} 未 ready: {a.status}"


def test_p0_atoms_provides_callable():
    agents = _agents()
    expect = {
        "method-impact": ["impact.method", "impact.kind"],
        "deadcode": ["deadcode.scan", "deadcode.stats"],
        "doc-freshness": ["doc.anchor", "doc.stale"],
    }
    for name, caps in expect.items():
        a = agents[name]
        for c in caps:
            assert a.capabilities()[c]["callable"], f"{name}.{c} 应可调用"


# ══════════ impact.method — 方法级影响分析 ══════════
def test_impact_method_reverse_reach_real():
    a = _agents()["method-impact"]
    r = a.run(_capability="impact.method", path=REPO_ROOT,
              symbol="method_impact.reverse_reach", transitive=True)
    assert r["ok"], r.get("error")
    d = r["data"]
    # method_impact.reverse_reach 至少被 method_impact.main 调用
    assert "method_impact.main" in d["impact"], f"应含调用者 method_impact.main: {d['impact']}"
    # 传播路径可回溯且以 symbol 收尾
    p = d["paths"].get("method_impact.main")
    assert p and p[-1] == "method_impact.reverse_reach", f"路径未回溯到根: {p}"
    assert d["entities"] > 0 and d["edges"] > 0


def test_impact_method_graph_builds():
    a = _agents()["method-impact"]
    r = a.run(_capability="impact.method", path=REPO_ROOT)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["entities"] > 0 and d["edges"] > 0
    # 方法级图含类方法 fqn（module.Class.method）
    class_method = next((f for f in method_impact.build_graph([REPO_ROOT])["entities"]
                         if f.count(".") >= 2), None)
    assert class_method is not None, "应解析出 module.Class.method 方法级 fqn"


def test_impact_kind_classify():
    a = _agents()["method-impact"]
    # method_impact.main 调用 build_graph → CALLS
    r = a.run(_capability="impact.kind", path=REPO_ROOT,
              caller="method_impact.main", to="method_impact.build_graph")
    assert r["ok"], r.get("error")
    assert "CALLS" in r["data"]["kind"], f"应判 CALLS: {r['data']['kind']}"


# ══════════ deadcode — 死代码检测 ══════════
def test_deadcode_scan_real():
    a = _agents()["deadcode"]
    r = a.run(_capability="deadcode.scan", path=REPO_ROOT)
    assert r["ok"], r.get("error")
    d = r["data"]
    for k in ("total", "live", "dead_count", "ratio", "roots", "dead", "dead_by_file"):
        assert k in d, f"deadcode.scan 缺 {k}"
    assert d["total"] > 0 and d["live"] > 0
    assert isinstance(d["dead"], list) and isinstance(d["dead_by_file"], dict)
    # 入口 roots 非空（应含 main 类入口）
    assert len(d["roots"]) > 0


def test_deadcode_ratio_bounds():
    r = deadcode.scan([REPO_ROOT])
    assert 0.0 <= r["ratio"] <= 1.0, f"ratio 越界: {r['ratio']}"
    assert r["dead_count"] == len(r["dead"])


# ══════════ doc-freshness — 文档新鲜度 ══════════
def test_doc_anchor_ok_stale_unresolved():
    a = _agents()["doc-freshness"]
    with tempfile.TemporaryDirectory() as td:
        # 构造：源文件 + 文档（含一个正常锚点、一个改了内容的 stale、一个文件不存在的 unresolved）
        os.makedirs(os.path.join(td, "src"))
        src = os.path.join(td, "src", "target.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write("def alpha():\n    return 1\n\ndef beta():\n    return 2\n")
        md = os.path.join(td, "doc.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("正常锚点 `repo://src/target.py#L1-2`\n"
                    "符号锚点 `target.alpha`\n"
                    "消失文件 `repo://src/gone.py#L1-1`\n")
        r = a.run(_capability="doc.anchor", path=md, root=td)
        assert r["ok"], r.get("error")
        d = r["data"]
        assert d["anchors"] >= 3
        # 逐锚点核心验证：target.py 存在 → repo 锚点 ok；gone.py 不存在 → unresolved
        items = doc_freshness.extract_anchors(td, open(md, encoding="utf-8").read())
        repo_anchors = [it for it in items if it["type"] == "repo"]
        for it in repo_anchors:
            s = doc_freshness.audit_anchor(td, it)["status"]
            if it["path"] == "src/gone.py":
                assert s == "unresolved", f"gone.py 应 unresolved，实得 {s}"
            else:
                assert s == "ok", f"{it['path']} 应 ok，实得 {s}"
        # 符号锚点 target.alpha ok
        sym_anchors = [it for it in items if it["type"] == "symbol"]
        for it in sym_anchors:
            assert doc_freshness.audit_anchor(td, it)["status"] == "ok"


def test_doc_stale_hash_detection():
    a = _agents()["doc-freshness"]
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "t.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write("x = 1\ny = 2\n")
        md = os.path.join(td, "doc.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("`repo://t.py#L1-2@sha256:00000000`\n")  # 期望哈希与实际不符 → stale
        items = doc_freshness.extract_anchors(td, open(md, encoding="utf-8").read())
        st = doc_freshness.audit_anchor(td, items[0])["status"]
        assert st == "stale", f"期望哈希不符应 stale，实得 {st}"


def test_doc_freshness_audit_dir():
    a = _agents()["doc-freshness"]
    # 对 codeAgent 自身 docs/ 跑审计（真实数据，输出结构完整）
    r = a.run(_capability="doc.stale", path=os.path.join(REPO_ROOT, "docs"), root=REPO_ROOT)
    assert r["ok"], r.get("error")
    d = r["data"]
    for k in ("stale", "unresolved", "stale_count", "unresolved_count"):
        assert k in d, f"doc.stale 缺 {k}"
    assert isinstance(d["stale"], list) and isinstance(d["unresolved"], list)
