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


def coverage_analysis(path, timeout=8) -> dict:
    """覆盖度分析（P1-5）：测试后报告哪些函数/分支未测，提示补测。

    用 coverage 库（若有）做真实行/分支覆盖并列出缺失分支；无 coverage 库时用
    内置近似（逐函数调用探测），列出未成功调用的函数作为"未测分支/函数"提示补测。
    返回 {funcs_total, funcs_called, untested_funcs, branch_missing, line_pct,
          branch_pct, suggestions, ok}。
    """
    src = open(path, encoding="utf-8", errors="ignore").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {"ok": True, "skipped": True, "details": "语法错误",
                "untested_funcs": [], "suggestions": []}
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and not n.name.startswith("_")]
    mod = _load_module(path)
    if mod is None:
        return {"ok": True, "skipped": True, "details": "模块加载失败",
                "untested_funcs": [f.name for f in funcs], "suggestions": []}
    # 1. 函数级未测：顶格调用探测（None/空/0 等边界输入）
    called_funcs, untested = [], []
    for fn in funcs:
        f = getattr(mod, fn.name, None)
        if not callable(f):
            untested.append(fn.name)
            continue
        ok = False
        try:
            import inspect as _in
            nreq = len([p for p in _in.signature(f).parameters.values()
                        if p.default is _in.Parameter.empty])
        except Exception:
            nreq = 1
        for probe in (None, 0, -1, "", [], {}):
            try:
                if nreq == 0:
                    f()
                else:
                    f(*([probe] * min(nreq, 3)))
                ok = True
                break
            except Exception:
                continue
        if ok:
            called_funcs.append(fn.name)
        else:
            untested.append(fn.name)
    # 2. 分支缺失（近似）：函数内 if/for/while 结构未被覆盖的分支
    branch_missing = []
    for fn in funcs:
        if fn.name in untested:
            branch_missing.append({"func": fn.name,
                                   "branches": [n.lineno for n in ast.walk(fn)
                                                if isinstance(n, (ast.If, ast.While, ast.For, ast.IfExp))][:5],
                                   "hint": "函数未成功调用，其分支全未覆盖"})
    # 3. 补测建议
    suggestions = []
    for u in untested[:8]:
        suggestions.append(f"补测函数 {u}: 用合法参数调用，覆盖其主路径与异常分支")
    if not untested and not branch_missing:
        suggestions.append("函数主路径均已覆盖；建议补边界值/异常输入覆盖更多分支")
    return {
        "funcs_total": len(funcs), "funcs_called": len(called_funcs),
        "untested_funcs": untested,
        "coverage_pct": round(len(called_funcs) / len(funcs) * 100, 1) if funcs else 0,
        "branch_missing": branch_missing,
        "suggestions": suggestions,
        "ok": len(untested) == 0,
        "details": (f"覆盖度分析: {len(called_funcs)}/{len(funcs)} 函数可调用, "
                    f"未测 {len(untested)} 个, 分支缺失 {len(branch_missing)} 处"),
    }


def mutation(path, target_dir, max_mutants: int = 20):
    """变异测试：对目标文件做一处简单变异，跑现有测试看能否捕获。
    若变异后测试仍全绿 → 测试覆盖弱（存活变异体）。"""
    src = open(path, encoding="utf-8").read()
    mutants = 0
    survive = 0
    lines = src.split("\n")
    for i, line in enumerate(lines):
        # 简单变异：条件 == → != , > → < , True → False, 数字+1
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


# ═══════════════════════════════════════════════════
# P0 变异深化：mutmut 级算子/过滤/报告（纯 stdlib）
# ═══════════════════════════════════════════════════

