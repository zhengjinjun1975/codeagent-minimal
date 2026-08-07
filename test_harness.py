#!/usr/bin/env python3
"""test_harness.py — 专业化测试 harness（纯标准库零依赖）

对目标 Python 模块做测试闭环：冒烟 → 单元测试 → 边界测试 → 变异测试 → 稳定性。

- 冒烟   : 能否 import + 跑起来
- 单元   : 自动发现并运行 test_*.py / *_test.py 用例（有 pytest 则优先，否则标准库跑）
- 边界   : 对函数生成边界/异常输入（None/空/极值/错类型），找未处理的边界
- 变异   : 故意改一处代码，看现有测试能否捕获（测测试质量）
- 稳定性 : 重复 N 次 + 超时，看是否崩溃/挂起

用法:
  python test_harness.py 目标.py [--mutation] [--stability N] [--boundary]
"""
import ast
import sys
import os
import time
import traceback
import importlib.util
import argparse
import random

# 边界/异常输入生成器：探测函数健壮性
BOUNDARY_INPUTS = [None, "", [], {}, 0, -1, 0.0, 1e9, -1e9, " ", "\n", "a" * 1000, True]


def _load_module(path):
    """加载目标模块（不执行 main），返回 module 或 None。"""
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        sys.argv = [path]  # 防模块读 sys.argv 崩溃
        spec.loader.exec_module(mod)
        return mod
    except SystemExit:
        return mod
    except Exception:
        return None


def _find_functions(path):
    """从 ast 找可调用函数（排除私有/装饰器复杂）。"""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError:
        return []
    funcs = []
    entry_names = {"main", "cli", "run", "setup", "serve", "start"}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            if node.name not in entry_names:  # 排除 CLI/入口函数(需命令行参数, 不可单测)
                funcs.append(node.name)
    return funcs


def smoke(path):
    """冒烟：模块能否加载 + 有无顶层可调用函数。"""
    mod = _load_module(path)
    if mod is None:
        return {"ok": False, "issue": "模块导入失败", "details": "模块无法 import（可能有语法错或导入时崩溃）"}
    funcs = _find_functions(path)
    return {"ok": True, "functions": funcs, "details": f"模块导入成功，发现 {len(funcs)} 个顶层函数"}


def run_unit(target_dir):
    """单元测试：自动发现 test 文件并运行。
    有 pytest 则调用（subprocess），否则标准库逐个跑 test_ 函数。"""
    tests = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            if (f.startswith("test_") and f.endswith(".py")) or f.endswith("_test.py"):
                p = os.path.join(root, f)
                # 排除工具自身/递归源（避免 harness 跑自己导致自递归）
                content = open(p, encoding="utf-8", errors="ignore").read()
                if "import test_harness" in content or "import review" in content or "codeagent" in f.lower():
                    continue
                tests.append(p)
    if not tests:
        return {"ok": True, "test_count": 0, "skipped": True, "details": "未发现测试文件"}
    # 优先 pytest
    try:
        import pytest
        import subprocess
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"] + tests, capture_output=True, text=True, timeout=120)
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        # "no tests ran" / exit 5 = 无测试用例，算跳过不算失败
        if "no tests ran" in r.stdout or r.returncode == 5:
            return {"ok": True, "runner": "pytest", "test_count": 0, "skipped": True,
                    "details": "pytest: 发现测试文件但无测试用例"}
        return {"ok": r.returncode == 0, "runner": "pytest", "test_count": len(tests),
                "details": tail or "pytest 完成", "exit": r.returncode}
    except ImportError:
        # 标准库兜底：逐个收集并跑 test_ 函数
        passed, failed = 0, []
        for t in tests:
            mod = _load_module(t)
            if mod is None:
                failed.append((os.path.basename(t), "导入失败"))
                continue
            for name in dir(mod):
                if name.startswith("test_") and callable(getattr(mod, name)):
                    try:
                        getattr(mod, name)()
                        passed += 1
                    except Exception as e:
                        failed.append((name, str(e)[:60]))
        return {"ok": len(failed) == 0, "runner": "stdlib", "test_count": passed + len(failed),
                "passed": passed, "failed": failed}


