#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：load_project/scan_issues/analyze_project(复用AST结构)
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：project。数据不出厂，可独立运行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import ast
import os

class CodeProjectAgent(AtomicAgent):
    name = "code-project"
    version = "0.1.0"
    domain = "project"
    description = "项目加载/分析: 快照+问题清单, AST零LLM"
    provides = ["project.load", "project.scan", "project.analyze"]
    depends_on = []
    inputs = ["path", "file_pattern", "max_complexity", "impact"]
    outputs = ["files", "snapshot", "issues", "summary"]

    def _register_defaults(self):
        self.register("project.load", self._load)
        self.register("project.scan", self._scan)
        self.register("project.analyze", self._analyze)

    def _collect(self, path, file_pattern="*.py"):
        if os.path.isfile(path):
            return [path]
        import fnmatch
        out = []
        for root, _d, files in os.walk(path):
            for f in files:
                # 修复 P2-5：用 fnmatch 精确匹配（"*.py" 不再误配 .pyi/.pyw/copy 等）
                if fnmatch.fnmatch(f, file_pattern) or f == file_pattern:
                    out.append(os.path.join(root, f))
        return sorted(out)

    def _load(self, path, file_pattern="*.py"):
        """项目快照: 文件清单 + 每个文件的类/函数/imports。"""
        files = self._collect(path, file_pattern)
        snapshot = []
        for f in files:
            try:
                src = open(f, encoding="utf-8", errors="ignore").read()
                tree = ast.parse(src)
                cls = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
                funcs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]
                imports = []
                for n in ast.walk(tree):
                    if isinstance(n, ast.Import):
                        imports += [a.name for a in n.names]
                    elif isinstance(n, ast.ImportFrom):
                        imports.append(n.module or "")
                snapshot.append({"file": f, "classes": cls, "functions": funcs, "imports": imports})
            except Exception:
                snapshot.append({"file": f, "classes": [], "functions": [], "imports": []})
        return {"files": files, "snapshot": snapshot, "count": len(files)}

    def _scan(self, path, max_complexity=10, impact=False):
        """扫描问题: 对每个 py 文件跑静态分析。"""
        import review as rv
        files = self._collect(path)
        issues = []
        for f in files:
            try:
                src = open(f, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            static = rv._static_analyze(src, max_complexity=max_complexity)
            for i in static["all_issues"]:
                issues.append(dict(i, file=f))
        return {"issues": issues, "count": len(issues), "files": files}

    def _analyze(self, path, file_pattern="*.py", max_complexity=10):
        """综合分析: 快照 + 问题数 + 概况。"""
        load = self._load(path, file_pattern)
        scan = self._scan(path, max_complexity=max_complexity)
        return {"files": load["files"], "snapshot": load["snapshot"],
                "issues": scan["issues"], "issue_count": scan["count"],
                "summary": f"{len(load['files'])} 文件, {scan['count']} 个问题"}


agent = CodeProjectAgent()

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="code-project 原子自测入口")
    ap.add_argument("path", help="项目根目录或文件")
    ap.add_argument("--capability", default="project.load",
                    choices=["project.load", "project.scan", "project.analyze"])
    args = ap.parse_args()
    agent.load()
    print("══ code-project 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, path=args.path)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