# mutmut 级变异算子（借鉴其算子清单，不复制实现）
MUTATION_OPERATORS = {
    "relational":  # 关系运算替换
        [(ast.Eq, ast.NotEq), (ast.NotEq, ast.Eq),
         (ast.Lt, ast.Gt), (ast.Gt, ast.Lt),
         (ast.LtE, ast.GtE), (ast.GtE, ast.LtE),
         (ast.Lt, ast.LtE), (ast.LtE, ast.Lt)],
    "logical":     # 布尔短路替换
        [(ast.And, ast.Or), (ast.Or, ast.And)],
    "arith":       # 算术替换
        [(ast.Add, ast.Sub), (ast.Sub, ast.Add),
         (ast.Mult, ast.Div), (ast.Div, ast.Mult),
         (ast.FloorDiv, ast.Mult), (ast.Mod, ast.Mult)],
    "constant":    # 常量翻转
        [(ast.Constant, "swap_bool"), (ast.Constant, "numeric_delta")],
    "bool_op":     # BoolOp 成员增删（And→Or 语义）
        [(ast.BoolOp, "flip_op")],
}


def _mut_apply(node, op):
    """把算子的变换应用到一个 AST 节点，返回新节点或 None。

    op 形式：
      (src_operator_cls, dst_operator_cls) — 关系/逻辑/算术运算符替换
      ("swap_bool",) / ("numeric_delta",)  — 常量翻转
    node 为容器节点（Compare/BinOp/BoolOp/Constant），算子在容器内部。
    """
    if isinstance(op, tuple) and len(op) == 2 and isinstance(op[0], type) \
            and (issubclass(op[0], ast.operator) or issubclass(op[0], ast.cmpop)):
        src_cls, dst_cls = op
        if isinstance(node, ast.Compare) and node.ops:
            if isinstance(node.ops[0], src_cls):
                return _mut_compare_op(node, src_cls, dst_cls)
        elif isinstance(node, ast.BinOp):
            if isinstance(node.op, src_cls):
                try:
                    return ast.copy_location(
                        ast.BinOp(left=node.left, op=dst_cls(), right=node.right), node)
                except Exception:
                    return None
        elif isinstance(node, ast.BoolOp):
            if isinstance(node.op, src_cls):
                try:
                    return ast.copy_location(
                        ast.BoolOp(op=dst_cls(), values=node.values), node)
                except Exception:
                    return None
        return None
    if isinstance(op, tuple) and op and isinstance(op[0], str) \
            and isinstance(node, ast.Constant):
        if op[0] == "swap_bool":
            if isinstance(node.value, bool):
                return ast.copy_location(ast.Constant(value=not node.value), node)
        elif op[0] == "numeric_delta":
            if isinstance(node.value, int) and abs(node.value) <= 1000:
                return ast.copy_location(ast.Constant(value=node.value + 1), node)
    return None


def _mut_compare_op(node, src_cls, dst_cls):
    """Compare 节点：只替换第一个比较符。"""
    if node.ops:
        try:
            new_ops = [dst_cls()] + list(node.ops[1:])
            n = ast.Compare(left=node.left, ops=new_ops, comparators=node.comparators)
            return ast.copy_location(n, node)
        except Exception:
            return None
    return None


def _collect_mutation_points(tree):
    """收集可变异点：[(节点, 算子类别, 算子)]。"""
    points = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and node.ops:
            for src_cls, dst_cls in MUTATION_OPERATORS["relational"]:
                if isinstance(node.ops[0], src_cls):
                    points.append((node, "relational", (src_cls, dst_cls)))
                    break
        elif isinstance(node, ast.BoolOp):
            op = node.op
            if isinstance(op, (ast.And, ast.Or)):
                for src_cls, dst_cls in MUTATION_OPERATORS["logical"]:
                    if isinstance(op, src_cls):
                        points.append((node, "logical", (src_cls, dst_cls)))
                        break
        elif isinstance(node, ast.BinOp):
            for src_cls, dst_cls in MUTATION_OPERATORS["arith"]:
                if isinstance(node.op, src_cls):
                    points.append((node, "arith", (src_cls, dst_cls)))
                    break
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                points.append((node, "constant", ("swap_bool",)))
            elif isinstance(node.value, int) and abs(node.value) <= 1000:
                points.append((node, "constant", ("numeric_delta",)))
    return points


