#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：**图说的每件事，运行时都要对得上**。

单元测试各自证明"我这块对"；这个文件证明"拼起来也对"：
  图说某条组合的每一步都落在真实能力上 → 运行时必须真能路由到那个原子并调用
  图说某条组合 cli-reachable         → 那条 CLI 命令必须真是个子命令，且真能跑通
  图说某个入口 evidence 指向某函数    → 那个函数必须真的在做声明的事
  门说"有问题就红"                   → 植入缺陷后整条链路（建图→台账→四问）必须判 fail

这一层不追覆盖率，只追一件事：**别让门自己骗自己**。
图从代码现算，如果图和代码对不上，图越漂亮越危险。
"""
import ast
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import atoms_graph_core as core  # noqa: E402
from agent_runtime import AgentRuntime  # noqa: E402
from codeagent import CodeAgent  # noqa: E402

import test_atoms_graph_gate as gate_mini  # noqa: E402  复用迷你仓（同目录，pytest 已加 path）


@pytest.fixture(scope="module")
def graph():
    return core.build_graph(ROOT)


@pytest.fixture(scope="module")
def rt():
    return AgentRuntime()


# ────────────────────────── 图 ↔ 运行时 ──────────────────────────
def test_e2e_every_chain_step_routes_to_live_capability(graph, rt):
    """组合里每一步都必须能被运行时路由到原子、且原子真挂着这条能力。

    图是 AST 抽的，运行时有自己的 _cap_index —— 两者对不上就是"图上说能走，
    实际走不通"，那比没有图更坏。
    """
    missing = []
    for ch in graph["chains"]:
        for st in ch["steps"]:
            cap = st.get("capability")
            if not cap:
                continue
            atom = rt.capability_atom(cap)
            if atom is None:
                missing.append("%s: %s（无归属原子）" % (ch["name"], cap))
                continue
            caps = rt.agents[atom].capabilities()
            if not (caps.get(cap) or {}).get("callable"):
                missing.append("%s: %s（原子 %s 里不可调用）" % (ch["name"], cap, atom))
    assert not missing, "图上可走、运行时走不通：%s" % missing


def test_e2e_signature_snapshot_matches_runtime(graph, rt):
    """签名快照必须是**运行时真实能接的参数**，不能只是 AST 抽的一份说法。"""
    bad = []
    for cap, sig in graph["signatures"]["capabilities"].items():
        atom = rt.capability_atom(cap)
        if atom is None:
            bad.append("%s: 运行时没有这个能力" % cap)
            continue
        acc = rt.agents[atom].accepts(cap)
        if not acc.get("available"):
            continue
        if sorted(acc["params"]) != sorted(sig.get("params") or []):
            bad.append("%s: 快照 %s ≠ 运行时 %s" % (cap, sig.get("params"), acc["params"]))
    assert not bad, bad


def test_e2e_every_declared_capability_is_routable(graph, rt):
    """regersy 声明的每条能力都必须能被运行时路由到（否则"声明"是空的）。"""
    lost = [cap for cap in graph["capability_index"] if rt.capability_atom(cap) is None]
    assert not lost, "registry 里有、运行时路由不到：%s" % lost[:10]


# ────────────────────────── 图 ↔ CLI ──────────────────────────
def _cli_subcommands():
    src = open(os.path.join(ROOT, "codeagent.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "add_parser" and n.args:
            a0 = n.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                out.add(a0.value)
    return out


def test_e2e_entry_commands_are_real_subcommands():
    """入口清单里写的命令必须真实存在（否则"cli-reachable"是编的）。"""
    subs = _cli_subcommands()
    assert "chain" in subs and "graph" in subs, subs
    for e in core.load_entries(ROOT)["entries"]:
        cmd = (e.get("command") or "").split()
        if e.get("kind") == "cli" and cmd and cmd[0] == "codeagent":
            assert cmd[1] in subs, "入口 %s 声明的命令 %r 不是真子命令" % (e["id"], cmd[1])


def test_e2e_cli_graph_matches_core(graph):
    """CLI 的 graph 输出必须与内核现算一致（不许有自己的一套口径）。"""
    r = subprocess.run([sys.executable, os.path.join(ROOT, "codeagent.py"), "graph", "--json"],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    d = json.loads(r.stdout)["graph"]
    m = graph["metrics"]
    assert d["atoms"] == m["atoms"]
    assert d["capabilities"] == m["capabilities"]
    assert d["chains"]["total"] == m["chains"]
    assert d["chains"]["by_reach"] == m["chains_by_reach"]


def test_e2e_cli_chain_template_runs():
    """`chain --template` 真跑到模板表里那条链，且全绿。"""
    r = subprocess.run([sys.executable, os.path.join(ROOT, "codeagent.py"), "chain",
                        "--task", "定位一个导入失败", "--template", "排查/定根因", "--json"],
                       cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stderr[-800:]
    d = json.loads(r.stdout)["chain"]
    assert d["ok"], d.get("error")
    assert d["data"]["verdict"] == "全部通过"
    assert d["data"]["chain"] == ["plan.think", "archreview.layers"]


def test_e2e_cli_chain_unknown_template_fails_loudly():
    """模板名写错要报错并列出可选，不许静默走默认链。"""
    r = subprocess.run([sys.executable, os.path.join(ROOT, "codeagent.py"), "chain",
                        "--task", "x", "--template", "不存在的组合", "--json"],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    d = json.loads(r.stdout)["chain"]
    assert d["ok"] is False
    assert "没有名为" in d["error"]


# ────────────────────────── 组合 ↔ 执行 ──────────────────────────
def test_e2e_template_chain_executes_end_to_end(rt):
    """模板表的链交给运行时能真跑通（占位符解析 + 路由 + 信封全串起来）。"""
    chain, err = rt.default_chain("排查/定根因")
    assert chain, err
    r = rt.run_chain(chain, task="定位一个导入失败",
                     seed={"task": "定位一个导入失败", "language": "python"})
    assert r["ok"], r.get("error")
    assert r["data"]["verdict"] == "全部通过"
    assert set(r["data"]["results"]) == {"plan.think", "archreview.layers"}


def test_e2e_default_chain_via_codeagent_api():
    """`CodeAgent().chain()` 走的必须是模板表里那条默认链（不是代码里的旧链）。"""
    ca = CodeAgent()
    chain, _ = ca.rt.default_chain()
    assert [c.get("capability") for c in chain] == [
        "plan.think", "plan.gen", "codereview.review", "test.run", "evolve.refine"]


def test_e2e_impact_points_at_real_sites(graph):
    """影响面给出的调用点必须真实存在于源码里（否则"改动前先看"看到的是幻觉）。"""
    info = graph["capability_index"]["test.run"]
    assert info["sites"], "test.run 至少有一个调用点"
    for site in info["sites"]:
        rel, _, line = site.rpartition(":")
        path = os.path.join(ROOT, rel)
        assert os.path.isfile(path), site
        lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
        assert 0 < int(line) <= len(lines), "%s 指向的行越界" % site
        assert "run_capability" in lines[int(line) - 1], "%s 那一行不是调用点" % site


# ────────────────────────── 门 ↔ 真实缺陷 ──────────────────────────
def test_e2e_gate_turns_red_on_broken_repo(tmp_path):
    """整条链路（建图 → 台账 → 四问）必须把"有人把入口抄错"判成 fail。

    这一条是把单元级的负向验证串成端到端：植入缺陷后，不是某个函数返回 False，
    而是**门的结论变成 fail** —— 那才是 CI 会拦下来的东西。
    """
    mini = gate_mini._make_mini(tmp_path / "e2e-mini")
    p = mini / "config" / "atoms_entries.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["entries"][0]["evidence"]["symbol"] = "不存在的函数"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    g = core.build_graph(mini)
    assert "G19_ENTRY_STALE" in {f["rule"] for f in g["findings"]}
    # 入口失效 → 组合失去入口 → G14；两条 P1 一起把 Q2 判红
    assert "G14_UNREACHABLE_CHAIN" in {f["rule"] for f in g["findings"]}
    res = core.apply_baseline(g["findings"], {"entries": [], "expires": None})
    qs = core.questions(res["open"], g["metrics"])
    assert qs["Q2"]["level"] == "red", qs["Q2"]
    assert qs["overall"] == "fail"


def test_e2e_gate_passes_on_this_repo():
    """本仓不改任何东西 → 整条门（CLI 全仓判）必须 pass，且四问齐全。

    与上一条成对：一条证明"有缺陷会红"，一条证明"没缺陷不红"。
    只测前者会做出爱误报的门；只测后者会做出永远绿的门。
    """
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify_atoms_graph.py"),
                        "--all"], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-1200:] + r.stderr[-800:]
    assert "结论：通过" in r.stdout
    for q in ("Q1", "Q2", "Q3", "Q4"):
        assert q in r.stdout
