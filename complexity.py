#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""complexity.py — 复杂度度量（radon 级，纯标准库零依赖）。

借鉴（不复制）radon 的**思想**：McCabe 圈复杂度 / 认知复杂度 / 代码行规模。
纯 AST 实现，无第三方依赖，数据不出厂。

- cyclomatic_complexity(node)：McCabe V(G) = 决策点 + 1
  （if/elif/for/while/with/except/try/assert/comprehension/and/or/bool/条件表达式）
- function_complexity(tree)：每个函数/方法的圈复杂度 + 认知复杂度 + 行数
- project_report(target)：目录级复杂度报告（超阈值告警）

用法：
    from complexity import project_report, function_complexity
    r = project_report(path_or_dir, max_complexity=10)
"""

import ast
import os

COMPLEXITY_THRESHOLDS = {"ok": 10, "warn": 20, "high": 40}


def cyclomatic_complexity(node) -> int:
    """McCabe 圈复杂度：1 + 决策点计数。"""
    decisions = 0
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.IfExp)):
            decisions += 1
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.While)):
            decisions += 1
        elif isinstance(n, (ast.With, ast.AsyncWith, ast.ExceptHandler)):
            decisions += 1
        elif isinstance(n, ast.And) or isinstance(n, ast.Or):
            decisions += 1
        elif isinstance(n, ast.BoolOp):
            decisions += max(0, len(n.values) - 1)
        elif isinstance(n, (ast.comprehension, ast.Assert)):
            decisions += 1
        elif isinstance(n, ast.Match):
            decisions += len(n.cases)
        elif isinstance(n, ast.Try):
            decisions += len(n.handlers)
    return decisions + 1


def cognitive_complexity(node) -> int:
    """认知复杂度：嵌套决策叠加，粗略实现。"""
    score = 0

    def walk(n, nesting=0):
        nonlocal score
        inc = 0
        if isinstance(n, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            inc = 1
        elif isinstance(n, ast.ExceptHandler):
            inc = 1
        elif isinstance(n, ast.And) or isinstance(n, ast.Or):
            inc = 1
        elif isinstance(n, ast.IfExp):
            inc = 1
        elif isinstance(n, ast.BoolOp):
            inc = max(0, len(n.values) - 1)
        if inc:
            score += inc + nesting
            nesting += 1
        for child in ast.iter_child_nodes(n):
            walk(child, nesting)

    walk(node)
    return score


def _line_span(node):
    return getattr(node, "lineno", 0), getattr(node, "end_lineno", getattr(node, "lineno", 0))


def function_complexity(tree) -> list:
    """每个函数/方法的复杂度 + 行数 + 圈/认知复杂度。返回排序 list。"""
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end = _line_span(node)
            rows.append({
                "name": node.name,
                "type": "async" if isinstance(node, ast.AsyncFunctionDef) else "function",
                "line": start,
                "lines": max(1, end - start + 1),
                "cyclomatic": cyclomatic_complexity(node),
                "cognitive": cognitive_complexity(node),
                "args": len(node.args.args),
                "verdict": "high" if node.name.startswith("test_") is False and
                            cyclomatic_complexity(node) > COMPLEXITY_THRESHOLDS["high"] else
                            ("warn" if cyclomatic_complexity(node) > COMPLEXITY_THRESHOLDS["warn"] else
                             ("warn" if cyclomatic_complexity(node) > COMPLEXITY_THRESHOLDS["ok"] else "ok")),
            })
    return sorted(rows, key=lambda r: (-r["cyclomatic"], r["line"]))


def _collect_py(target: str) -> list:
    py = []
    if os.path.isfile(target) and target.endswith(".py"):
        py = [target]
    elif os.path.isdir(target):
        for root, _d, files in os.walk(target):
            for f in files:
                if f.endswith(".py"):
                    py.append(os.path.join(root, f))
    return py[:500]


def project_report(target: str, max_complexity: int = 10) -> dict:
    """目录/文件级复杂度报告。返回 {files:[...], worst, warnings, summary}。"""
    files_report = []
    total_funcs = 0
    warnings = []
    for pf in _collect_py(target):
        try:
            tree = ast.parse(open(pf, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        funcs = function_complexity(tree)
        total_funcs += len(funcs)
        over = [f for f in funcs if f["cyclomatic"] > max_complexity]
        avg = round(sum(f["cyclomatic"] for f in funcs) / len(funcs), 1) if funcs else 0.0
        files_report.append({
            "file": pf, "funcs": len(funcs), "avg_cyclomatic": avg,
            "max_cyclomatic": max((f["cyclomatic"] for f in funcs), default=0),
            "over_threshold": len(over),
            "worst": over[0] if over else None,
        })
        for f in over[:5]:
            warnings.append({
                "file": pf, "function": f["name"], "line": f["line"],
                "cyclomatic": f["cyclomatic"], "cognitive": f["cognitive"],
                "suggestion": f"函数 {f['name']} 圈复杂度 {f['cyclomatic']}>{max_complexity}，建议拆分/降分支",
            })
    files_report.sort(key=lambda r: -r["max_cyclomatic"])
    return {
        "files": files_report, "total_files": len(files_report),
        "total_functions": total_funcs, "warnings": warnings,
        "worst": files_report[0] if files_report else None,
        "summary": f"分析 {len(files_report)} 文件/{total_funcs} 函数，{len(warnings)} 个超阈值复杂函数",
        "max_complexity": max_complexity,
    }


# ── CLI ─────────────────────────────────────
def main():
    import argparse, json
    ap = argparse.ArgumentParser(description="complexity: radon 级复杂度度量(纯stdlib)")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--max", type=int, default=10, help="圈复杂度阈值")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = project_report(args.target, max_complexity=args.max)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("══ 复杂度报告 ══")
        print(r["summary"])
        for w in r["warnings"]:
            print(f"  ⚠️ {w['function']} L{w['line']} 圈{w['cyclomatic']} 认知{w['cognitive']}: {w['suggestion']}")


if __name__ == "__main__":
    main()