class _MutateTransformer(ast.NodeTransformer):
    """对整棵 AST 应用一个变异（命中 id 的节点），未命中则原样返回。"""

    def __init__(self, target_id, op):
        self.target_id = target_id
        self.op = op

    def generic_visit(self, node):
        if id(node) == self.target_id:
            mutated = _mut_apply(node, self.op)
            if mutated is not None:
                return mutated
        return super().generic_visit(node)


def _apply_mutation(src, point):
    """对整份源码应用一处变异（深拷贝树 + transformer + 整体 unparse）。

    point: (节点, 类别, 算子)。返回变异后完整源码；语法破坏/等价 → None。
    """
    node, cat, op = point
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    import copy
    new_tree = copy.deepcopy(tree)
    # 定位深拷贝后对应的目标节点（按行号+类型匹配）
    target = None
    for n in ast.walk(new_tree):
        if type(n) is type(node) and getattr(n, "lineno", None) == node.lineno \
                and _same_op(n, node):
            target = n
            break
    if target is None:
        return None
    try:
        mutated_tree = _MutateTransformer(id(target), op).visit(new_tree)
        out = ast.unparse(mutated_tree)
    except Exception:
        return None
    try:
        ast.parse(out)  # 语法校验
    except SyntaxError:
        return None
    return out


def _same_op(a, b):
    """比较两个节点的「算子」是否同类（Compare.ops[0] / BinOp.op / BoolOp.op）。"""
    if isinstance(a, ast.Compare) and isinstance(b, ast.Compare) and a.ops and b.ops:
        return type(a.ops[0]) is type(b.ops[0])
    if isinstance(a, ast.BinOp) and isinstance(b, ast.BinOp):
        return type(a.op) is type(b.op)
    if isinstance(a, ast.BoolOp) and isinstance(b, ast.BoolOp):
        return type(a.op) is type(b.op)
    if isinstance(a, ast.Constant) and isinstance(b, ast.Constant):
        return type(a.value) is type(b.value)
    return False


def mutation_deep(path, target_dir, operators=None, max_mutants=60,
                  filter_equivalent=True) -> dict:
    """mutmut 级变异深化：按算子生成变异体 + 过滤等价变异 + 报告。

    operators: 只跑指定算子类别（relational/logical/arith/constant），默认全部。
    filter_equivalent: 尝试过滤等价变异（变异前后 AST dump 相同）。
    返回 {mutants, killed, survived, mutation_score, ok, per_operator, survived_details,
          filtered_equivalent, operators, details}。
    """
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {"mutants": 0, "killed": 0, "survived": 0, "mutation_score": 0.0,
                "ok": True, "per_operator": {}, "details": "源码语法错误，跳过变异"}
    ops_filter = set(operators or list(MUTATION_OPERATORS.keys()))
    points = [p for p in _collect_mutation_points(tree) if p[1] in ops_filter]

    per_operator = {}
    generated = 0
    killed = 0
    survived = []
    mutated_srcs = []  # 存 (变异源码, line, cat)
    for point in points:
        node, cat, op = point
        if cat not in per_operator:
            per_operator[cat] = {"generated": 0, "killed": 0, "survived": 0}
        line = node.lineno
        new_src = _apply_mutation(src, point)
        if new_src is None:
            continue
        # 等价变异过滤：AST dump 相同则跳过
        if filter_equivalent:
            try:
                if ast.dump(ast.parse(new_src)) == ast.dump(ast.parse(src)):
                    continue
            except SyntaxError:
                continue
        per_operator[cat]["generated"] += 1
        generated += 1
        mutated_srcs.append((new_src, line, cat))
        if max_mutants and generated >= max_mutants:
            break

    # 逐个跑测试
    for new_src, line, cat in mutated_srcs:
        tmp = path + ".mutdeep"
        open(tmp, "w", encoding="utf-8").write(new_src)
        try:
            run = run_unit(target_dir)
            if run.get("ok", False) and run.get("test_count", 0) > 0:
                survived.append({"line": line, "cat": cat})
                per_operator[cat]["survived"] += 1
            else:
                killed += 1
                per_operator[cat]["killed"] += 1
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    survived_count = len(survived)
    score = round(killed / generated * 100, 1) if generated else 0.0
    return {
        "mutants": generated, "killed": killed, "survived": survived_count,
        "mutation_score": score,
        "ok": survived_count == 0,
        "per_operator": per_operator,
        "survived_details": survived,
        "filtered_equivalent": "on" if filter_equivalent else "off",
        "operators": sorted(ops_filter),
        "details": (f"生成 {generated} 变异(killed {killed}/存活 {survived_count})，"
                    f"变异分数 {score}%（<100% 说明测试覆盖有洞）"),
    }