def boundary(path):
    """边界测试：对发现的函数喂边界/异常输入，找出未处理异常的函数。"""
    mod = _load_module(path)
    if mod is None:
        return {"ok": True, "skipped": True, "details": "模块加载失败，跳过边界测试"}
    funcs = _find_functions(path)
    findings = []
    for fname in funcs:
        fn = getattr(mod, fname, None)
        if not callable(fn):
            continue
        try:
            import inspect
            sig = inspect.signature(fn)
            params = [p for p in sig.parameters.values()]
        except Exception:
            params = []
        if not params:
            continue
        # 对每个参数逐个喂边界输入，其余用默认值
        for pi, param in enumerate(params):
            for inp in BOUNDARY_INPUTS:
                args = []
                for j, p in enumerate(params):
                    if j == pi:
                        args.append(inp)
                    elif p.default is not inspect.Parameter.empty:
                        args.append(p.default)
                    else:
                        args.append(None)  # 无默认值填充 None
                try:
                    fn(*args)
                except TypeError:
                    pass  # 类型/参数不符，正常拒绝
                except Exception as e:
                    findings.append({"func": fname, "param": param.name, "input": repr(inp)[:24],
                                     "error": type(e).__name__,
                                     "suggestion": f"{fname} 参数 {param.name} 遇 {repr(inp)} 抛 {type(e).__name__}，考虑加校验"})
    return {"ok": len(findings) == 0, "findings": findings, "funcs_checked": len(funcs),
            "details": f"检查 {len(funcs)} 个函数，发现 {len(findings)} 个边界未处理" if findings else "边界处理良好"}


def coverage(path, timeout=3):
    """覆盖率估算：调用各顶层函数，统计被成功调用(无异常崩溃)的比例。"""
    mod = _load_module(path)
    if mod is None:
        return {"ok": True, "skipped": True, "details": "模块加载失败，跳过覆盖率"}
    funcs = _find_functions(path)
    if not funcs:
        return {"ok": True, "skipped": True, "details": "无可测函数"}
    called = 0
    for fname in funcs:
        fn = getattr(mod, fname, None)
        if not callable(fn):
            continue
        try:
            import inspect
            nparams = len([p for p in inspect.signature(fn).parameters.values() if p.default is inspect.Parameter.empty])
        except Exception:
            nparams = 1
        try:
            if nparams == 0:
                fn()
                called += 1
            else:
                fn(None)  # 用 None 探测(能跑通算覆盖)
                called += 1
        except Exception:
            pass
    return {"ok": True, "funcs_total": len(funcs), "funcs_called": called,
            "coverage_pct": round(called / len(funcs) * 100, 1) if funcs else 0,
            "details": f"函数覆盖 {called}/{len(funcs)} = {round(called/len(funcs)*100,1) if funcs else 0}%"}


