#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dep_audit.py — 本体感知(依赖图)代码审查引擎（纯标准库零依赖）

把「纯逐行正则审查」升级为「依赖图感知审查」：先对代码库做 AST 解析，
抽取出实体(函数/类/模块)与实体间依赖(调用图 + import 图)，审查时：
  - 查依赖与影响范围：改 A 会波及哪些调用方/依赖方（反向可达集）
  - 查循环依赖：模块 import 环（SCC）
  - 查模块耦合：fan-in / fan-out / 耦合指数，找高耦合脆弱点
  - 跨文件引用链：issue 指向的实体被谁引用、引用链是什么

用法：
    python dep_audit.py <文件或目录>                     # 依赖图报告
    python dep_audit.py <目录> --impact <符号>           # 改 A 波及谁（影响分析）
    python dep_audit.py <目录> --impact <符号> --json    # JSON 输出
    python dep_audit.py <目录> --impact <符号> --transitive # 传递闭包(全量波及)

本模块被 review.py(--dep / --impact) 与 code_agent.py 复用。
"""
import ast
import os
import sys
import json
from pathlib import Path
from collections import defaultdict, deque

# 内置名/常见第三方名，不作为依赖实体（避免噪声）
_BUILTINS = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
_BUILTINS |= {"self", "cls", "__name__", "__file__", "__doc__", "__init__", "__main__"}
# 明显的外部标准库/常用库，解析调用时不当作项目内实体
_STDLIB_HINT = {"os", "sys", "re", "json", "ast", "math", "time", "datetime", "pathlib",
                "subprocess", "collections", "typing", "argparse", "itertools", "functools",
                "urllib", "random", "hashlib", "requests", "numpy", "pandas", "logging"}


def _collect_py(paths):
    """展开 文件/目录 为 .py 文件列表（跳过虚拟环境）。"""
    out = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            if p.suffix == ".py":
                out.append(p)
        elif p.is_dir():
            out.extend(sorted(f for f in p.rglob("*.py")
                              if ".venv" not in str(f) and "node_modules" not in str(f)))
    return out


def _parse_module(path: str) -> dict:
    """AST 解析单个模块：实体定义 + import 映射 + 调用边。"""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content)
    except SyntaxError:
        return {"file": str(path), "ok": False}
    mod = Path(path).stem
    # 该文件里定义的实体名 → (kind, fqn, lineno)
    defined = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("__"):
            defined[node.name] = ("func", f"{mod}.{node.name}", node.lineno)
        elif isinstance(node, ast.ClassDef):
            defined[node.name] = ("class", f"{mod}.{node.name}", node.lineno)
    # import 映射: 名字 → 解析到的模块前缀（用于跨文件调用边解析）
    import_map = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                import_map[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            m = node.module or ""
            if node.level:  # 相对导入：粗略归到同目录模块
                for a in node.names:
                    import_map[a.asname or a.name] = a.name
            else:
                for a in node.names:
                    import_map[a.asname or a.name] = f"{m}.{a.name}"
    # 调用边: 函数 → 被调用符号（含方法名，解析尽量贴近）
    call_edges = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("__"):
            caller = f"{mod}.{node.name}"
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    name = sub.func.id
                    if name in defined:
                        call_edges.append((caller, defined[name][1]))
                    elif name in import_map:
                        call_edges.append((caller, import_map[name]))
                    elif name not in _BUILTINS and not name[0].islower() or (name in _BUILTINS):
                        pass
                    elif name not in _BUILTINS:
                        call_edges.append((caller, name))
                elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    # module.fn(...) / obj.method(...)
                    root = None
                    if isinstance(sub.func.value, ast.Name):
                        root = sub.func.value.id
                    elif isinstance(sub.func.value, ast.Attribute):
                        # a.b.c() → 用根名
                        r = sub.func.value
                        while isinstance(r, ast.Attribute):
                            r = r.value
                        if isinstance(r, ast.Name):
                            root = r.id
                    if root and root in import_map:
                        call_edges.append((caller, f"{import_map[root]}.{sub.func.attr}"))
    # 顶层 import 依赖（模块级模块名）
    module_imports = sorted(set(v.split(".")[0] for v in import_map.values()))
    return {"file": str(path), "ok": True, "mod": mod, "defined": defined,
            "imports": module_imports, "import_map": import_map, "edges": call_edges}


def build_graph(paths):
    """构建依赖图：{entities, edges, module_deps, modules}。"""
    files = _collect_py(paths)
    parsed = [_parse_module(str(f)) for f in files]
    parsed = [p for p in parsed if p.get("ok")]
    entities = {}      # fqn → {kind, module, file, line}
    for p in parsed:
        for name, (kind, fqn, line) in p["defined"].items():
            entities[fqn] = {"kind": kind, "module": p["mod"], "file": p["file"], "line": line}
    edges = []         # (caller_fqn, called_fqn)
    for p in parsed:
        for c, t in p["edges"]:
            edges.append((c, t))
    module_deps = defaultdict(set)   # mod → set(mod)
    for p in parsed:
        for imp in p["imports"]:
            # 只保留项目内模块（同目录 .py）作为模块依赖边，避免把 stdlib 计入环
            module_deps[p["mod"]].add(imp)
    return {"files": files, "parsed": parsed, "entities": entities,
            "edges": edges, "module_deps": dict(module_deps)}


def callers(graph, entity):
    """直接调用/引用 entity 的实体列表（改 entity 直接影响的第一层）。"""
    return sorted({c for c, t in graph["edges"] if t == entity})


def reverse_reach(graph, entity, transitive=False, module_mode=False):
    """影响分析：改 entity 波及的所有调用方/依赖方。
    - module_mode=True: entity 为模块名，波及 import 它的模块（含传递）
    - 默认: entity 为函数/类 fqn，波及调用它的实体（含传递可达，即改 A 最终影响谁）
    返回 sorted 列表（含说明）。"""
    if module_mode:
        adj = defaultdict(set)
        for m, deps in graph["module_deps"].items():
            for d in deps:
                adj[d].add(m)  # d 被 m 依赖 → 改 d 影响 m
        if entity not in adj:
            return []
        seed = set(adj.get(entity, set()))
        if transitive:
            seen, stack = set(seed), list(seed)
            while stack:
                cur = stack.pop()
                for up in adj.get(cur, set()) - seen:
                    seen.add(up)
                    stack.append(up)
            return sorted(seen)
        return sorted(seed)
    # 实体模式：被调方 → 调用方 反向边
    rev = defaultdict(set)
    for c, t in graph["edges"]:
        rev[t].add(c)
    if entity not in rev and entity not in graph["entities"]:
        return []
    # 从调用它的实体出发，逐层找上层调用者（受影响面向上传播）
    seed = set(rev.get(entity, set()))
    if transitive:
        seen, stack = set(seed), list(seed)
        while stack:
            cur = stack.pop()
            for up in rev.get(cur, set()):
                if up not in seen:
                    seen.add(up)
                    stack.append(up)
        return sorted(seen)
    return sorted(seed)


def circular_imports(graph):
    """检测模块 import 环（SCC 中环大小≥2，或自环）。返回环列表。"""
    adj = defaultdict(set)
    for m, deps in graph["module_deps"].items():
        for d in deps:
            if m != d:
                adj[m].add(d)
    nodes = set(graph["module_deps"].keys())
    # Tarjan SCC
    index, low, onstack, S, comps = {}, {}, set(), [], []
    def strongconnect(v):
        index[v] = low[v] = len(index)
        S.append(v); onstack.add(v)
        for w in adj[v]:
            if w not in index:
                strongconnect(w); low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = S.pop(); onstack.discard(w); comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or (len(comp) == 1 and v in adj[v]):
                comps.append(comp)
    for v in nodes:
        if v not in index:
            strongconnect(v)
    return sorted(comps, key=len, reverse=True)


def coupling_metrics(graph):
    """模块耦合：afferent(被依赖/fan-in) + efferent(依赖/fan-out) + 耦合指数。
    耦合指数 = efferent（出度，衡量对外依赖脆弱度）；fan-in 高 = 改它影响面大。"""
    mods = set(graph["module_deps"].keys())
    fan_in = defaultdict(int)
    fan_out = {m: len(set(d for d in graph["module_deps"].get(m, set()) if d in mods))
               for m in mods}
    for m, deps in graph["module_deps"].items():
        for d in deps:
            if d in mods:
                fan_in[d] += 1
    rows = []
    for m in sorted(mods):
        rows.append({"module": m, "fan_in": fan_in[m], "fan_out": fan_out[m],
                     "coupling_index": fan_out[m],  # 对外依赖数 = 脆弱度
                     "impact_surface": fan_in[m]})  # 被依赖数 = 改它的影响面
    return rows


def cross_file_refs(graph, entity):
    """跨文件引用链：entity 在哪些文件被谁引用（含被调方）。"""
    refs = []
    for c, t in graph["edges"]:
        if t == entity:
            cf = graph["entities"].get(c, {})
            refs.append({"caller": c, "caller_file": cf.get("file", "?"), "called": t})
    return refs


def dep_report(paths, impact=None, transitive=False, json_out=False):
    """组装依赖图审查报告。"""
    graph = build_graph(paths)
    report = {"targets": [str(p) for p in paths], "files": len(graph["files"]),
              "entities": len(graph["entities"]), "call_edges": len(graph["edges"])}
    # 模块耦合
    report["coupling"] = sorted(coupling_metrics(graph),
                                key=lambda r: -r["impact_surface"])
    # 循环依赖
    report["circular_imports"] = circular_imports(graph)
    # 影响分析
    if impact:
        entities = [e for e in graph["entities"] if e.endswith("." + impact) or e == impact]
        mods = [m for m in graph["module_deps"] if m == impact]
        result = {}
        for e in entities:
            result[e] = {"callers_direct": callers(graph, e),
                         "impact_affected": reverse_reach(graph, e, transitive=transitive)}
        for m in mods:
            result["module:" + m] = {"impact_affected": reverse_reach(graph, m,
                                                                      transitive=transitive, module_mode=True)}
        if not result and (impact in graph["entities"] or impact in graph["module_deps"]):
            result[impact] = {"impact_affected": []}
        report["impact"] = {impact: result}
    if json_out:
        return json.dumps(report, ensure_ascii=False, indent=2)
    return report


# ── 人类可读输出 ─────────────────────────────────
def _fmt_report(report):
    L = []
    L.append(f"══ 依赖图感知审查: {report['files']} 文件 / {report['entities']} 实体 / {report['call_edges']} 调用边 ══")
    # 耦合 Top
    L.append("\n📦 模块耦合(按影响面排序: 改它波及多大):")
    for r in report["coupling"][:10]:
        warn = " ⚠️高耦合" if r["impact_surface"] >= 3 or r["coupling_index"] >= 4 else ""
        L.append(f"   {r['module']:<22} fan_in(被依赖)={r['fan_in']:<3} fan_out(依赖)={r['fan_out']:<3}{warn}")
    # 循环依赖
    L.append("\n🔁 循环依赖(模块 import 环):")
    if report["circular_imports"]:
        for c in report["circular_imports"]:
            L.append(f"   ⚠️ {' → '.join(sorted(c))}（有环，改动需同步，避免 import 死锁）")
    else:
        L.append("   ✅ 无循环依赖")
    # 影响分析
    if report.get("impact"):
        L.append("\n🎯 影响分析(改 A 波及 B):")
        for symbol, res in report["impact"].items():
            for ent, info in res.items():
                aff = info.get("impact_affected", [])
                L.append(f"   改 {ent} → 直接影响 {len(info.get('callers_direct', []))} 处, 波及 {len(aff)} 个调用方")
                if aff:
                    L.append(f"      波及: {', '.join(aff[:20])}{' …' if len(aff) > 20 else ''}")
    return "\n".join(L)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="本体感知(依赖图)代码审查引擎")
    ap.add_argument("target", nargs="+", help="文件或目录")
    ap.add_argument("--impact", help="影响分析: 改该符号/模块波及谁")
    ap.add_argument("--transitive", action="store_true", help="影响分析含传递闭包")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()
    report = dep_report(args.target, impact=args.impact, transitive=args.transitive, json_out=args.json)
    if args.json:
        print(report)
    else:
        print(_fmt_report(report))


if __name__ == "__main__":
    main()