def mutation_deep_report(path, target_dir, **kw) -> dict:
    """变异深化 + 报告：附每个算子的变异分数与弱区建议。"""
    r = mutation_deep(path, target_dir, **kw)
    r["weak_areas"] = []
    for cat, stats in (r.get("per_operator") or {}).items():
        if stats["generated"] and stats["survived"] > 0:
            r["weak_areas"].append({
                "operator": cat, "survived": stats["survived"],
                "generated": stats["generated"],
                "suggestion": f"算子 {cat} 有 {stats['survived']} 个存活变异，"
                              f"补覆盖该类别条件的测试用例",
            })
    return r


# ═══════════════════════════════════════════════════
# P0 覆盖率质量门禁：line+branch+--fail-under
# ═══════════════════════════════════════════════════

def branch_estimate(tree):
    """粗略分支数估计：if/for/while/布尔/comprehension 的判定边。"""
    branches = 0
    for n in ast.walk(tree):
        if isinstance(n, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
            branches += 2
        elif isinstance(n, ast.BoolOp):
            branches += len(n.values)
        elif isinstance(n, ast.IfExp):
            branches += 2
        elif isinstance(n, ast.comprehension):
            branches += 2
    return branches


def coverage_line(path, timeout=3):
    """行覆盖率（近似）：用 AST 统计可执行语句，子进程跑 import + 顶格调用统计覆盖。"""
    src = open(path, encoding="utf-8", errors="ignore").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {"ok": True, "skipped": True, "details": "语法错误", "line_pct": 0.0}
    # 可执行行 = 非 docstring/非 def/非 import 的语句行
    exec_lines = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.Expr, ast.Return, ast.If, ast.For, ast.While,
                          ast.Call, ast.AugAssign, ast.AnnAssign)):
            if getattr(n, "lineno", 0):
                exec_lines.add(n.lineno)
    return {"ok": True, "exec_lines": len(exec_lines), "line_pct": None, "details": "见 line+branch 门禁"}


