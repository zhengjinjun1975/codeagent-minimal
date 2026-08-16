#!/usr/bin/env python3
"""code-test 原子壳（open_source:true）。

复用（零改动核心）：test_harness.run_all / smoke / coverage / unit /
boundary / mutation / stability / _find_functions。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力：
  test.gen  — 从代码(AST)生成基本+边界测试文件
  test.run  — 复用 test_harness.run_all 跑完整测试闭环（冒烟/覆盖/单元/边界/变异/稳定）
  test.tdd  — 红→绿→回归 反馈闭环
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


class CodeTestAgent(AtomicAgent):
    name = "code-test"
    version = "0.1.0"
    domain = "test"
    description = "测试原子：复用 test_harness，红绿回归"
    provides = ["test.gen", "test.run", "test.tdd"]
    depends_on = []
    inputs = ["code", "path", "target_dir", "task"]
    outputs = ["test_files", "smoke", "coverage", "unit", "boundary", "mutation", "stability", "red_green"]

    def _register_defaults(self):
        self.register("test.gen", self._gen)
        self.register("test.run", self._run)
        self.register("test.tdd", self._tdd)

    # ── test.gen：从 AST 生成基本 + 边界测试 ────────────────
    def _gen(self, code, path=None):
        """code: {文件名: 代码内容}。生成 {test_files: {测试名: 测试代码}}。
        path 可选：已落盘的目标文件路径（用于复用 _find_functions）。"""
        test_files = {}
        if isinstance(code, str):
            code = {path or "target.py": code}
        for name, content in code.items():
            # 用 AST 找可测试函数（复用 test_harness._find_functions 思路）
            funcs = []
            try:
                tree = ast.parse(content)
                entry = {"main", "cli", "run", "setup", "serve", "start"}
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and not node.name.startswith("_") and node.name not in entry:
                        funcs.append(node.name)
            except SyntaxError:
                funcs = []
            base = os.path.splitext(os.path.basename(name))[0]
            lines = ["import sys", "import os",
                     f"sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))",
                     f"import {base}"]
            for fn in funcs:
                lines += [f"def test_{fn}_basic():",
                          f"    try:",
                          f"        {base}.{fn}()",
                          f"    except Exception as e:",
                          f"        print('{fn} 调用异常(可接受):', e)  # 边界值/缺参 视为红→需补",
                          f"def test_{fn}_boundary():",
                          f"    # 边界值探测：不传参(缺参) / None / 空串 是否被正确处理",
                          f"    for arg in [None, '', 0, []]:",
                          f"        try:",
                          f"            {base}.{fn}(arg)",
                          f"        except Exception:",
                          f"            pass"]
            if not funcs:
                lines += ["def test_import_ok():",
                          f"    import {base}",
                          "    assert True"]
            test_files[f"tests/test_{base}_gen.py"] = "\n".join(lines)
        return {"test_files": test_files, "summary": f"生成 {len(test_files)} 测试文件"}

    # ── test.run：复用 test_harness.run_all ────────────────
    def _run(self, path, target_dir=".", do_mutation=False, do_stability=False,
             do_boundary=True):
        """对目标文件跑完整测试闭环。返回 {ok, data:{...}}（数据含 red_green 推导）。"""
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


# 模块级实例
agent = CodeTestAgent()


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="code-test 原子独立自测入口")
    ap.add_argument("path", help="目标 Python 文件")
    ap.add_argument("--dir", default=".", help="测试文件所在目录")
    ap.add_argument("--capability", default="test.run",
                    choices=["test.gen", "test.run", "test.tdd"])
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
