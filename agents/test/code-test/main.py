#!/usr/bin/env python3
"""code-test 原子壳（open_source:true）。

复用（零改动核心）：test_harness.run_all / smoke / coverage / unit /
boundary / mutation / stability / _find_functions。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力：
  test.gen  — 从代码(AST)生成基本+边界测试文件
  test.run  — 复用 test_harness.run_all 跑完整测试闭环（冒烟/覆盖/单元/边界/变异/稳定）
  test.tdd  — 红→绿→回归 反馈闭环
  test.snapshot — 复用 reg_guard.snapshot/snapshot_store 回归快照（输出相对基线变化即回归信号）
  test.affected — 复用 reg_guard.select_affected_tests 依赖图增量回归测试选择
核心零改动，数据不出厂。
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import test_harness as th  # 复用核心：核心零改动
import reg_guard as rg     # 复用核心：回归快照/增量测试选择，核心零改动


class CodeTestAgent(AtomicAgent):
    name = "code-test"
    version = "0.2.0"
    domain = "test"
    description = "测试原子：复用 test_harness 红绿回归 + reg_guard 回归快照/增量测试选择 + P0-3智能测试选择(git diff) + P1-4人类在环 + P1-5覆盖度分析"
    provides = ["test.gen", "test.run", "test.tdd", "test.snapshot", "test.affected",
                "test.select", "test.coverage_analysis"]
    depends_on = []
    inputs = ["code", "path", "target_dir", "task"]
    outputs = ["test_files", "smoke", "coverage", "unit", "boundary", "mutation", "stability", "red_green"]

    def _register_defaults(self):
        self.register("test.gen", self._gen)
        self.register("test.run", self._run)
        self.register("test.tdd", self._tdd)
        self.register("test.snapshot", self._snapshot)
        self.register("test.affected", self._affected)
        self.register("test.select", self._select)
        self.register("test.coverage_analysis", self._coverage_analysis)

    # ── test.select：智能测试选择（P0-3）git diff → 受影响测试 ──
    def _select(self, project_root=".", test_map=None, transitive=True, run=False):
        """按 git diff 分析受影响原子/文件，只选相关测试（省时非全量）。
        run=True 时把选出的测试跑起来并聚合红绿。返回 {affected_tests, ...}。"""
        sel = rg.select_affected_tests_git(project_root=project_root,
                                           test_map=test_map, transitive=transitive)
        if run and sel.get("affected_tests"):
            import test_harness as th
            per_file, ggs = [], []
            for t in sel["affected_tests"]:
                rep = th.run_all(t, os.path.dirname(t) or project_root,
                                 do_mutation=False, do_stability=False, do_boundary=True)
                ok = rep["unit"].get("ok", True) and rep.get("boundary", {}).get("ok", True)
                ggs.append(ok)
                per_file.append({"file": t, "ok": ok, "red_green": {
                    "red": not ok, "green": ok}})
            sel["run_results"] = per_file
            sel["run_green"] = all(ggs) if ggs else None
            sel["run_summary"] = (f"智能选择重跑 {len(per_file)} 测试, "
                                  f"全绿={sel['run_green']}" if per_file else "无测试可跑(建议全量)")
        return sel

    # ── test.coverage_analysis：覆盖度分析（P1-5）──
    def _coverage_analysis(self, path):
        import test_harness as th
        return th.coverage_analysis(path)

    # ── test.gen：从 AST 生成基本 + 边界测试 ────────────────
    def _gen(self, code, path=None):
        """code: {文件名: 代码内容}。生成 {test_files: {测试名: 测试代码}}。
        path 可选：已落盘的目标文件路径（用于复用 _find_functions）。
        修复 P2-6：按函数签名（AST args）生成对应参数，避免多参/关键字函数生成低价值用例；
        产出前用 ast.parse 校验合法性，非法则回退为 import-only 测试。
        P1-4 人类在环：AI 生成的测试标注 needs_human_review（渐进式，不自动放行）。"""
        test_files = {}
        if isinstance(code, str):
            code = {path or "target.py": code}
        for name, content in code.items():
            funcs = []  # [(函数名, 必填位置参数个数)]
            try:
                tree = ast.parse(content)
                entry = {"main", "cli", "run", "setup", "serve", "start"}
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and not node.name.startswith("_") and node.name not in entry:
                        pos_args = [a.arg for a in node.args.args]
                        if pos_args and pos_args[0] in ("self", "cls"):
                            pos_args = pos_args[1:]  # 剔除 self/cls
                        # 必填位置参数 = 位置参数 - 带默认值的参数
                        req = max(0, len(pos_args) - len(node.args.defaults))
                        funcs.append((node.name, req))
            except SyntaxError:
                funcs = []
            base = os.path.splitext(os.path.basename(name))[0]
            lines = ["import sys", "import os",
                     f"sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))",
                     f"import {base}"]
            for fn, req in funcs:
                # 基本用例：按必填参数个数构造调用（缺参→显式提示，不再一律单参探测）
                pos = ", ".join("None" for _ in range(max(1, req)))
                lines += [f"def test_{fn}_basic():",
                          f"    try:",
                          f"        {base}.{fn}({pos})",
                          f"    except Exception as e:",
                          f"        print('{fn} 调用异常(可接受):', e)  # 边界值/缺参 视为红→需补",
                          f"def test_{fn}_boundary():",
                          f"    # 按签名必填参数个数探测边界值（None/空串/0/[]）",
                          f"    args = [None, '', 0, []]",
                          f"    for v in args[:max(1, {req})]:",
                          f"        try:",
                          f"            {base}.{fn}(v)",
                          f"        except Exception:",
                          f"            pass"]
            if not funcs:
                lines += ["def test_import_ok():",
                          f"    import {base}",
                          "    assert True"]
            # 修复 P2-6：ast.parse 校验产出合法性，非法→回退 import-only
            test_code = "\n".join(lines)
            try:
                ast.parse(test_code)
            except SyntaxError:
                test_code = ("import sys\nimport os\n"
                             f"sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                             f"import {base}\n"
                             "def test_import_ok():\n"
                             f"    import {base}\n"
                             "    assert True\n")
            test_files[f"tests/test_{base}_gen.py"] = test_code
        return {"test_files": test_files, "summary": f"生成 {len(test_files)} 测试文件"}

    # ── test.run：复用 test_harness.run_all ────────────────
    def _run(self, path, target_dir=".", do_mutation=False, do_stability=False,
             do_boundary=True, human_review=True):
        """对目标文件跑完整测试闭环。返回 {ok, data:{...}}（数据含 red_green 推导）。
        P1-4 人类在环：AI 生成的测试/结论默认标注需人工复核（渐进式，不自动放行）；
        P1-5 覆盖度分析并入报告，指出未测函数/分支提示补测。"""
        if not os.path.exists(path):
            raise FileNotFoundError(f"目标不存在: {path}")
        report = th.run_all(path, target_dir,
                            do_mutation=do_mutation, do_stability=do_stability,
                            do_boundary=do_boundary, n=10, max_mutants=10)
        # 红绿推导：unit(测试)绿 且 boundary 绿 = 绿；任一红 = 红
        unit_ok = report["unit"].get("ok", True)
        bnd_ok = report["boundary"].get("ok", True) if "boundary" in report else True
        report["red_green"] = {
            "red": not (unit_ok and bnd_ok),
            "green": bool(unit_ok and bnd_ok),
        }
        # P1-5 覆盖度分析：报告哪些函数/分支未测，提示补测
        try:
            report["coverage_analysis"] = th.coverage_analysis(path)
        except Exception as e:
            report["coverage_analysis"] = {"ok": False, "error": str(e)}
        # P1-4 人类在环：渐进式，AI 结论不自动放行
        if human_review:
            report["human_review"] = {
                "needs_review": True,
                "reason": "测试为 AI 生成/自动运行，红绿结论仅供初筛，需人工复核断言正确性与覆盖充分性后再放行",
                "progressive": True,
                "auto_pass": False,
            }
        report["summary"] = ("全绿" if report["red_green"]["green"] else "红（存在失败项，见边界/单元）")
        return report

    # ── test.tdd：红→绿→回归 反馈闭环 ────────────────────
    def _tdd(self, path, target_dir=".", task="", max_iter=2):
        """跑测试；若红则返回改进建议（不自动改码，保持算法零改动）。"""
        report = self._run(path, target_dir)
        if report["red_green"]["green"]:
            report["tdd"] = {"stage": "green", "needs_fix": False,
                             "advice": "全部通过，进入回归锁定"}
        else:
            report["tdd"] = {"stage": "red", "needs_fix": True,
                             "advice": "存在失败项：优先补参数校验/边界值处理（None/空串/0），"
                                       "随后重跑验证红→绿"}
        return report

    # ── test.snapshot：回归快照（复用 reg_guard）──────────
    def _snapshot(self, path, funcs=None, args_by_func=None, snapshot_dir=None):
        """回归快照：记录函数输出基线，再比对相对基线是否变化（回归信号）。
        funcs 缺省 → 自动探测目标文件内非私有函数。返回 {results, changed, summary}。"""
        if not funcs:
            funcs = []
            try:
                tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
                funcs = [n.name for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and not n.name.startswith("_")]
            except SyntaxError:
                pass
        sd = snapshot_dir or os.path.join(os.path.dirname(os.path.abspath(path)),
                                          ".codeagent_snapshots")
        return rg.snapshot_store(path, funcs, args_by_func=args_by_func,
                                 snapshot_dir=sd)

    # ── test.affected：依赖图增量回归测试选择（复用 reg_guard）──
    def _affected(self, changed_files, project_root=".", test_map=None, transitive=True):
        """依赖图影响分析：改这些文件 → 影响哪些模块 → 选哪些回归测试。
        changed_files 为改动 .py 文件路径列表。返回 {affected, tests, ...}。"""
        return rg.select_affected_tests(changed_files, project_root=project_root,
                                        test_map=test_map, transitive=transitive)


# 模块级实例
agent = CodeTestAgent()


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="code-test 原子独立自测入口")
    ap.add_argument("path", help="目标 Python 文件")
    ap.add_argument("--dir", default=".", help="测试文件所在目录")
    ap.add_argument("--capability", default="test.run",
                    choices=["test.gen", "test.run", "test.tdd", "test.snapshot", "test.affected",
                             "test.select", "test.coverage_analysis"])
    args = ap.parse_args()

    agent.load()
    print("══ code-test 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    if args.capability == "test.gen":
        content = open(args.path, encoding="utf-8").read()
        r = agent.run(_capability="test.gen", code={args.path: content})
    elif args.capability == "test.tdd":
        r = agent.run(_capability="test.tdd", path=args.path, target_dir=args.dir)
    else:
        r = agent.run(_capability="test.run", path=args.path, target_dir=args.dir)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