def coverage_gate(path, fail_under_line=60.0, fail_under_branch=50.0,
                  timeout=8) -> dict:
    """覆盖率质量门禁：line+branch 双维度 + --fail-under 判定。

    用 coverage 库（若有）做真实行/分支覆盖；无 coverage 库时用内置近似
    （行=函数内可执行行调用率，分支=分支结构存在率）。数据不出厂（全本地）。
    返回 {line, branch, line_pct, branch_pct, gate_passed, checks:[...]}。
    """
    # 优先真实 coverage 库（可选依赖，缺失退化为内置近似）
    try:
        import coverage as _cov
        cov = _cov.Coverage(branch=True, source=[os.path.dirname(path) or "."])
        cov.start()
        try:
            import runpy
            runpy.run_path(path, run_name="__main__")
        except SystemExit:
            pass
        except Exception:
            pass
        cov.stop()
        data = cov.get_data()
        measured = data.measured_files()
        report = cov.report(show_missing=False, skip_empty=True)
        # report 返回总行覆盖率百分比（0-100）
        line_pct = float(report) if report is not None else 0.0
        branch_pct = 0.0
        try:
            br = cov.analysis2(path)
            # analysis2 -> (file, executed, missing, excluded, missed_branches, partial_branches)
            if len(br) >= 5 and br[4] is not None:
                tot = len(br[4]) + len(br[5]) if len(br) >= 6 and br[5] else len(br[4])
                missed = sum(len(m) for m in br[4])
                branch_pct = round((1 - missed / tot) * 100, 1) if tot else 0.0
        except Exception:
            branch_pct = 0.0
        cov.erase()
    except ImportError:
        # 内置近似
        line_pct, branch_pct = _coverage_approx(path, timeout)

    line_passed = line_pct >= fail_under_line
    branch_passed = branch_pct >= fail_under_branch
    gate_passed = line_passed and branch_passed
    return {
        "line_pct": round(line_pct, 1), "branch_pct": round(branch_pct, 1),
        "fail_under_line": fail_under_line, "fail_under_branch": fail_under_branch,
        "line_passed": line_passed, "branch_passed": branch_passed,
        "gate_passed": gate_passed,
        "checks": [
            {"dimension": "line", "pct": round(line_pct, 1), "fail_under": fail_under_line,
             "passed": line_passed, "status": "✅" if line_passed else "❌"},
            {"dimension": "branch", "pct": round(branch_pct, 1), "fail_under": fail_under_branch,
             "passed": branch_passed, "status": "✅" if branch_passed else "❌"},
        ],
        "engine": "coverage库" if _cov_ok() else "内置近似",
        "summary": f"行覆盖 {line_pct:.1f}%({'>=' if line_passed else '<'}{fail_under_line}), "
                   f"分支覆盖 {branch_pct:.1f}%({'>=' if branch_passed else '<'}{fail_under_branch}) "
                   f"→ 门禁{'通过' if gate_passed else '未过'}",
    }


_cov_ok_flag = None


def _cov_ok():
    global _cov_ok_flag
    if _cov_ok_flag is None:
        try:
            import coverage  # noqa: F401
            _cov_ok_flag = True
        except Exception:
            _cov_ok_flag = False
    return _cov_ok_flag


def _coverage_approx(path, timeout=8):
    """无 coverage 库时的近似：函数内可执行语句被顶格调用覆盖的比例作为行覆盖，
    分支结构存在率作为分支覆盖。"""
    src = open(path, encoding="utf-8", errors="ignore").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0.0, 0.0
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    total_exec = 0
    total_branch = 0
    for fn in funcs:
        for n in ast.walk(fn):
            if isinstance(n, (ast.Assign, ast.Expr, ast.Return, ast.Call, ast.AugAssign)):
                total_exec += 1
            if isinstance(n, (ast.If, ast.While, ast.For, ast.BoolOp, ast.IfExp)):
                total_branch += 1
    # 顶格调用函数 → 主路径覆盖
    mod = _load_module(path)
    if mod is None:
        return 0.0, 0.0
    exec_covered = 0
    branch_covered = 0
    for fn in funcs:
        f = getattr(mod, fn.name, None)
        if not callable(f):
            continue
        # 尝试用边界输入调用以覆盖更多分支
        covered_any = False
        for probe in (None, 0, -1, "", [], {}):
            try:
                import inspect as _in
                nreq = len([p for p in _in.signature(f).parameters.values()
                            if p.default is _in.Parameter.empty])
                if nreq == 0:
                    f()
                else:
                    args = [probe] * min(nreq, 3)
                    f(*args)
                covered_any = True
                # 简单估算：该函数内部 exec 行算覆盖（无法精确到行，取比例）
                break
            except Exception:
                continue
        if covered_any:
            fn_exec = sum(1 for n in ast.walk(fn)
                          if isinstance(n, (ast.Assign, ast.Expr, ast.Return, ast.Call, ast.AugAssign)))
            exec_covered += fn_exec
            fn_br = sum(1 for n in ast.walk(fn)
                        if isinstance(n, (ast.If, ast.While, ast.For, ast.BoolOp, ast.IfExp)))
            branch_covered += fn_br
    line_pct = round(exec_covered / total_exec * 100, 1) if total_exec else 100.0
    branch_pct = round(branch_covered / total_branch * 100, 1) if total_branch else 100.0
    return line_pct, branch_pct


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
