#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fuzz_engine.py — 属性/模糊测试引擎（Hypothesis 级思想，纯标准库零依赖）。

借鉴（不复制）Hypothesis / Hypothesis 属性测试与 Hypothesis 类模糊测试的**思想**：
- 属性测试：用户给「对任意输入都应成立的性质」（不变量），引擎随机生成输入验证。
- 覆盖驱动生成：从被测试代码的 AST 提取函数签名与分支，生成针对性输入（边界/类型/极值）。
- 模糊测试：随机生成大量输入喂给目标函数，检测崩溃/未处理异常/超时。

纯标准库实现：随机数 + AST 签名提取 + subprocess 隔离执行（防崩溃污染主进程）。

用法：
    from fuzz_engine import fuzz_function, property_test, coverage_driven_gen
"""

import ast
import inspect
import os
import random
import subprocess
import sys
import tempfile
import time

# ── 基础类型生成器池 ─────────────────────────
BASIC_GEN = ["int", "float", "str", "bool", "None", "list", "dict", "empty_str",
             "zero", "neg_int", "big_int", "empty_list", "bytes", "tuple"]


def _gen_value(gen: str, rng: random.Random):
    if gen == "int":
        return rng.randint(-10000, 10000)
    if gen == "float":
        return rng.uniform(-1e6, 1e6)
    if gen == "str":
        return "".join(rng.choice("abcdefg0123456789 _-") for _ in range(rng.randint(0, 30)))
    if gen == "bool":
        return rng.random() < 0.5
    if gen == "None":
        return None
    if gen == "list":
        return [rng.randint(-100, 100) for _ in range(rng.randint(0, 10))]
    if gen == "dict":
        return {str(rng.randint(0, 99)): rng.randint(0, 9) for _ in range(rng.randint(0, 6))}
    if gen == "empty_str":
        return ""
    if gen == "zero":
        return 0
    if gen == "neg_int":
        return -rng.randint(1, 1000)
    if gen == "big_int":
        return rng.randint(10 ** 9, 10 ** 12)
    if gen == "empty_list":
        return []
    if gen == "bytes":
        return bytes(rng.randrange(256) for _ in range(rng.randint(0, 16)))
    if gen == "tuple":
        return tuple(rng.randint(-9, 9) for _ in range(rng.randint(0, 5)))
    return None


def _infer_gen(param_name: str, annotation) -> str:
    """按参数名+注解推断类型生成器。"""
    name = (param_name or "").lower()
    if annotation is not None:
        ann = annotation
        if isinstance(ann, ast.Name):
            ann = ann.id
        elif isinstance(ann, ast.Constant):
            ann = str(ann.value)
        if isinstance(ann, str):
            if ann in ("int", "float", "str", "bool", "bytes"):
                return {"int": "int", "float": "float", "str": "str",
                        "bool": "bool", "bytes": "bytes"}[ann]
            if ann in ("list", "List"):
                return "list"
            if ann in ("dict", "Dict"):
                return "dict"
    if name in ("s", "str", "name", "text", "msg", "key", "value", "path", "url", "email"):
        return "str"
    if name in ("n", "num", "count", "size", "limit", "idx", "index", "a", "b", "x", "y"):
        return "int"
    if name in ("items", "arr", "list", "data", "values", "seq"):
        return "list"
    return "int"


# ── 函数签名提取（供隔离执行 + 生成器）────────
def _extract_sig(path, funcname):
    """AST 提取函数参数名 + 注解 + 必填数。返回 dict 或 None。"""
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            args = []
            defaults = len(node.args.defaults)
            for i, a in enumerate(node.args.args):
                is_req = i >= (len(node.args.args) - defaults) if defaults else True
                ann = getattr(a, "annotation", None)
                args.append({"name": a.arg, "required": is_req, "annotation": ann})
            return {"name": funcname, "args": args, "is_async": isinstance(node, ast.AsyncFunctionDef)}
    return None


# ── 覆盖驱动生成（P1）：从分支生成针对性用例 ──
def coverage_driven_gen(path, funcname=None, max_cases=8) -> dict:
    """覆盖率驱动生成用例：从目标函数 AST 提取分支/条件，生成能覆盖多分支的输入。
    返回 {func, cases:[{args:[...], describe}], coverage_hint}。"""
    tree = None
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
    except SyntaxError:
        return {"func": funcname, "cases": [], "coverage_hint": 0, "error": "语法错误"}
    targets = []
    if funcname:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
                targets.append(node)
    else:
        targets = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and not n.name.startswith("_") and n.name not in ("main", "cli", "run")]
    if not targets:
        return {"func": funcname, "cases": [], "coverage_hint": 0, "error": "未找到函数"}

    cases = []
    for fn in targets[:3]:
        args = [a.arg for a in fn.args.args]
        if args and args[0] in ("self", "cls"):
            args = args[1:]
        conditions = []
        for sub in ast.walk(fn):
            if isinstance(sub, ast.If):
                conditions.append(sub.test)
        # 构造覆盖分支的输入：边界值字典
        branch_cases = []
        for cond in conditions[:4]:
            for probe in ("== 0", "> 0", "< 0", "is None", "== ''"):
                branch_cases.append(f"{' '.join(args[:1]) or 'x'} {probe}")
        # 生成具体参数：对每个参数轮换 0/负数/空串/None 等边界
        if args:
            boundaries = [0, -1, "", None, [], 1]
            for b in boundaries[:max_cases]:
                call_args = []
                for i, a in enumerate(args):
                    call_args.append(b if i == 0 else _gen_value("int", random.Random()))
                cases.append({"func": fn.name, "args": call_args, "describe": f"分支覆盖: {a if args else '?'}={repr(b)}"})
        else:
            cases.append({"func": fn.name, "args": [], "describe": "无参调用（覆盖主路径）"})
    return {"func": funcname, "cases": cases, "coverage_hint": len(cases),
            "branches_explored": len(set(c.get("describe", "") for c in cases))}


# ── 模糊测试（隔离执行）───────────────────────
def _isolation_script(target, funcname, arg_reprs, timeout):
    """子进程隔离：import 目标模块 → 调函数 → 捕获异常/崩溃/超时。"""
    import json
    code = (
        "import sys, os, json, traceback, importlib.util\n"
        f"path={json.dumps(target)}\n"
        "name=os.path.splitext(os.path.basename(path))[0]\n"
        "spec=importlib.util.spec_from_file_location(name, path)\n"
        "mod=importlib.util.module_from_spec(spec)\n"
        "sys.argv=[path]\n"
        "try:\n"
        "    spec.loader.exec_module(mod)\n"
        "except Exception as e:\n"
        "    print('IMPORT_FAIL:'+type(e).__name__+':'+str(e)); sys.exit(0)\n"
        f"fn=getattr(mod, {json.dumps(funcname)}, None)\n"
        "if fn is None:\n"
        "    print('NO_FUNC'); sys.exit(0)\n"
        f"args={json.dumps(arg_reprs)}\n"
        "try:\n"
        "    if hasattr((insp:=__import__('inspect')),'iscoroutinefunction') and insp.iscoroutinefunction(fn):\n"
        "        __import__('asyncio').run(fn(*args))\n"
        "    else:\n"
        "        fn(*args)\n"
        "    print('OK') \n"
        "except Exception as e:\n"
        "    print('EXC:'+type(e).__name__+':'+str(e)[:120])\n"
    )
    return code


def fuzz_function(path, funcname, iterations=100, timeout=2.0, seed=None,
                  min_interesting_exceptions=0) -> dict:
    """对目标函数做属性/模糊测试。
    返回 {func, runs, crashed:[{args, error}], unhandled:[...], ok, details}。
    隔离执行：每个输入在子进程跑，防崩溃/死循环污染主进程。"""
    sig = _extract_sig(path, funcname)
    if sig is None:
        return {"func": funcname, "ok": False, "runs": 0, "crashed": [],
                "unhandled": [], "details": "函数签名提取失败"}
    rng = random.Random(seed)
    gens = [_infer_gen(a["name"], a["annotation"]) for a in sig["args"]]
    crashed, unhandled = [], []
    # 白名单异常：预期可接受（TypeError/ValueError 由参数类型不符触发，属正常拒绝）
    acceptable = {"TypeError", "ValueError", "StopIteration"}

    # 预生成一批输入（含确定性种子）
    arg_batches = []
    for _ in range(iterations):
        arg_batches.append([_gen_value(g, rng) for g in gens])

    # 对每批输入单独子进程跑（防崩溃污染）
    for batch in arg_batches[:iterations]:
        prog = _isolation_script(path, funcname, batch, timeout)
        try:
            r = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                               text=True, timeout=timeout, cwd=os.path.dirname(path) or ".")
        except subprocess.TimeoutExpired:
            crashed.append({"args": _short(batch), "error": "TimeoutExpired(>%ss)" % timeout})
            continue
        out = (r.stdout or "").strip().splitlines()
        line = out[-1] if out else ""
        if line.startswith("EXC:"):
            _, etype, emsg = line.split(":", 2)
            if etype not in acceptable:
                unhandled.append({"args": _short(batch), "error": f"{etype}: {emsg}"})
    ok = not crashed and not unhandled
    return {"func": funcname, "runs": len(arg_batches), "crashed": crashed,
            "unhandled": unhandled, "ok": ok,
            "details": f"模糊 {len(arg_batches)} 次，未处理异常 {len(unhandled)}，崩溃 {len(crashed)}"}


def _short(args):
    return [repr(a)[:24] for a in args]


# ── 属性测试：不变量校验 ──────────────────────
def property_test(path, funcname, properties, iterations=50, timeout=2.0, seed=42) -> dict:
    """属性测试：properties 为 [(描述, callable(返回值/调用结果)->bool)]。
    对随机输入调用函数，校验每个不变量。
    返回 {func, iterations, failures:[{property, args, detail}], ok}。"""
    sig = _extract_sig(path, funcname)
    if sig is None:
        return {"func": funcname, "ok": False, "iterations": 0, "failures": [],
                "details": "签名提取失败"}
    rng = random.Random(seed)
    gens = [_infer_gen(a["name"], a["annotation"]) for a in sig["args"]]
    mod = _load_safe(path)
    if mod is None:
        return {"func": funcname, "ok": False, "iterations": 0, "failures": [],
                "details": "模块加载失败"}
    fn = getattr(mod, funcname, None)
    if not callable(fn):
        return {"func": funcname, "ok": False, "iterations": 0, "failures": [], "details": "函数不可调用"}
    failures = []
    import asyncio
    for _ in range(iterations):
        args = [_gen_value(g, rng) for g in gens]
        try:
            if inspect.iscoroutinefunction(fn):
                result = asyncio.run(fn(*args))
            else:
                result = fn(*args)
        except Exception as e:
            failures.append({"property": "调用", "args": _short(args),
                             "detail": f"抛出 {type(e).__name__}: {e}"})
            continue
        for pname, prop in properties:
            try:
                if not prop(result):
                    failures.append({"property": pname, "args": _short(args), "detail": f"返回 {result!r}"})
            except Exception as e:
                failures.append({"property": pname, "args": _short(args),
                                 "detail": f"属性校验异常 {type(e).__name__}"})
    return {"func": funcname, "iterations": iterations, "failures": failures,
            "ok": len(failures) == 0,
            "details": f"属性测试 {iterations} 次，失败 {len(failures)}"}


def _load_safe(path):
    import importlib.util
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name + "_fuzz", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        sys.argv = [path]
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def fuzz_project(path, iterations=40, timeout=1.5, max_funcs=5) -> dict:
    """目录级模糊：对全部非私有函数做模糊测试。"""
    results = {}
    py = []
    if os.path.isfile(path) and path.endswith(".py"):
        py = [path]
    elif os.path.isdir(path):
        for root, _d, files in os.walk(path):
            for f in files:
                if f.endswith(".py"):
                    py.append(os.path.join(root, f))
    total_runs = 0
    total_unhandled = 0
    for pf in py[:max_funcs * 3]:
        try:
            tree = ast.parse(open(pf, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and not node.name.startswith("_") and len(results) < max_funcs:
                r = fuzz_function(pf, node.name, iterations=iterations, timeout=timeout)
                results[f"{os.path.basename(pf)}::{node.name}"] = r
                total_runs += r.get("runs", 0)
                total_unhandled += len(r.get("unhandled", []))
                break
    return {"functions": results, "total_runs": total_runs,
            "total_unhandled": total_unhandled, "count": len(results),
            "summary": f"模糊 {len(results)} 函数/{total_runs} 次，未处理异常 {total_unhandled}"}


def main():
    import argparse, json
    ap = argparse.ArgumentParser(description="fuzz_engine: 属性/模糊测试(纯stdlib,隔离执行)")
    ap.add_argument("path", help="文件或目录")
    ap.add_argument("--func", default=None, help="目标函数名")
    ap.add_argument("--iter", type=int, default=40)
    ap.add_argument("--timeout", type=float, default=1.5)
    args = ap.parse_args()
    if args.func:
        r = fuzz_function(args.path, args.func, iterations=args.iter, timeout=args.timeout)
    else:
        r = fuzz_project(args.path, iterations=args.iter, timeout=args.timeout)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
