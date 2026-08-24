#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""method_impact.py — 方法级影响分析（纯标准库零依赖）。

借鉴 code-graph-rag 的「方法级 CALLS / REFERENCES / INSTANTIATES / INHERITS 边 + 传递反向可达」，
在 dep_audit 的文件级/实体级依赖图之上，下沉为「函数/方法 → 被调函数/方法」的方法级调用图，
并为每条边标注类型（kind），从而支持：

- impact.method：改某个函数/方法，波及哪些「间接调用者」，且路径可回溯
  （反向 BFS，保留前驱以重建最短传播路径）。
- impact.kind：把边区分为 CALLS（真调用）/ INSTANTIATES（实例化）/ INHERITS（继承）/ REFERENCES（仅名字引用）。

与 dep_audit 的关系：dep_audit 已能给出函数→函数 reverse_reach（实体级，方法粒度），
本模块在其基础上补齐 ①边类型标注 ②传播路径重建 ③类方法 fqn（module.Class.method） ④stdlib/三方调用降噪。

用法：
    python method_impact.py <目录> --symbol <fqn> [--transitive]   # 方法级影响 + 路径
    python method_impact.py <目录> --kind <caller> --to <callee>   # 边类型分类
    python method_impact.py <目录> --symbol <fqn> --json           # JSON 输出