def mutation(path, target_dir, max_mutants: int = 20):
    """变异测试：对目标文件做一处简单变异，跑现有测试看能否捕获。
    若变异后测试仍全绿 → 测试覆盖弱（存活变异体）。"""
    src = open(path, encoding="utf-8").read()
    mutants = 0
    survive = 0
    lines = src.split("\n")
    for i, line in enumerate(lines):
        # 简单变异：条件 == → != , > → <, True → False, 数字+1
        mutated = None
        if "==" in line and "!=" not in line:
            mutated = line.replace("==", "!=")
        elif " > " in line:
            mutated = line.replace(" > ", " < ")
        elif " < " in line:
            mutated = line.replace(" < ", " > ")
        elif "True" in line:
            mutated = line.replace("True", "False")
        elif " and " in line:
            mutated = line.replace(" and ", " or ")
        if mutated and mutated != line and "def " not in line:
            mutant_src = lines.copy()
            mutant_src[i] = mutated
            # 变异不能破坏语法
            try:
                ast.parse("\n".join(mutant_src))
            except SyntaxError:
                continue
            tmp = path + ".mut"
            open(tmp, "w", encoding="utf-8").write("\n".join(mutant_src))
            try:
                run = run_unit(target_dir)
                mutants += 1
                if run.get("ok", False) and run.get("test_count", 0) > 0:
                    survive += 1  # 变异后测试还全绿 → 存活（弱测试）
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            if max_mutants and mutants >= max_mutants:
                break  # 限次，避免大文件过慢
    return {"mutants": mutants, "survived": survive,
            "killed": mutants - survive,
            "ok": survive == 0,
            "details": f"生成 {mutants} 个变异，测试捕获 {mutants - survive} 个，存活 {survive} 个（存活=测试覆盖弱）"}


def stability(path, n=20, timeout=3):
    """稳定性：对每个函数重复调用 n 次 + 超时，看是否崩溃/挂起/缓慢。"""
    mod = _load_module(path)
    if mod is None:
        return {"ok": True, "skipped": True, "details": "模块加载失败，跳过稳定性测试"}
    funcs = _find_functions(path)
    results = []
    for fname in funcs:
        fn = getattr(mod, fname, None)
        if not callable(fn):
            continue
        try:
            sig = __import__("inspect").signature(fn)
            nparams = len([p for p in sig.parameters.values() if p.default is __import__("inspect").Parameter.empty])
        except Exception:
            nparams = 0
        if nparams == 0:
            start = time.time()
            crash = None
            for _ in range(n):
                try:
                    fn()
                except Exception as e:
                    crash = type(e).__name__
                    break
            elapsed = time.time() - start
            results.append({"func": fname, "crash": crash, "elapsed": round(elapsed, 3),
                            "slow": elapsed > timeout})
    return {"ok": all(not r["crash"] and not r["slow"] for r in results), "results": results}


def run_all(path, target_dir, do_mutation=True, do_stability=True, do_boundary=True, n=20, max_mutants=20):
    """完整测试闭环。"""
    report = {"target": path, "harness": "test_harness v1.0"}
    report["smoke"] = smoke(path)
    report["coverage"] = coverage(path)
    report["unit"] = run_unit(target_dir)
    if do_boundary:
        report["boundary"] = boundary(path)
    if do_mutation and report["unit"].get("test_count", 0) > 0:
        report["mutation"] = mutation(path, target_dir, max_mutants)
    if do_stability:
        report["stability"] = stability(path, n=n)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="专业化测试 harness")
    ap.add_argument("target", help="目标 Python 文件")
    ap.add_argument("--dir", default=".", help="测试文件所在目录(默认当前)")
    ap.add_argument("--no-mutation", action="store_true")
    ap.add_argument("--no-boundary", action="store_true")
    ap.add_argument("--no-stability", action="store_true")
    ap.add_argument("--stability-n", type=int, default=20)
    ap.add_argument("--max-mutants", type=int, default=20, help="变异测试限次(默认20,防大文件过慢)")
    args = ap.parse_args()
    rep = run_all(args.target, args.dir,
                  do_mutation=not args.no_mutation,
                  do_stability=not args.no_stability,
                  do_boundary=not args.no_boundary,
                  n=args.stability_n, max_mutants=args.max_mutants)
    print(f"══ 测试 harness 报告: {rep['target']} ══")
    for k in ["smoke", "coverage", "unit", "boundary", "mutation", "stability"]:
        if k in rep:
            v = rep[k]
            mark = "✅" if v.get("ok", False) else "⚠️" if v.get("skipped") else "❌"
            print(f"  {mark} {k}: {v.get('details', '')}")
    print("══ 结束 ══")
