#!/usr/bin/env python3
"""code-fuzz 原子壳（open_source:true）。

复用（零改动核心）：fuzz_engine.coverage_driven_gen / fuzz_function /
property_test / fuzz_project。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力（属性模糊测试，隔离执行，纯 stdlib）：
  fuzz.gen      — 覆盖率驱动生成用例（从 AST 分支生成针对性输入）
  fuzz.run      — 属性/模糊测试单函数（子进程隔离，防崩溃/死循环污染）
  fuzz.property — 不变量校验（随机输入调函数，校验每条 property）
  fuzz.project  — 项目级模糊（批量函数，找未处理异常）

核心零改动，数据不出厂。
"""

import os
import sys

# 让入口能 import 仓库根模块
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import fuzz_engine  # 复用核心：核心零改动


class CodeFuzzAgent(AtomicAgent):
    name = "code-fuzz"
    version = "0.1.0"
    domain = "fuzz"
    description = "属性模糊测试原子：复用 fuzz_engine，隔离执行纯 stdlib"
    provides = ["fuzz.gen", "fuzz.run", "fuzz.property", "fuzz.project"]
    depends_on = []
    inputs = ["path", "funcname", "iterations", "timeout", "seed", "max_cases",
              "properties", "max_funcs"]
    outputs = ["func", "cases", "coverage_hint", "runs", "crashed", "unhandled",
               "failures", "ok", "details"]

    def _register_defaults(self):
        self.register("fuzz.gen", self._gen)
        self.register("fuzz.run", self._run)
        self.register("fuzz.property", self._property)
        self.register("fuzz.project", self._project)

    # ── 能力实现（复用 fuzz_engine，一行不改核心）────────────────
    def _gen(self, path, funcname=None, max_cases=8):
        """覆盖率驱动生成用例。path 必填，funcname 可选（缺省扫全部函数）。"""
        return fuzz_engine.coverage_driven_gen(path, funcname=funcname,
                                               max_cases=max_cases)

    def _run(self, path, funcname, iterations=100, timeout=2.0, seed=None):
        """属性/模糊测试单函数：子进程隔离，防崩溃污染主进程。"""
        return fuzz_engine.fuzz_function(path, funcname, iterations=iterations,
                                         timeout=timeout, seed=seed)

    def _property(self, path, funcname, properties, iterations=50, timeout=2.0, seed=42):
        """不变量校验：properties 为 [(描述, callable(返回值)->bool)]。"""
        return fuzz_engine.property_test(path, funcname, properties,
                                         iterations=iterations, timeout=timeout,
                                         seed=seed)

    def _project(self, path, iterations=40, timeout=1.5, max_funcs=5):
        """项目级模糊：批量函数，找未处理异常。"""
        return fuzz_engine.fuzz_project(path, iterations=iterations, timeout=timeout,
                                        max_funcs=max_funcs)


# 模块级实例（loader 也可直接取用）
agent = CodeFuzzAgent()


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="code-fuzz 原子独立自测入口")
    ap.add_argument("path", help="目标 Python 文件")
    ap.add_argument("--funcname", default=None, help="目标函数名")
    ap.add_argument("--iterations", type=int, default=40)
    ap.add_argument("--capability", default="fuzz.gen",
                    choices=["fuzz.gen", "fuzz.run", "fuzz.property", "fuzz.project"])
    args = ap.parse_args()

    agent.load()
    print("══ code-fuzz 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    if args.capability == "fuzz.gen":
        r = agent.run(_capability="fuzz.gen", path=args.path, funcname=args.funcname)
    elif args.capability == "fuzz.run":
        fn = args.funcname or "add"
        r = agent.run(_capability="fuzz.run", path=args.path, funcname=fn,
                      iterations=args.iterations)
    elif args.capability == "fuzz.property":
        r = agent.run(_capability="fuzz.property", path=args.path,
                      funcname=args.funcname or "add",
                      properties=[("可加性", lambda v: isinstance(v, (int, float)))])
    else:
        r = agent.run(_capability="fuzz.project", path=args.path,
                      iterations=args.iterations)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
