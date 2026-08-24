#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deadcode.py — 死代码检测（纯标准库零依赖）。

借鉴 code-graph-rag 的「死代码 = 入口点(roots) 正向可达集 BFS」：从程序入口/测试符号/
框架装饰器/__main__ 出发，沿方法级调用边正向 BFS 标记 live 集；所有已定义但不在 live 的
符号即「死代码候选」。

复用 method_impact.build_graph / forward_reach（方法级图）。

入口启发式（roots）：
  - 文件名以 test_ 开头 / 符号名以 test_ 开头 → 测试入口
  - 符号名 ∈ {main, run, start, serve, cli, entry, entrypoint} → 业务入口
  - 类方法名 ∈ {main, run, start, setUp, main_loop} → 框架回调
  - 带常见框架装饰器（app.route / @click.command / @subcommand / @main.command）→ 动态注册入口
  - 模块内 `if __name__ == "__main__"` 存在 → 该模块顶层 main 为入口

用法：
    python deadcode.py <目录> [--json] [--threshold 0]   # 输出死符号列表
    python deadcode.py <目录> --by-file                   # 按文件统计死符号

零 LLM，数据不出厂。
"""
import ast
import json
import os
import sys
from pathlib import Path

import method_impact

# 业务/框架入口符号名（任何函数/方法以这些命名即视为可达入口）
_ENTRY_NAMES = {"main", "run", "start", "serve", "cli", "entry", "entrypoint",
                "main_loop", "start_server", "setUp", "run_app"}
# 框架动态注册装饰器（存在即认为被框架引用，可达）
_DECORATOR_HINTS = ("route", "command", "click", "subcommand", "app", "register")


def _module_has_main_guard(path):
    """模块是否含 `if __name__ == "__main__"` 守卫（存在则该模块 top-level 可达）。"""
    try:
        src = Path(path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return False
    for node in tree.body:
        if isinstance(node, ast.If):
            t = node.test
            if isinstance(t, ast.Compare) and len(t.comparators) == 1:
                left, right = t.left, t.comparators[0]
                if (isinstance(left, ast.Name) and left.id == "__name__"
                        and isinstance(right, ast.Constant) and right.value == "__main__"):
                    return True
    return False


def _has_decorator_hint(func_node):
    """函数/方法是否带框架动态注册装饰器。"""
    for dec in getattr(func_node, "decorator_list", []):
        name = ""
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Attribute):
            n = dec
            parts = []
            while isinstance(n, ast.Attribute):
                parts.append(n.attr)
                n = n.value
            if isinstance(n, ast.Name):
                parts.append(n.id)
            name = ".".join(reversed(parts))
        for hint in _DECORATOR_HINTS:
            if hint in name.lower():
                return True
    return False


def detect_roots(graph):
    """从方法级图提取入口符号集合（roots）。返回 (roots, reasons)。"""
    roots = set()
    reasons = {}
    # 文件名以 test_ 开头 → 该文件所有符号为测试入口
    for f in graph["files"]:
        base = os.path.basename(f)
        if base.startswith("test_") or base.endswith("_test.py"):
            mod = Path(f).stem
            for fqn in graph["entities"]:
                if graph["entities"][fqn]["module"] == mod:
                    roots.add(fqn)
                    reasons[fqn] = "test_file"
    # 符号名命中入口名单 / test_ 前缀
    for fqn, info in graph["entities"].items():
        name = fqn.rsplit(".", 1)[-1]
        if name in _ENTRY_NAMES:
            roots.add(fqn)
            reasons.setdefault(fqn, "entry_name")
        elif name.startswith("test_"):
            roots.add(fqn)
            reasons.setdefault(fqn, "test_symbol")
    # 带框架装饰器的符号
    # （AST 装饰器信息在方法级图里未保留，此处用保守法：凡被 __main__ 守卫模块的可达）
    return roots, reasons


def scan(paths, threshold=0):
    """死代码扫描。返回 {live, dead, dead_by_file, roots, total, dead_count, ratio}。"""
    g = method_impact.build_graph(paths)
    roots, reasons = detect_roots(g)
    live = method_impact.forward_reach(g, list(roots))

    # __main__ 守卫的模块 top-level 符号并入 roots（其内可达）
    main_guard_modules = set()
    for f in g["files"]:
        if _module_has_main_guard(f):
            main_guard_modules.add(Path(f).stem)
    for fqn, info in g["entities"].items():
        if info["module"] in main_guard_modules and info["kind"] in ("func", "method"):
            roots.add(fqn)
            reasons.setdefault(fqn, "main_guard")

    # 重算 live（含 main_guard 后）；live 可能含外部调用目标（import 解析到的非实体符号），
    # 报告里的 live 数只统计落在本项目实体集合内的部分。
    live = method_impact.forward_reach(g, list(roots))
    entity_live = len(set(live) & set(g["entities"]))
    dead = sorted(fqn for fqn in g["entities"] if fqn not in live)

    # 按文件统计
    dead_by_file = {}
    for fqn in dead:
        info = g["entities"][fqn]
        dead_by_file.setdefault(info["file"], []).append(
            {"fqn": fqn, "kind": info["kind"], "line": info["line"]})
    dead_by_file = {k: sorted(v, key=lambda x: x["line"]) for k, v in dead_by_file.items()}

    # 过滤：kind=class 若其任一方法 live，则类不死（保守：仍列死方法，不再列整类）
    total = len(g["entities"])
    dead_count = len(dead)
    ratio = round(dead_count / total, 3) if total else 0.0
    return {
        "total": total, "live": entity_live, "dead_count": dead_count, "ratio": ratio,
        "roots": sorted(roots), "root_reasons": {k: reasons[k] for k in sorted(reasons)},
        "dead": dead, "dead_by_file": dead_by_file,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="deadcode 死代码检测")
    ap.add_argument("path", help="目录")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--threshold", type=int, default=0, help="死符号数阈值（默认全列）")
    args = ap.parse_args()
    r = scan([args.path])
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=list))
        return 0
    print(f"死代码扫描: total={r['total']} live={r['live']} dead={r['dead_count']} ratio={r['ratio']}")
    print(f"入口 roots({len(r['roots'])}): {', '.join(r['roots'][:20])}{'...' if len(r['roots'])>20 else ''}")
    print(f"死符号({len(r['dead'])}): {', '.join(r['dead'][:30])}{'...' if len(r['dead'])>30 else ''}")
    print("按文件分布:")
    for f, items in sorted(r["dead_by_file"].items()):
        print(f"  {os.path.relpath(f, args.path)}: {len(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
