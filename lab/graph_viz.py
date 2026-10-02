#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_viz.py — 图数据归一（FR1.4/FR1.5/FR6.2，纯 stdlib）。

把原子输出归一为 {nodes, edges} 供前端 SVG 渲染：
  deps    文件级 import 依赖（lab 内置 AST 解析，真实）
  impacts 方法级影响面（复用 method-impact 能力：codeagent.py impact，容忍形态）
  layers  架构分层（复用 arch-review: archreview.layers）
失败降级为真实错误提示，不虚构图。
"""
import ast
import json
import os
import re
import subprocess
import sys


def _norm_path(p):
    return str(p).replace(os.sep, "/")


def deps_graph(root, rel_file=None):
    """文件级 import 依赖图（AST 解析，真实）：{nodes[{id,label,kind}], edges}。"""
    base = os.path.realpath(root)
    nodes, edges, seen = [], [], set()
    files = _py_files(base)
    if rel_file:
        target = os.path.realpath(os.path.join(base, rel_file))
        files = [f for f in files if f == target]
    # node id = 相对路径
    for f in files:
        rel = _norm_path(os.path.relpath(f, base))
        nodes.append({"id": rel, "label": os.path.basename(f), "kind": "file"})
    mods = {}  # 模块名 → 文件 rel
    for f in files:
        rel = os.path.relpath(f, base)
        name = os.path.splitext(os.path.basename(f))[0]
        mods[name] = _norm_path(rel)
    for f in files:
        rel = _norm_path(os.path.relpath(f, base))
        for imp in _imports(f):
            if imp in mods and (rel, mods[imp]) not in seen:
                edges.append({"from": rel, "to": mods[imp], "label": "import"})
                seen.add((rel, mods[imp]))
    return {"nodes": nodes, "edges": edges, "count": len(edges)}


def _py_files(root):
    out = []
    for r, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and d not in ("__pycache__", "lab_data", "node_modules")]
        for f in fs:
            if f.endswith(".py"):
                out.append(os.path.join(r, f))
    return out


def _imports(pyfile):
    """提取 import 的顶层模块名（真实 AST）。"""
    names = set()
    try:
        with open(pyfile, encoding="utf-8", errors="ignore") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
    except (OSError, SyntaxError):
        pass
    return names


def impact_graph(root, rel_file, symbol=None, codeagent_root=None, timeout=120):
    """方法级影响面：复用 method-impact 原子（codeagent.py impact），容忍形态归一。

    兜底：子进程失败 → lab 内置 AST 调用链（真实）。返回 {nodes, edges, source}。
    """
    base = os.path.realpath(root)
    real = os.path.realpath(os.path.join(base, rel_file))
    if os.path.commonpath([real, base]) != base or not os.path.isfile(real):
        return {"ok": False, "error": "路径越界或文件不存在", "nodes": [], "edges": []}
    if codeagent_root:
        ca = os.path.join(codeagent_root, "codeagent.py")
        if os.path.isfile(ca):
            cmd = [sys.executable, ca, "impact", real, "--json"]
            if symbol:
                cmd += ["--symbol", symbol]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                   encoding="utf-8", errors="replace")
                out = (r.stdout or "").strip()
                if out:
                    data = json.loads(out)
                    env = data.get("impact") or data
                    norm = _norm_impact(env, rel_file, symbol)
                    if norm.get("nodes") or norm.get("error"):
                        return norm
            except Exception as e:  # noqa: BLE001
                return _fallback_ast(real, rel_file, symbol, error=f"原子调用失败:{e}")
    return _fallback_ast(real, rel_file, symbol)


def _norm_impact(env, rel_file, symbol):
    """容忍 method-impact 输出形态 → {nodes, edges}。"""
    if not isinstance(env, dict):
        return {"ok": False, "error": "impact 输出非字典", "nodes": [], "edges": []}
    nodes, edges = [], []
    # 形态1: 已带 nodes/edges
    if isinstance(env.get("nodes"), list) and isinstance(env.get("edges"), list):
        nodes, edges = env["nodes"], env["edges"]
    else:
        # 形态2: impacted/impact_map {被影响: 影响者} 或 {symbol: [deps]}
        impacted = env.get("impacted") or env.get("impact_map") or {}
        if isinstance(impacted, dict):
            for k, v in impacted.items():
                nodes.append({"id": str(k), "label": str(k), "kind": "symbol"})
                vals = v if isinstance(v, list) else [v]
                for t in vals:
                    edges.append({"from": str(k), "to": str(t), "label": "affects"})
        # 形态3: deps/report 树
        deps = env.get("deps") or env.get("report") or env.get("tree") or {}
        if isinstance(deps, dict) and not edges:
            root = deps.get("module") or deps.get("root") or rel_file
            nodes.append({"id": str(root), "label": str(root), "kind": "module"})
            for k, v in (deps.items() if isinstance(deps, dict) else []):
                if k in ("module", "root"):
                    continue
                if isinstance(v, list):
                    for t in v[:20]:
                        edges.append({"from": str(root), "to": str(t), "label": "dep"})
                elif isinstance(v, dict):
                    for t in v.keys():
                        edges.append({"from": str(root), "to": str(t), "label": "dep"})
    if not nodes and not edges:
        return {"ok": True, "nodes": [], "edges": [], "source": "impact",
                "empty_reason": "原子无输出（无依赖/未命中符号）"}
    return {"ok": True, "nodes": nodes[:60], "edges": edges[:120], "source": "impact"}


def _fallback_ast(real, rel_file, symbol, error=None):
    """内置 AST 调用链（真实）：symbol 指定 → 函数被谁调用 + 调用了谁。"""
    nodes, edges = [], []
    try:
        with open(real, encoding="utf-8", errors="ignore") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError) as e:
        return {"ok": False, "error": f"AST解析失败:{e}", "nodes": [], "edges": []}
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    root_id = symbol if symbol in funcs else (rel_file or "module")
    nodes.append({"id": root_id, "label": root_id, "kind": "symbol"})
    if symbol in funcs:
        fn = funcs[symbol]
        calls = [n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        for c in calls:
            if c in funcs:
                nodes.append({"id": c, "label": c, "kind": "symbol"})
                edges.append({"from": root_id, "to": c, "label": "calls"})
        # 谁调用它（文件内）
        for name, fn2 in funcs.items():
            if name == symbol:
                continue
            for n in ast.walk(fn2):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                        and n.func.id == symbol:
                    nodes.append({"id": name, "label": name, "kind": "symbol"})
                    edges.append({"from": name, "to": root_id, "label": "calls"})
    else:
        # 无指定符号：函数间调用关系（全文件）
        for name, fn2 in funcs.items():
            nodes.append({"id": name, "label": name, "kind": "symbol"})
            for n in ast.walk(fn2):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                        and n.func.id in funcs and n.func.id != name:
                    edges.append({"from": name, "to": n.func.id, "label": "calls"})
    if error:
        return {"ok": True, "nodes": nodes, "edges": edges, "source": "ast_fallback",
                "fallback": error}
    return {"ok": True, "nodes": nodes, "edges": edges, "source": "ast"}


def layers_graph(root, rel_dir="", codeagent_root=None, timeout=120):
    """架构分层图：复用 arch-review（archreview.layers），容忍形态归一。"""
    base = os.path.realpath(root)
    folder = os.path.realpath(os.path.join(base, rel_dir)) if rel_dir else base
    if os.path.commonpath([folder, base]) != base:
        return {"ok": False, "error": "目录越界", "nodes": [], "edges": []}
    if codeagent_root:
        ar = os.path.join(codeagent_root, "arch_review.py")
        if os.path.isfile(ar):
            try:
                r = subprocess.run([sys.executable, ar, folder, "--capability", "layers"],
                                   capture_output=True, text=True, timeout=timeout,
                                   encoding="utf-8", errors="replace")
                out = (r.stdout or "").strip()
                if out:
                    data = json.loads(out)
                    layers = data.get("layers") if isinstance(data, dict) \
                        else data.get("data", {}).get("layers")
                    return _norm_layers(layers, rel_dir)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"arch-review调用失败:{e}",
                        "nodes": [], "edges": []}
    return {"ok": False, "error": "arch-review 不可用", "nodes": [], "edges": []}


def _norm_layers(layers, rel_dir):
    if not isinstance(layers, list):
        return {"ok": False, "error": "分层输出异常", "nodes": [], "edges": []}
    nodes, edges = [], []
    prev = None
    for i, ly in enumerate(layers):
        if isinstance(ly, str):
            lid = f"L{i}"; label = ly
        elif isinstance(ly, dict):
            lid = ly.get("name") or ly.get("layer") or f"L{i}"
            label = ly.get("name") or lid
        else:
            continue
        nodes.append({"id": str(lid), "label": str(label), "kind": "layer",
                      "y": i})
        if prev:
            edges.append({"from": str(lid), "to": prev, "label": "依赖方向"})
        prev = str(lid)
    return {"ok": True, "nodes": nodes, "edges": edges, "source": "arch-review"}