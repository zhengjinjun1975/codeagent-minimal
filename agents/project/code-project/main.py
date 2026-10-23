#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：audit_scan 扫描层 + review 按语言分流
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：project。数据不出厂，可独立运行。

2026-10-23 修：此前 `_collect` 硬编码 `*.py`，遇到 JS/PHP/Java 项目返回 0 文件
却报 ok=true —— 一个文件都没审，却报告「一切正常」。收文件改走 audit_scan，
零文件一律由 empty_verdict 判为非 ok。
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import audit_scan
import lang_profile


class CodeProjectAgent(AtomicAgent):
    name = "code-project"
    version = "0.2.0"
    domain = "project"
    description = "项目加载/分析: 快照+问题清单, 多语言识别(AST/文本两档)"
    provides = ["project.load", "project.scan", "project.analyze"]
    depends_on = []
    inputs = ["path", "file_pattern", "max_complexity", "impact"]
    outputs = ["files", "snapshot", "issues", "summary", "coverage"]

    def _register_defaults(self):
        self.register("project.load", self._load)
        self.register("project.scan", self._scan)
        self.register("project.analyze", self._analyze)

    def _collect(self, path, file_pattern=None):
        """收文件。pattern 为空时按语言画像收所有可识别扩展名，不再只认 .py。"""
        return audit_scan.collect(path, file_pattern)

    def _snap(self, f):
        """单文件快照。非 Python 不做 AST —— 拿不到结构就明说拿不到。"""
        info = lang_profile.classify(f)
        base = {"file": f, "language": info["language"], "support": info["support"]}
        if info["support"] != lang_profile.SUPPORT_NATIVE:
            base.update({"classes": [], "functions": [], "imports": [], "parser": None})
            return base
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError) as e:
            base.update({"classes": [], "functions": [], "imports": [],
                         "parse_error": "%s: %s" % (type(e).__name__, e)})
            return base
        base.update({
            "classes": [n.name for n in tree.body if isinstance(n, ast.ClassDef)],
            "functions": [n.name for n in ast.walk(tree)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and not n.name.startswith("_")],
            "imports": self._imports_of(tree),
            "parser": "ast",
        })
        return base

    @staticmethod
    def _imports_of(tree):
        out = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                out += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                out.append(n.module or "")
        return out

    def _load(self, path, file_pattern=None):
        """项目快照: 文件清单 + 覆盖度 + 每个文件的结构。"""
        files = self._collect(path, file_pattern)
        cov = audit_scan.coverage(files)
        if not files:
            return audit_scan.empty_verdict(path, cov)
        return {"files": files, "snapshot": [self._snap(f) for f in files],
                "count": len(files), "coverage": cov,
                "summary": audit_scan.describe(cov)}

    def _scan_one(self, f, max_complexity):
        """审一个文件。读不了的单列一条 issue，不当作「没问题」。"""
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                src = fh.read()
        except OSError as e:
            return [{"file": f, "severity": "info", "kind": "unreadable",
                     "title": "文件读不了，未检查: %s" % e}]
        import review
        static = review._analyze_dispatch(f, src, {
            "max_complexity": max_complexity, "strict_undefined": False,
            "semantic": True, "denoise": True})
        return [dict(i, file=f) for i in static["all_issues"]]

    def _scan(self, path, max_complexity=10, file_pattern=None):
        """扫描问题: 逐文件静态分析，按语言分流（Python 走 AST，其余走文本级）。"""
        files = self._collect(path, file_pattern)
        cov = audit_scan.coverage(files)
        if not files:
            return audit_scan.empty_verdict(path, cov)
        issues = []
        for f in files:
            issues.extend(self._scan_one(f, max_complexity))
        return {"issues": issues, "count": len(issues), "files": files, "coverage": cov}

    def _analyze(self, path, file_pattern=None, max_complexity=10):
        """综合分析: 快照 + 问题数 + 覆盖度。零文件同样判为非 ok。"""
        files = self._collect(path, file_pattern)
        cov = audit_scan.coverage(files)
        if not files:
            return audit_scan.empty_verdict(path, cov)
        load = self._load(path, file_pattern)
        scan = self._scan(path, max_complexity=max_complexity, file_pattern=file_pattern)
        return {"files": load["files"], "snapshot": load["snapshot"],
                "issues": scan["issues"], "issue_count": scan["count"],
                "coverage": cov,
                "summary": "%s；%d 个问题" % (audit_scan.describe(cov), scan["count"])}


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
