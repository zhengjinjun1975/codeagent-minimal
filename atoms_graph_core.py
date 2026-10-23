#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atoms_graph_core.py — 原子能力图谱内核（四层抽取 + 十三条规则 + 基线台账 + 渲染）

为什么要有这件：codeagent 的能力声明（registry.json）、实现（各原子 main.py）、
调用（run_capability）和组合（chain_templates.json / run_chain）是**四份各自为政的真相**。
四份之间没有任何东西对齐，于是「编排写错」只能靠运行时踩坑发现，而踩了坑也不一定看得见
—— `AtomicAgent.call` 的 `except Exception` 会把 TypeError 吞成 `{ok:false, degraded:true}`，
上层只看到"降级"，根因在日志里消失。

四层数据源：
  L0 声明层  registry.json                      provides / depends_on / inputs / outputs
  L1 实现层  agents/*/*/main.py                 AST：register() 真实挂载 + 方法签名
  L2 调用层  全仓 *.py                          AST：run_capability("x.y", ...) 调用点与实参名
  L3 组合层  chain_templates.json + 代码内链    组合 = 有序能力序列，逐步可解析

三条设计约束：
  1 **单一定义**：CI 门、`codeagent.py graph`、单测、文档生成四处都 import 本件，
    不各写一份抽取逻辑（各写一份，本地一个说法、CI 另一个说法，等于没有门）。
  2 **可追踪**：能力有 `trace_id`（原子#能力）、调用点有 `site_id`（文件:行）、
    组合有 `chain_id`。改一个能力，能反查出「哪些组合会断、哪些调用点要跟着改」。
  3 **判不了就说判不了**：取不到基线 ref 时明确返回"未判"，不假装通过。