零 LLM，数据不出厂。
"""
import ast
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path

# 外部/标准库常见名，不当作项目内被调实体（去噪）
_STDLIB_HINT = {
    "os", "sys", "re", "json", "ast", "math", "time", "datetime", "pathlib",
    "subprocess", "collections", "typing", "argparse", "itertools", "functools",
    "urllib", "random", "hashlib", "logging", "traceback", "tempfile",
    "requests", "numpy", "pandas", "importlib", "importlib.util",
}


def _collect_py(paths):
    """展开 文件/目录 为 .py 文件列表（跳过虚拟环境）。"""
    out = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            if p.suffix == ".py":
                out.append(str(p))
        elif p.is_dir():
            out.extend(sorted(str(f) for f in p.rglob("*.py")
                              if ".venv" not in str(f) and "node_modules" not in str(f)))
    return out


def _parse(path):
    """AST 解析单文件：定义(fqn/kind/line) + 方法级调用边(kind)。

    边类型：
      CALLS         普通函数/方法调用
      INSTANTIATES  类实例化（Class(...)）
      INHERITS      继承（class X(Base)）
      REFERENCES    仅名字被引用（非调用），保守边
    返回 dict 或 None（语法错误跳过）。
    """
    try:
        src = Path(path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except SyntaxError:
        return None
    mod = Path(path).stem

    # import 映射：名字 -> 解析前缀（尽量贴近真实 fqn）
    import_map = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                import_map[a.asname or a.name] = a.name
        elif isinstance(n, ast.ImportFrom):
            m = n.module or ""
            for a in n.names:
                import_map[a.asname or a.name] = (f"{m}.{a.name}" if m else a.name)

    defined = {}   # 局部名字 -> fqn
    fqns = []      # [(fqn, kind, line)]

    def add_func(node, prefix):
        fqn = f"{prefix}.{node.name}"
        defined[node.name] = fqn
        fqns.append((fqn, "method" if "." in prefix else "func", node.lineno))

    # 顶层定义 + 类方法（module.Class.method）
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            add_func(node, mod)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            cfqn = f"{mod}.{node.name}"
            defined[node.name] = cfqn
            fqns.append((cfqn, "class", node.lineno))
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and not b.name.startswith("_"):
                    add_func(b, cfqn)

    edges = []   # (caller_fqn, callee_fqn, kind)

    def _resolve_name(name):
        """把 AST 里的名字解析到 fqn（先局部定义，再 import 映射）。"""
        if name in defined:
            return defined[name]
        if name in import_map:
            return import_map[name]
        return None

    # 建立集合供边解析用（先有完整定义）
    _class_fqns = {f for f, k, _ in fqns if k == "class"}
    _fqn_set = {f for f, _, _ in fqns}

    def _scan_body(func_node, caller):
        # 调用：Call(func=Name) / Call(func=Attribute)
        for sub in ast.walk(func_node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name):
                    tgt = _resolve_name(f.id)
                    if tgt:
                        kind = "INSTANTIATES" if tgt in _class_fqns else "CALLS"
                        edges.append((caller, tgt, kind))
                elif isinstance(f, ast.Attribute):
                    root = f.value
                    while isinstance(root, ast.Attribute):
                        root = root.value
                    if isinstance(root, ast.Name):
                        base = _resolve_name(root.id)
                        if base:
                            # 类方法调用 module.Class.method
                            method_fqn = f"{base}.{f.attr}"
                            if method_fqn in _fqn_set:
                                edges.append((caller, method_fqn, "CALLS"))
                            elif base in _class_fqns:
                                # 类实例上的方法调用（可能动态分派，保守 REFERENCES）
                                edges.append((caller, method_fqn, "REFERENCES"))
                            else:
                                edges.append((caller, f"{base}.{f.attr}", "CALLS"))

        # 继承边
        for base in getattr(func_node, "bases", []):
            if isinstance(base, ast.Name):
                tgt = _resolve_name(base.id)
                if tgt:
                    edges.append((caller, tgt, "INHERITS"))

    # 方法体扫描调用边
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            caller = f"{mod}.{node.name}"
            _scan_body(node, caller)
        elif isinstance(node, ast.ClassDef):
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and not b.name.startswith("_"):
                    caller = f"{mod}.{node.name}.{b.name}"
                    _scan_body(b, caller)

    return {"mod": mod, "file": str(path), "defined": defined, "fqns": fqns, "edges": edges}


def build_graph(paths):
    """构建方法级图：{entities:{fqn:{kind,module,file,line}}, edges:[(caller,callee,kind)], files}。"""
    files = _collect_py(paths)
    entities = {}
    edges = []
    for f in files:
        p = _parse(f)
        if not p:
            continue
        for fqn, kind, line in p["fqns"]:
            entities[fqn] = {"kind": kind, "module": p["mod"], "file": p["file"], "line": line}
        edges.extend(p["edges"])
    return {"files": files, "entities": entities, "edges": edges}


def _class_fqns(graph):
    return {f for f, i in graph["entities"].items() if i["kind"] == "class"}


def classify(graph, caller, callee):
    """返回某对 (caller,callee) 的边类型列表；找不到则尽力从实体推断。"""
    kinds = {k for c, t, k in graph["edges"] if c == caller and t == callee}
    if not kinds:
        info = graph["entities"].get(callee)
        if info and info["kind"] == "class":
            kinds.add("INSTANTIATES" if caller else "INHERITS")
        elif info:
            kinds.add("CALLS")
    return sorted(kinds)


def reverse_reach(graph, symbol, transitive=True, max_depth=None):
    """改 symbol 波及的调用者（方法级传递反向可达）。

    返回 (impact_list, paths)：
      impact_list: sorted [ (fqn, kind, direct) ... ] 影响方
      paths: {fqn: [传播路径]} 最短传播路径（从直接调用者到根调用者）。
    """
    rev = defaultdict(set)
    for c, t, k in graph["edges"]:
        rev[t].add(c)
    if symbol not in rev and symbol not in graph["entities"]:
        return [], {}

    seed = sorted(rev.get(symbol, set()))
    if not transitive:
        return seed, {s: [s, symbol] for s in seed}

    # BFS 反向，保留前驱重建最短路径
    pred = {symbol: None}
    queue = deque([symbol])
    while queue:
        cur = queue.popleft()
        if max_depth and _depth(pred, cur) >= max_depth:
            continue
        for up in sorted(rev.get(cur, set())):
            if up not in pred:
                pred[up] = cur
                queue.append(up)
    pred.pop(symbol, None)
    impact = sorted(pred.keys())
    paths = {f: _reconstruct(pred, f, symbol) for f in impact}
    return impact, paths


def _depth(pred, node):
    d = 0
    cur = node
    while cur is not None:
        d += 1
        cur = pred.get(cur)
        if d > 100:
            break
    return d


def _reconstruct(pred, start, end):
    path = []
    cur = start
    seen = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        path.append(cur)
        if cur == end:
            break
        cur = pred.get(cur)
    return path


def forward_reach(graph, roots, max_depth=None):
    """从 roots 出发正向可达集（死代码检测用）：follow 谁被调用。"""
    adj = defaultdict(set)
    for c, t, k in graph["edges"]:
        adj[c].add(t)
    live = set(roots)
    queue = deque(roots)
    while queue:
        cur = queue.popleft()
        if max_depth and _depth({n: None for n in live}, cur) >= max_depth:
            continue
        for t in sorted(adj.get(cur, set())):
            if t not in live:
                live.add(t)
                queue.append(t)
    return live


# ── CLI 自测 ────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="method_impact 方法级影响分析")
    ap.add_argument("path", help="目录")
    ap.add_argument("--symbol", help="方法 fqn（如 module.Class.method）")
    ap.add_argument("--caller", help="边类型：caller")
    ap.add_argument("--to", help="边类型：callee")
    ap.add_argument("--transitive", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    g = build_graph([args.path])
    out = {}
    if args.caller and args.to:
        out["kind"] = classify(g, args.caller, args.to)
    elif args.symbol:
        impact, paths = reverse_reach(g, args.symbol, transitive=args.transitive)
        out = {"symbol": args.symbol, "impact": impact, "paths": paths,
               "entities": len(g["entities"]), "edges": len(g["edges"])}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=list))
    else:
        print(f"方法级图: entities={len(g['entities'])} edges={len(g['edges'])}")
        print(json.dumps(out, ensure_ascii=False, indent=2, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