本件只读代码、不写仓库；写盘由 scripts/verify_atoms_graph.py 决定。
"""

import ast
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

SCHEMA = "codeagent-atoms-graph-v2"
BASELINE_SCHEMA = "atoms-graph-baseline-v1"

# 域 → 六大类（纯展示分组，不参与校验）
GROUP_OF = {
    "approval": "安全治理", "guard": "安全治理", "security": "安全治理",
    "secrets": "安全治理", "sandbox": "安全治理", "locality": "安全治理",
    "codereview": "质量验证", "test": "质量验证", "fuzz": "质量验证",
    "bugdeep": "质量验证", "depscan": "质量验证", "archreview": "质量验证",
    "domain": "质量验证", "ontology": "质量验证", "impact": "质量验证", "atomic": "质量验证",
    "code": "生产编排", "plan": "生产编排", "dispatch": "生产编排",
    "project": "生产编排", "reuse": "生产编排", "skill": "生产编排",
    "evolve": "生产编排", "model": "生产编排", "llm": "生产编排", "subagent": "生产编排",
    "memory": "记忆上下文", "context": "记忆上下文", "session": "记忆上下文",
    "taskstate": "记忆上下文", "docs": "记忆上下文",
    "deliver": "交付运行", "mcp": "交付运行", "web": "交付运行", "tools": "交付运行",
}
GROUP_ORDER = ["安全治理", "质量验证", "生产编排", "记忆上下文", "交付运行", "其他"]

# 能力名前缀与 domain 不一致的**白名单**：domain → 允许出现的异域前缀。
# 建白名单而不改名，是因为改名要连带改全部调用点与对外文档，收益只是整齐（方案 §15 决策 2）。
PREFIX_ALLOWED = {
    "atomic": {"atomicity"},
    "codereview": {"minimal"},
    "docs": {"doc"},
    "impact": {"deadcode"},
    "locality": {"local"},
    "memory": {"event"},
    "tools": {"git"},
    "web": {"browsersmoke", "ui"},
}

# 组合模板静态入参里允许出现的占位符（由 run_chain 解析）
PLACEHOLDERS = {"$task", "$upstream_files"}

# 改这些文件等于改所有原子/所有组合的契约 → 增量判定直接升级为全仓
PUBLIC_LAYER = {
    "atomic_base.py", "agent_runtime.py", "agent_loader.py",
    "registry.json", "capability_scope.py",
}

SKIP_DIR_PARTS = {"node_modules", ".git", "vendor", "__pycache__", ".mypy_cache", ".pytest_cache"}

RULES = {
    "G1_PHANTOM_CALL": ("P0", "调用了 registry 未声明的能力（运行时必然失败）"),
    "G2_UNWIRED_CAPABILITY": ("P2", "未接线：已挂载可调用但全仓零调用（编排覆盖度，非原子缺陷）"),
    "G3_DEP_NOT_EXERCISED": ("P2", "depends_on 声明了，源码里找不到对应调用"),
    "G4_UNDECLARED_DEP": ("P1", "原子实际跨原子调用，但 depends_on 未声明"),
    "G5_CONTRACT_GAP": ("P1", "调用点实参名/必填项与目标签名对不上（TypeError 被吞成 degraded）"),
    "G6_NAMING_DRIFT": ("P3", "能力前缀与 domain 不一致（白名单外）"),
    "G7_CYCLE": ("P0", "depends_on 成环"),
    "G8_ORDER_CONFLICT": ("P3", "registry.order 与依赖先序冲突"),
    "G9_CHAIN_PHANTOM": ("P0", "组合引用了不存在的能力（走到那一步必断）"),
    "G10_CHAIN_INPUT_MISMATCH": ("P1", "组合的静态入参签名接不住（TypeError 被吞成 degraded）"),
    "G11_CHAIN_PLACEHOLDER": ("P2", "组合入参用了未登记的占位符或占位符写错形态"),
    "G12_CAP_NOT_MOUNTED": ("P0", "provides 里声明了，但 main.py 没有 register（调了必失败）"),
    "G13_CAP_UNDECLARED": ("P1", "main.py 挂了能力，但 provides 没声明（对外不可见）"),
}

RULE_GROUP = {
    "G1_PHANTOM_CALL": "A 调用正确性", "G5_CONTRACT_GAP": "A 调用正确性",
    "G12_CAP_NOT_MOUNTED": "A 调用正确性", "G13_CAP_UNDECLARED": "A 调用正确性",
    "G3_DEP_NOT_EXERCISED": "B 依赖一致性", "G4_UNDECLARED_DEP": "B 依赖一致性",
    "G7_CYCLE": "B 依赖一致性", "G8_ORDER_CONFLICT": "B 依赖一致性",
    "G9_CHAIN_PHANTOM": "C 组合可追踪", "G10_CHAIN_INPUT_MISMATCH": "C 组合可追踪",
    "G11_CHAIN_PLACEHOLDER": "C 组合可追踪",
    "G2_UNWIRED_CAPABILITY": "D 覆盖与命名", "G6_NAMING_DRIFT": "D 覆盖与命名",
}


# ────────────────────────── 通用小工具 ──────────────────────────
def _p(*parts):
    return Path(*[str(x) for x in parts])


def load_json(path, default=None):
    """读 JSON；读不到返回 default 并**不**静默（调用方决定要不要报）。
    注意用内置 open：早前那版 scripts/constitution_gate_changed.py 用 io.open 却没 import io，
    NameError 被 except 吞掉，台账永远读不到 → 存量全被当成新增。这里不再踩。"""
    try:
        with open(str(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def classify_group(domain):
    return GROUP_OF.get(domain, "其他")


def _rel(path, repo):
    try:
        return str(Path(path).resolve().relative_to(Path(repo).resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def _iter_py(repo, skip_tests=False):
    repo = Path(repo)
    for p in sorted(repo.rglob("*.py")):
        if any(part in SKIP_DIR_PARTS for part in p.parts):
            continue
        if skip_tests and (p.parent.name == "tests" or str(p).replace("\\", "/").find("/tests/") >= 0):
            continue
        yield p


# ────────────────────────── L0 声明层 ──────────────────────────
def load_registry(repo):
    """registry.json 是 agent_loader.scan() 的**产物**，不是声明源。
    声明源在各自原子 manifest.json —— 所以这里只读不写，dormant 之类要落 manifest。"""
    d = load_json(_p(repo, "registry.json"))
    if not isinstance(d, dict) or not isinstance(d.get("agents"), dict):
        raise SystemExit("做不到读 registry.json（不存在或结构不对）：%s" % _p(repo, "registry.json"))
    return d


def build_owner_index(atoms):
    """返回 atom_of(path) -> 原子名。用最长前缀匹配，避免 code 与 code-review 互相吃掉。"""
    own = sorted(((str(a["path"]).replace("\\", "/").strip("/"), n) for n, a in atoms.items()),
                 key=lambda x: -len(x[0]))

    def atom_of(path):
        s = str(path).replace("\\", "/")
        for prefix, name in own:
            if s == prefix or s.startswith(prefix + "/") or s.startswith(prefix):
                return name
        return None

    return atom_of, own


# ────────────────────────── L1 实现层 ──────────────────────────
def _fn_params(fn):
    """返回 (可接受参数名集合, 是否 **kwargs, 必填位置参数集合)。"""
    a = fn.args
    pos = list(a.posonlyargs) + list(a.args)
    names = {x.arg for x in pos + list(a.kwonlyargs)}
    names.discard("self")
    ndef = len(a.defaults or [])
    required = {x.arg for x in (pos[:len(pos) - ndef] if ndef else pos) if x.arg != "self"}
    return names, (a.kwarg is not None), required


def scan_impl(atom, repo):
    """解析原子入口：能力 → {method, params, required, has_kwargs, opaque}。"""
    entry = _p(repo, atom.get("path", ""), atom.get("entry", "main.py"))
    out = {"capabilities": {}, "error": None}
    if not entry.exists():
        out["error"] = "入口不存在: %s" % entry
        return out
    try:
        tree = ast.parse(entry.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as e:
        out["error"] = "AST 解析失败: %s" % e
        return out

    methods = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[sub.name] = sub

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "register" or len(node.args) < 2:
            continue
        cap = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
        if not isinstance(cap, str):
            continue
        target = node.args[1]
        info = {"opaque": False, "line": node.lineno}
        if isinstance(target, ast.Attribute):
            fn = methods.get(target.attr)
            if fn is not None:
                params, has_kw, required = _fn_params(fn)
                info.update(method=target.attr, params=sorted(params),
                            has_kwargs=has_kw, required=sorted(required))
            else:
                info["opaque"] = True
        else:
            info["opaque"] = True
        out["capabilities"][cap] = info
    return out


# ────────────────────────── L2 调用层 ──────────────────────────
def scan_calls(repo, atom_of):
    """返回调用点列表。用 AST 而不是正则：只有 AST 能拿到调用点实际传了哪些关键字参数
    —— 这正是「编排传错参数 → TypeError → 被信封降级」的根因位置。"""
    rows = []
    for p in _iter_py(repo, skip_tests=True):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "run_capability(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        caller = atom_of(p) or "<编排层>"
        rel = _rel(p, repo)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "run_capability" or not node.args:
                continue
            a0 = node.args[0]
            if not (isinstance(a0, ast.Constant) and isinstance(a0.value, str)):
                continue
            kw, splat = set(), False
            for k in node.keywords:
                if k.arg is None:
                    splat = True
                else:
                    kw.add(k.arg)
            rows.append({
                "site_id": "%s:%d" % (rel, a0.lineno),
                "caller": caller, "capability": a0.value,
                "file": rel, "line": a0.lineno,
                "kwargs": None if splat else sorted(kw),
            })
    return rows


def count_raw_sites(repo):
    """原始 `run_capability(` 出现次数（含动态能力名，AST 抽不出来的那些）。"""
    n = 0
    for p in _iter_py(repo, skip_tests=True):
        try:
            n += p.read_text(encoding="utf-8", errors="ignore").count("run_capability(")
        except OSError:
            pass
    return n


# ────────────────────────── L3 组合层 ──────────────────────────
def _steps_of_list(node):
    """从一个 List 字面量里抽步骤；不是组合（没有 capability 键）返回 []。"""
    steps = []
    for el in node.elts:
        if isinstance(el, ast.Constant) and isinstance(el.value, str):
            steps.append({"capability": el.value, "step": el.value,
                          "inputs": None, "inputs_kind": "none"})
            continue
        if not isinstance(el, ast.Dict):
            steps.append({"capability": None, "step": None,
                          "inputs": None, "inputs_kind": "unresolved"})
            continue
        d = {}
        for k, v in zip(el.keys, el.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                d[k.value] = v
        cap = d.get("capability")
        if not (isinstance(cap, ast.Constant) and isinstance(cap.value, str)):
            steps.append({"capability": None, "step": None,
                          "inputs": None, "inputs_kind": "unresolved"})
            continue
        ins = d.get("inputs")
        static = {}
        kind = "none"
        if isinstance(ins, ast.Dict):
            kind = "static"
            for k, v in zip(ins.keys, ins.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    static[k.value] = (v.value if isinstance(v, ast.Constant) else "$expr")
        elif ins is not None:
            kind = "callable"
        step_name = d.get("step")
        steps.append({
            "capability": cap.value,
            "step": (step_name.value if isinstance(step_name, ast.Constant)
                     and isinstance(step_name.value, str) else cap.value),
            "inputs": static, "inputs_kind": kind,
        })
    return steps if any(s["capability"] for s in steps) else []


def _name_refs(node):
    """把 `chain or default` 这类表达式化成候选名集合。"""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.BoolOp):
        out = []
        for v in node.values:
            out.extend(_name_refs(v))
        return out
    return []


def scan_code_chains(repo, atom_of):
    """抽取代码内写死的组合（局部 list 字面量 + run_chain 引用）。"""
    chains = []
    for p in _iter_py(repo, skip_tests=True):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "run_chain(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = _rel(p, repo)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            locals_ = {}
            for st in ast.walk(fn):
                if isinstance(st, ast.Assign) and isinstance(st.value, ast.List):
                    steps = _steps_of_list(st.value)
                    if steps:
                        for tgt in st.targets:
                            if isinstance(tgt, ast.Name):
                                locals_[tgt.id] = (steps, st.lineno)
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                    continue
                if call.func.attr != "run_chain" or not call.args:
                    continue
                refs = _name_refs(call.args[0])
                hit = next((r for r in refs if r in locals_), None)
                cid = "code:%s#%s:%d" % (rel, fn.name, call.lineno)
                if hit:
                    steps, def_line = locals_[hit]
                    chains.append({
                        "chain_id": cid, "kind": "code", "name": "%s.%s" % (rel, fn.name),
                        "file": rel, "line": def_line, "call_line": call.lineno,
                        "steps": steps, "dynamic": False, "reason": None,
                        "owner": atom_of(p) or "<编排层>",
                    })
                elif any(isinstance(a, ast.Constant) and a.value is None for a in call.args):
                    continue  # run_chain(None) = 自动选链，交给模板表，不算写死的组合
                else:
                    chains.append({
                        "chain_id": cid, "kind": "code", "name": "%s.%s" % (rel, fn.name),
                        "file": rel, "line": call.lineno, "call_line": call.lineno,
                        "steps": [], "dynamic": True,
                        "reason": "组合由运行时动态给出（非字面量），静态抽不出步骤",
                        "owner": atom_of(p) or "<编排层>",
                    })
    return chains


def find_template_file(repo):
    for p in sorted(_p(repo, "agents").rglob("chain_templates.json")):
        if "__pycache__" not in str(p):
            return p
    return None


def load_template_chains(repo):
    """组合模板表：声明式组合的主要来源。每条模板 = 一条可追踪的组合。"""
    f = find_template_file(repo)
    if f is None:
        return [], None
    d = load_json(f, {}) or {}
    tpls = d.get("templates") or []
    chains = []
    rel = _rel(f, repo)
    for i, t in enumerate(tpls):
        steps = []
        for st in (t.get("chain") or []):
            if isinstance(st, str):
                steps.append({"capability": st, "step": st, "inputs": {}, "inputs_kind": "none"})
            elif isinstance(st, dict):
                ins = st.get("inputs") or {}
                steps.append({"capability": st.get("capability"), "step": st.get("step") or st.get("capability"),
                              "inputs": dict(ins) if isinstance(ins, dict) else {},
                              "inputs_kind": "static" if isinstance(ins, dict) and ins else "none"})
            else:
                steps.append({"capability": None, "step": None, "inputs": {}, "inputs_kind": "unresolved"})
        chains.append({
            "chain_id": "tpl:%s#%d" % (rel, i), "kind": "template",
            "name": t.get("name") or ("模板 %d" % (i + 1)),
            "file": rel, "line": 0, "call_line": 0, "steps": steps,
            "dynamic": False, "reason": None,
            "keywords": list(t.get("keywords") or []),
            "note": t.get("note") or "",
        })
    return chains, rel


# ────────────────────────── 建图 + 体检 ──────────────────────────
def build_graph(repo):
    repo = Path(repo).resolve()
    reg = load_registry(repo)
    atoms = reg["agents"]
    atom_of, _own = build_owner_index(atoms)
    prov = {c: n for n, a in atoms.items() for c in a["provides"]}

    impls = {n: scan_impl(a, repo) for n, a in atoms.items()}
    calls = scan_calls(repo, atom_of)
    raw_sites = count_raw_sites(repo)
    for c in calls:
        c["resolved"] = c["capability"] in prov
        c["atom"] = prov.get(c["capability"])

    code_chains = scan_code_chains(repo, atom_of)
    tpl_chains, tpl_rel = load_template_chains(repo)
    chains = tpl_chains + code_chains

    # ── 组合逐步解析（这是「组合可追踪」的核心）──
    for ch in chains:
        for i, st in enumerate(ch["steps"], 1):
            cap = st.get("capability")
            st["order"] = i
            st["atom"] = prov.get(cap)
            st["resolved"] = cap in prov
            st["trace_id"] = "cap:%s" % cap if st["resolved"] else None
            info = impls.get(st["atom"], {}).get("capabilities", {}).get(cap) if st["atom"] else None
            st["sig_checked"] = bool(info and not info.get("opaque") and not info.get("has_kwargs"))
            if st["inputs_kind"] == "static" and st["sig_checked"]:
                accepted = set(info["params"])
                st["sig_bad"] = sorted(k for k in st["inputs"] if k not in accepted)
            else:
                st["sig_bad"] = []

    call_edges = Counter((c["caller"], c["capability"]) for c in calls)
    cap_calls = Counter(c["capability"] for c in calls)

    # 「被调到」有两条路：直接 run_capability，或进入某条组合的某一步。
    # 只算前者会把「只经组合调用」的能力误判成未接线 —— 那正好和本图谱的目的相反。
    chain_used = {}
    for ch in chains:
        for st in ch["steps"]:
            if st.get("resolved") and not ch["dynamic"]:
                chain_used.setdefault(st["capability"], []).append(ch["chain_id"])

    def reached(cap):
        return cap_calls.get(cap, 0) > 0 or cap in chain_used

    # 能力反查索引：能力 → 调用点 / 所在组合（改动影响面）
    cap_index = {}
    for cap, atom in prov.items():
        cap_index[cap] = {"atom": atom, "sites": [], "chains": []}
    for c in calls:
        if c["capability"] in cap_index:
            cap_index[c["capability"]]["sites"].append(c["site_id"])
    for ch in chains:
        for st in ch["steps"]:
            if st.get("capability") in cap_index:
                cap_index[st["capability"]]["chains"].append(
                    {"chain_id": ch["chain_id"], "name": ch["name"], "order": st["order"]})

    nodes = []
    for n, a in atoms.items():
        impl = impls[n]
        registered = sorted(impl["capabilities"])
        nodes.append({
            "id": n, "trace_prefix": "atom:%s" % n,
            "domain": a["domain"], "group": classify_group(a["domain"]),
            "version": a.get("version"), "provides": list(a["provides"]),
            "depends_on": list(a.get("depends_on") or []),
            "inputs": list(a.get("inputs") or []), "outputs": list(a.get("outputs") or []),
            "registered": registered,
            "mounted_all": bool(registered) and set(registered) >= set(a["provides"]),
            "complete": bool(registered) and set(registered) == set(a["provides"]),
            "wired": any(reached(c) for c in a["provides"]),
            "ncall": sum(cap_calls.get(c, 0) for c in a["provides"]),
            "nchain": sum(len(chain_used.get(c, [])) for c in a["provides"]),
            "path": str(a.get("path", "")).replace("\\", "/"),
            "impl_error": impl["error"],
        })
    node_by_id = {n["id"]: n for n in nodes}

    declared_deps = []
    for n, a in atoms.items():
        for dp in (a.get("depends_on") or []):
            declared_deps.append((n, prov.get(dp), dp))

    edges = []
    for (src, cap), cnt in sorted(call_edges.items(), key=lambda x: (-x[1], x[0])):
        edges.append({"from": src, "to": prov.get(cap), "capability": cap,
                      "kind": "call", "count": cnt, "resolved": cap in prov})
    for src, dst, cap in declared_deps:
        edges.append({"from": src, "to": dst, "capability": cap, "kind": "declared_dep",
                      "count": 1, "resolved": dst is not None})

    findings = []

    def add(rule, subject, detail, file=None, line=None, chain_id=None, atom=None):
        sev = RULES[rule][0]
        key = rule + "|" + re.sub(r":\d+", "", subject)
        findings.append({"rule": rule, "severity": sev, "group": RULE_GROUP[rule],
                         "subject": subject, "detail": detail, "key": key,
                         "file": file, "line": line, "chain_id": chain_id, "atom": atom})

    # G1 幻影调用
    for c in calls:
        if not c["resolved"]:
            add("G1_PHANTOM_CALL", "%s → %s" % (c["caller"], c["capability"]),
                "%s 调用了未声明的能力" % c["site_id"],
                file=c["file"], line=c["line"], atom=None if c["caller"] == "<编排层>" else c["caller"])

    # G2 未接线
    for n, a in atoms.items():
        for c in a["provides"]:
            if not reached(c):
                add("G2_UNWIRED_CAPABILITY", "%s · %s" % (n, c),
                    "已挂载可调用，但既无调用点、也不在任何组合里（待接线）", atom=n)

    # G3 声明依赖未走
    for src, dst, cap in declared_deps:
        if call_edges.get((src, cap), 0) == 0:
            add("G3_DEP_NOT_EXERCISED", "%s → %s" % (src, cap),
                "depends_on 声明了，源码中找不到对应调用", atom=src)

    # G4 未声明跨原子调用
    decl_pairs = {(s, c) for s, _d, c in declared_deps}
    for (src, cap), cnt in sorted(call_edges.items()):
        if src == "<编排层>" or prov.get(cap) == src:
            continue
        if (src, cap) not in decl_pairs:
            add("G4_UNDECLARED_DEP", "%s → %s" % (src, cap),
                "原子实际调用了外部能力 ×%d，但 depends_on 未声明" % cnt, atom=src)

    # G5 调用点契约错位
    for c in calls:
        dst = prov.get(c["capability"])
        if not dst:
            continue
        info = impls[dst]["capabilities"].get(c["capability"])
        if not info or info.get("opaque") or info.get("has_kwargs"):
            continue
        accepted = set(info["params"])
        if c["kwargs"]:
            bad = sorted(set(c["kwargs"]) - accepted)
            if bad:
                add("G5_CONTRACT_GAP", "%s → %s" % (c["site_id"], c["capability"]),
                    "传了 %s，但 %s.%s() 只接受 %s" % (bad, dst, info.get("method"), sorted(accepted)),
                    file=c["file"], line=c["line"], atom=dst)
        if c["kwargs"] is not None:
            lack = sorted(set(info.get("required") or []) - set(c["kwargs"]))
            if lack:
                add("G5_CONTRACT_GAP", "%s → %s" % (c["site_id"], c["capability"]),
                    "未提供必填参数 %s" % lack,
                    file=c["file"], line=c["line"], atom=dst)

    # G6 命名漂移（白名单外）
    for n, a in atoms.items():
        allowed = PREFIX_ALLOWED.get(a["domain"], set())
        for c in a["provides"]:
            pref = c.split(".")[0]
            if pref != a["domain"] and pref not in allowed:
                add("G6_NAMING_DRIFT", "%s · %s" % (n, c),
                    "前缀 %r ≠ domain %r，且不在白名单" % (pref, a["domain"]), atom=n)

    # G7 依赖成环
    adj = defaultdict(list)
    for src, dst, _cap in declared_deps:
        if dst:
            adj[src].append(dst)
    color = {}

    def dfs(u, stack):
        color[u] = 1
        for v in adj[u]:
            if color.get(v) == 1:
                add("G7_CYCLE", "%s → %s" % (u, v),
                    "depends_on 成环：" + " → ".join(stack + [v]), atom=u)
            elif color.get(v, 0) == 0:
                dfs(v, stack + [v])
        color[u] = 2

    for n in sorted(atoms):
        if color.get(n, 0) == 0:
            dfs(n, [n])

    # G8 order 与依赖先序冲突
    order = reg.get("order") or []
    pos = {n: i for i, n in enumerate(order)}
    for src, dst, _cap in declared_deps:
        if dst and pos.get(src, 0) < pos.get(dst, 1 << 30):
            add("G8_ORDER_CONFLICT", "%s → %s" % (src, dst),
                "order 里 %s(#%d) 排在提供者 %s(#%d) 之前"
                % (src, pos.get(src), dst, pos.get(dst)), atom=src)

    # G9/G10/G11 组合层
    for ch in chains:
        for st in ch["steps"]:
            cap = st.get("capability")
            if not cap:
                add("G9_CHAIN_PHANTOM", "%s 第%d步" % (ch["name"], st.get("order", 0)),
                    "组合里这一步的能力名抽不出来（非字面量），无法追踪",
                    file=ch["file"], line=ch["line"] or None, chain_id=ch["chain_id"])
                continue
            if not st["resolved"]:
                add("G9_CHAIN_PHANTOM", "%s 第%d步 → %s" % (ch["name"], st["order"], cap),
                    "组合引用了 registry 里不存在的能力，走到这一步必断",
                    file=ch["file"], line=ch["line"] or None, chain_id=ch["chain_id"])
            if st["sig_bad"]:
                add("G10_CHAIN_INPUT_MISMATCH", "%s 第%d步 → %s" % (ch["name"], st["order"], cap),
                    "静态入参 %s 不在方法签名里，运行时 TypeError 会被吞成 degraded" % st["sig_bad"],
                    file=ch["file"], line=ch["line"] or None, chain_id=ch["chain_id"], atom=st["atom"])
            for k, v in (st.get("inputs") or {}).items():
                if isinstance(v, str) and v.startswith("$") and v not in PLACEHOLDERS:
                    add("G11_CHAIN_PLACEHOLDER", "%s 第%d步 → %s" % (ch["name"], st["order"], k),
                        "占位符 %r 未登记（只认 %s）" % (v, sorted(PLACEHOLDERS)),
                        file=ch["file"], line=ch["line"] or None, chain_id=ch["chain_id"])

    # G12/G13 声明与挂载一致
    for n, a in atoms.items():
        impl = impls[n]["capabilities"]
        for c in a["provides"]:
            if c not in impl:
                add("G12_CAP_NOT_MOUNTED", "%s · %s" % (n, c),
                    "provides 声明了，但 %s 里没有 register，调了必失败" % a.get("entry", "main.py"), atom=n)
        for c in impl:
            if c not in a["provides"]:
                add("G13_CAP_UNDECLARED", "%s · %s" % (n, c),
                    "main.py 挂了这条能力，但 provides 没声明（对外不可见）", atom=n)

    total_caps = sum(len(a["provides"]) for a in atoms.values())
    reached_cnt = len({c for c in prov if reached(c)})
    chain_only = sorted(c for c in prov if cap_calls.get(c, 0) == 0 and c in chain_used)
    atom_level = [e for e in edges if e["kind"] == "call" and e["from"] != "<编排层>"]
    mounted = sum(len(x["registered"]) for x in nodes)
    complete = sum(1 for x in nodes if x["complete"])

    metrics = {
        "atoms": len(atoms),
        "capabilities": total_caps,
        "capabilities_mounted": mounted,
        "atoms_complete": complete,
        "atoms_wired": sum(1 for x in nodes if x["wired"]),
        "capabilities_reached": reached_cnt,
        "capability_coverage": round(reached_cnt / total_caps, 4) if total_caps else 0.0,
        "capabilities_chain_only": len(chain_only),
        "call_sites": len(calls),
        "call_sites_raw": raw_sites,
        "call_sites_dynamic": max(0, raw_sites - len(calls)),
        "declared_dep_edges": len(declared_deps),
        "declared_dep_exercised": sum(1 for s, _d, c in declared_deps if call_edges.get((s, c), 0)),
        "atom_level_call_edges": len(atom_level),
        "topology": "star" if not atom_level else "mixed",
        "chains": len(chains),
        "chain_steps": sum(len(c["steps"]) for c in chains),
        "chains_dynamic": sum(1 for c in chains if c["dynamic"]),
        "capabilities_in_chains": len({st["capability"] for c in chains for st in c["steps"] if st.get("capability")}),
        "rules_fired": dict(sorted(Counter(f["rule"] for f in findings).items())),
        "severity": dict(sorted(Counter(f["severity"] for f in findings).items())),
    }

    return {
        "schema": SCHEMA,
        "nodes": nodes,
        "edges": edges,
        "call_sites": calls,
        "chains": chains,
        "chain_source": tpl_rel,
        "capability_index": cap_index,
        "findings": sorted(findings, key=lambda f: (f["rule"], f["subject"])),
        "metrics": metrics,
    }


# ────────────────────────── 基线台账 ──────────────────────────
def baseline_path(repo):
    return _p(repo, "config", "atoms_graph_baseline.json")


def load_baseline(repo):
    d = load_json(baseline_path(repo))
    if not isinstance(d, dict):
        return {"schema": BASELINE_SCHEMA, "expires": None, "entries": [], "_missing": True}
    d.setdefault("entries", [])
    d.setdefault("_missing", False)
    return d


def apply_baseline(findings, bl, today=None):
    """三条纪律：① 每条基线必须有 reason + expires ② 过期即红 ③ 条目失效要报删除。
    缺一条，台账就会烂成永久豁免 —— 那是比没有台账更坏的结果。"""
    today = today or date.today()
    idx = {}
    for e in bl.get("entries") or []:
        idx.setdefault(e.get("key"), []).append(e)

    waived, open_, problems = [], [], []
    used = set()
    for f in findings:
        ent = None
        for cand in idx.get(f["key"], []):
            if id(cand) not in used:
                ent = cand
                used.add(id(cand))
                break
        if ent is None:
            open_.append(f)
            continue
        exp = ent.get("expires")
        if not ent.get("reason"):
            problems.append({"rule": "BASELINE_NO_REASON", "severity": "P1",
                             "subject": f["subject"], "detail": "基线条目没有写理由"})
        if exp and str(exp) < today.isoformat():
            problems.append({"rule": "BASELINE_EXPIRED", "severity": "P1",
                             "subject": f["subject"], "detail": "基线已于 %s 到期，须修掉或续期并写清原因" % exp})
        waived.append(dict(f, baseline_reason=ent.get("reason"), baseline_expires=exp))

    for key, ents in idx.items():
        for ent in ents:
            if id(ent) in used:
                continue
            problems.append({"rule": "BASELINE_STALE", "severity": "P2",
                             "subject": ent.get("subject") or key,
                             "detail": "这条基线已经没有对应问题了，请从台账删掉（否则台账会掩盖新问题）"})

    top = bl.get("expires")
    if top and str(top) < today.isoformat():
        problems.append({"rule": "BASELINE_EXPIRED", "severity": "P1",
                         "subject": "整本台账 %s" % top,
                         "detail": "台账整体已到期，须重新梳理存量，不许无限续期"})
    return {"waived": waived, "open": open_, "problems": problems}


def dump_baseline(graph, reason_default, expires, today=None):
    """从当前体检结果生成台账（只记 P2/P3；P0/P1 不许进台账，必须当场修）。"""
    today = today or date.today()
    entries = []
    for f in graph["findings"]:
        if f["severity"] not in ("P2", "P3"):
            continue
        entries.append({"key": f["key"], "rule": f["rule"], "subject": f["subject"],
                        "severity": f["severity"], "reason": reason_default, "expires": expires})
    return {"schema": BASELINE_SCHEMA, "_note": "只登记 P2/P3 存量；P0/P1 一律必须当场修，不进台账。"
                                               "每条必须有 reason 与 expires，过期即红，条目失效要删。",
            "expires": expires, "entries": entries}


# ────────────────────────── 增量范围 ──────────────────────────
def _git(repo, *args):
    try:
        r = subprocess.run(["git"] + list(args), cwd=str(repo), capture_output=True,
                           text=True, errors="ignore", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, (r.stdout or "").strip()


def pick_base(repo, explicit=None):
    null_sha = "0" * 40
    for cand in (explicit, os.environ.get("ATOMS_GRAPH_BASE")):
        if cand and cand.strip() and cand.strip() != null_sha:
            rc, _ = _git(repo, "rev-parse", "--verify", cand.strip() + "^{commit}")
            if rc == 0:
                return cand.strip(), "参数/环境变量"
    rc, _ = _git(repo, "rev-parse", "--verify", "origin/HEAD^{commit}")
    if rc == 0:
        return "origin/HEAD", "默认(origin/HEAD)"
    rc, _ = _git(repo, "rev-parse", "--verify", "HEAD~1^{commit}")
    if rc == 0:
        return "HEAD~1", "默认(HEAD~1)"
    return None, "取不到基线"


def changed_files(repo, base):
    files = set()
    for args in (("diff", "--name-only", "--diff-filter=ACMR", base, "HEAD"),
                 ("diff", "--name-only", "--diff-filter=ACMR"),
                 ("diff", "--name-only", "--cached", "--diff-filter=ACMR")):
        rc, out = _git(repo, *args)
        if rc == 0:
            files.update(x.strip() for x in out.splitlines() if x.strip())
    return sorted(files)


def scope(findings, changed, atoms):
    """增量判定：只判改动波及的原子/文件；改公共层则全仓。返回 (在范围, 出范围, 是否全仓, 原因)。"""
    if changed is None:
        return list(findings), [], True, "未判增量（--all 或取不到基线）"
    touched = {c for c in changed if os.path.basename(c) in PUBLIC_LAYER}
    if touched:
        return list(findings), [], True, "改了公共层 %s → 全仓进入硬红区间" % sorted(touched)

    changed_atoms, changed_files_set = set(), set(changed)
    for rel in changed:
        for name, a in atoms.items():
            prefix = str(a.get("path", "")).replace("\\", "/").strip("/")
            if prefix and (rel == prefix or rel.startswith(prefix + "/")):
                changed_atoms.add(name)

    inside, outside = [], []
    for f in findings:
        hit = False
        if f.get("file") and f["file"] in changed_files_set:
            hit = True
        if f.get("atom") and f["atom"] in changed_atoms:
            hit = True
        if f.get("chain_id"):
            ch_file = f["chain_id"].split(":")[1].split("#")[0] if f["chain_id"].count(":") >= 1 else ""
            if ch_file in changed_files_set:
                hit = True
        if f.get("atom") is None and f.get("file") is None and not f.get("chain_id"):
            hit = True  # 无法定位到文件的全局问题（如 order 冲突）一律判
        (inside if hit else outside).append(f)
    return inside, outside, False, "改动 %d 个文件，波及 %d 个原子" % (len(changed), len(changed_atoms))


# ────────────────────────── 渲染：Markdown ──────────────────────────
NO_HANDEDIT = "> 本文件由 `scripts/verify_atoms_graph.py --emit` 从代码生成，**请勿手写修改**。\n" \
              "> CI 用 `--check-drift` 比对：生成物与代码不同步即红。改图请改代码再重新生成。\n"


def _fmt_inputs(st):
    if st.get("inputs_kind") == "callable":
        return "λ 动态"
    if not st.get("inputs"):
        return "—"
    return " ".join("%s=%s" % (k, v) for k, v in sorted(st["inputs"].items()))


def render_markdown(graph):
    m = graph["metrics"]
    L = ["# 原子能力图谱（生成物）", "", NO_HANDEDIT, ""]
    L.append("完整性的环图快照见 `docs/atoms_graph_snapshot_<日期>.html`（当时生成的静态图，非本门产物）；本文件是**组合追踪**视图，两者同源不同视角。\n")

    L.append("## 一、概览\n")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append("| 原子 / 能力 | **%d** / **%d** |" % (m["atoms"], m["capabilities"]))
    L.append("| 能力挂载 | **%d/%d**（声明=挂载的原子 %d/%d） |"
             % (m["capabilities_mounted"], m["capabilities"], m["atoms_complete"], m["atoms"]))
    L.append("| 有调用边的原子 | **%d/%d** |" % (m["atoms_wired"], m["atoms"]))
    L.append("| 能力覆盖率 | **%.1f%%**（%d/%d） |"
             % (m["capability_coverage"] * 100, m["capabilities_reached"], m["capabilities"]))
    L.append("| 调用点 | **%d** 处（可静态解析 %d，动态能力名 %d） |"
             % (m["call_sites_raw"], m["call_sites"], m["call_sites_dynamic"]))
    L.append("| 组合 | **%d** 条 / **%d** 步（其中 %d 条组合静态抽不出步骤） |"
             % (m["chains"], m["chain_steps"], m["chains_dynamic"]))
    L.append("| 进入组合的能力 | **%d/%d** |" % (m["capabilities_in_chains"], m["capabilities"]))
    L.append("| 原子↔原子调用边 | %d（拓扑 %s） |" % (m["atom_level_call_edges"], m["topology"]))
    L.append("")

    L.append("## 二、组合清单（每一步都能落到真实能力）\n")
    L.append("组合 = 有序能力序列。下表每条组合的每一步都给出：能力名 → 所在原子 → 静态入参 → 定义位置。")
    L.append("`未解析` 的行就是会断的地方。\n")
    for ch in graph["chains"]:
        head = "### %s" % ch["name"]
        if ch["kind"] == "template":
            head += "（模板）"
        elif ch["kind"] == "code":
            head += "（代码内）"
        L.append(head)
        L.append("")
        L.append("- 组合 ID：`%s`" % ch["chain_id"])
        L.append("- 定义位置：`%s`%s" % (ch["file"], (":%d" % ch["line"]) if ch["line"] else ""))
        if ch.get("keywords"):
            L.append("- 命中关键词：%s" % "、".join(ch["keywords"]))
        if ch["dynamic"]:
            L.append("- **静态抽不出步骤**：%s" % ch["reason"])
        L.append("")
        if ch["steps"]:
            L.append("| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |")
            L.append("|---|---|---|---|---|---|")
            for st in ch["steps"]:
                ok = "✅" if st.get("resolved") else "❌ 未解析"
                if st.get("sig_bad"):
                    ok = "⚠ 入参不符"
                L.append("| %d | %s | `%s` | %s | `%s` | %s |"
                         % (st.get("order", 0), st.get("step") or "—", st.get("capability") or "?",
                            st.get("atom") or "—", _fmt_inputs(st), ok))
            L.append("")

    L.append("## 三、能力 → 组合 反查（改动影响面）\n")
    L.append("改某条能力之前，先在这里查它被哪些组合用到、哪些调用点依赖它。")
    L.append("`调用点 0 / 组合 0` 的就是未接线能力。\n")
    L.append("| 能力 | 原子 | 调用点 | 被哪些组合用到 |")
    L.append("|---|---|---|---|")
    rows = []
    for cap, info in graph["capability_index"].items():
        rows.append((len(info["sites"]), len(info["chains"]), cap, info))
    rows.sort(key=lambda x: (-x[0], -x[1], x[2]))
    for nsite, nchain, cap, info in rows:
        chains = "、".join("%s 第%d步" % (c["name"], c["order"]) for c in info["chains"]) or "—"
        L.append("| `%s` | %s | %d | %s |" % (cap, info["atom"], nsite, chains))
    L.append("")

    L.append("## 四、规则命中\n")
    L.append("| 组 | 规则 | 级别 | 命中 | 说明 |")
    L.append("|---|---|---|---|---|")
    last_group = None
    for rule, (sev, desc) in RULES.items():
        grp = RULE_GROUP[rule]
        n = m["rules_fired"].get(rule, 0)
        g = grp if grp != last_group else ""
        last_group = grp
        L.append("| %s | `%s` | %s | %s | %s |" % (g, rule, sev, ("**%d**" % n) if n else "0", desc))
    L.append("")

    L.append("## 五、问题明细\n")
    by_rule = defaultdict(list)
    for f in graph["findings"]:
        by_rule[f["rule"]].append(f)
    if not graph["findings"]:
        L.append("（无）\n")
    for rule, (sev, desc) in RULES.items():
        items = by_rule.get(rule) or []
        if not items:
            continue
        L.append("### %s（%d 项，%s）\n" % (rule, len(items), sev))
        for f in items[:60]:
            L.append("- `%s` — %s" % (f["subject"], f["detail"]))
        if len(items) > 60:
            L.append("- …… 另 %d 项见 `--json` 输出" % (len(items) - 60))
        L.append("")
    return "\n".join(L)


# ────────────────────────── 渲染：HTML 一张图 ──────────────────────────
GROUP_COLOR = {
    "安全治理": ("#FCEBEB", "#A32D2D"),
    "质量验证": ("#E6F1FB", "#185FA5"),
    "生产编排": ("#E1F5EE", "#0F6E56"),
    "记忆上下文": ("#FAEEDA", "#854F0B"),
    "交付运行": ("#EEEDFE", "#534AB7"),
    "其他": ("#F1EFE8", "#5F5E5A"),
}

_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:28px 32px 48px;font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  color:#1f2937;background:#fff;font-size:13px;line-height:1.6}
h1{font-size:19px;font-weight:500;margin:0 0 4px}
h2{font-size:15px;font-weight:500;margin:30px 0 10px;padding-bottom:6px;border-bottom:1px solid #e5e7eb}
.sub{color:#6b7280;font-size:12px;margin:0 0 18px}
.cards{display:flex;gap:10px;flex-wrap:wrap}
.card{flex:1;min-width:132px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px}
.card b{display:block;font-size:20px;font-weight:500;line-height:1.2}
.card span{color:#6b7280;font-size:11.5px}
.grid{display:flex;flex-wrap:wrap;gap:6px}
.atom{display:inline-block;border:1px solid;border-radius:7px;padding:3px 8px;font-size:11.5px;
  font-family:ui-monospace,Consolas,monospace}
.atom i{font-style:normal;font-size:10px;opacity:.65;margin-left:4px}
.grp{margin:10px 0 4px;color:#6b7280;font-size:11.5px}
.chain{border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px;margin-bottom:12px;background:#fff}
.chain-h{display:flex;align-items:baseline;gap:8px;margin-bottom:10px}
.chain-h b{font-weight:500;font-size:13.5px}
.chain-h code{color:#6b7280;font-size:11px}
.flow{display:flex;align-items:stretch;gap:0;flex-wrap:wrap}
.step{border:1px solid;border-radius:8px;padding:6px 10px;min-width:132px;font-size:11.5px}
.step small{display:block;color:#6b7280;font-size:10.5px;font-family:ui-monospace,Consolas,monospace}
.arrow{align-self:center;color:#9ca3af;padding:0 6px;font-size:12px}
.bad{border-color:#E24B4A;background:#FCEBEB}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #eef0f2}
th{color:#6b7280;font-weight:500;background:#fafafa}
code{font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
.tag{display:inline-block;border-radius:4px;padding:1px 6px;font-size:10.5px;font-family:ui-monospace,Consolas,monospace}
"""


def _atom_chip(n):
    fill, stroke = GROUP_COLOR.get(n["group"], GROUP_COLOR["其他"])
    style = "background:%s;border-color:%s;color:%s" % (fill, stroke, stroke)
    if n["wired"]:
        style += ";font-weight:500"
    else:
        style += ";border-style:dashed;opacity:.85"
    extra = "" if n["complete"] else " ⚠"
    return ('<span class="atom" style="%s" title="%s｜%s｜能力 %d｜调用 %d">%s<i>%d</i>%s</span>'
            % (style, n["id"], n["domain"], len(n["provides"]), n["ncall"], n["id"], len(n["provides"]), extra))


def render_html(graph):
    m = graph["metrics"]
    P = []
    P.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    P.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    P.append("<title>原子能力图谱 · codeagent-minimal</title>")
    P.append("<style>%s</style></head><body>" % _CSS)
    P.append("<h1>原子能力图谱</h1>")
    P.append('<p class="sub">由 <code>scripts/verify_atoms_graph.py --emit</code> 从代码生成，'
             "请勿手写。改图请改代码后重新生成。</p>")

    P.append('<div class="cards">')
    for label, val in (("原子", m["atoms"]), ("能力", m["capabilities"]),
                       ("能力覆盖", "%.1f%%" % (m["capability_coverage"] * 100)),
                       ("组合 / 步", "%d / %d" % (m["chains"], m["chain_steps"])),
                       ("有调用边原子", "%d/%d" % (m["atoms_wired"], m["atoms"])),
                       ("问题", sum(m["rules_fired"].values()))):
        P.append('<div class="card"><b>%s</b><span>%s</span></div>' % (val, label))
    P.append("</div>")

    P.append("<h2>一、全部原子（完整性）</h2>")
    P.append('<p class="sub">实线=有调用路径，虚线=待接线（插槽就绪，不是残件）。'
             "数字是能力条数，⚠ 表示挂载与声明不一致。</p>")
    by_group = defaultdict(list)
    for n in graph["nodes"]:
        by_group[n["group"]].append(n)
    for g in GROUP_ORDER:
        items = sorted(by_group.get(g) or [], key=lambda x: x["id"])
        if not items:
            continue
        wired = sum(1 for x in items if x["wired"])
        P.append('<div class="grp">%s　%d 个在位 · %d 已接线</div>' % (g, len(items), wired))
        P.append('<div class="grid">%s</div>' % "".join(_atom_chip(n) for n in items))

    P.append("<h2>二、组合链（组合可追踪）</h2>")
    P.append('<p class="sub">每条组合的每一步都落到真实能力；红框 = 这一步解析不到能力（走到这里必断）。</p>')
    for ch in graph["chains"]:
        P.append('<div class="chain">')
        kind = "模板" if ch["kind"] == "template" else "代码内"
        P.append('<div class="chain-h"><b>%s</b><span class="tag" style="background:#f3f4f6;color:#6b7280">%s</span>'
                 '<code>%s</code></div>' % (ch["name"], kind, ch["chain_id"]))
        if ch["dynamic"]:
            P.append('<div class="sub">静态抽不出步骤：%s</div>' % ch["reason"])
        else:
            P.append('<div class="flow">')
            for i, st in enumerate(ch["steps"]):
                fill, stroke = GROUP_COLOR["其他"]
                cls = "step"
                if not st.get("resolved"):
                    cls += " bad"
                    fill, stroke = "#FCEBEB", "#A32D2D"
                else:
                    atom = st.get("atom")
                    if atom:
                        node = next((x for x in graph["nodes"] if x["id"] == atom), None)
                        if node:
                            fill, stroke = GROUP_COLOR.get(node["group"], GROUP_COLOR["其他"])
                P.append('<div class="%s" style="background:%s;border-color:%s">%s<small>%s</small></div>'
                         % (cls, fill, stroke, st.get("capability") or "?", st.get("atom") or "未解析"))
                if i < len(ch["steps"]) - 1:
                    P.append('<div class="arrow">→</div>')
            P.append("</div>")
        P.append("</div>")

    P.append("<h2>三、能力被哪些组合用到（反查）</h2>")
    rows = [(len(v["sites"]), len(v["chains"]), k, v) for k, v in graph["capability_index"].items()]
    rows.sort(key=lambda x: (-x[0], -x[1], x[2]))
    P.append("<table><tr><th>能力</th><th>原子</th><th>调用点</th><th>被哪些组合用到</th></tr>")
    for nsite, nchain, cap, info in rows[:45]:
        chains = "、".join("%s 第%d步" % (c["name"], c["order"]) for c in info["chains"]) or "—"
        P.append("<tr><td><code>%s</code></td><td>%s</td><td>%d</td><td>%s</td></tr>"
                 % (cap, info["atom"], nsite, chains))
    P.append("</table>")

    P.append("<h2>四、规则命中</h2>")
    P.append("<table><tr><th>组</th><th>规则</th><th>级别</th><th>命中</th><th>说明</th></tr>")
    for rule, (sev, desc) in RULES.items():
        n = m["rules_fired"].get(rule, 0)
        P.append("<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>"
                 % (RULE_GROUP[rule], rule, sev, ("<b>%d</b>" % n) if n else "0", desc))
    P.append("</table>")
    P.append("</body></html>")
    return "".join(P)
