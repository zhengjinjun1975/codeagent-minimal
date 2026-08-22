#!/usr/bin/env python3
"""minimalist-style 原子壳（open_source:true）——P0 极简风格审查。

针对用户个人代码定位「极简编码流程」的配套原子：审查代码是否做到——
  纯标准库 / 不过度依赖 / 不炫技 / 可独立部署。
纯 stdlib 实现（自身也践行极简），数据不出厂，可独立运行。

能力：
  minimal.style       — 极简风格审查：解析 AST，检第三方依赖/炫技写法/过度工程信号
  minimal.deps        — 依赖审计：列出全部 import，区分 stdlib / 第三方 / 本地
  minimal.independent — 独立部署性检查：无第三方依赖 + 无硬编码外部绝对路径
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

# 本地代码原子壳库自身算「本地/标准库」，不算第三方依赖
_LOCAL_PREFIXES = ("codeagent", "atomic_base", "agent_loader", "agent_runtime",
                   "bug_deep", "chain_break", "complexity", "dep_audit", "dep_scan",
                   "dispatch", "fuzz_engine", "known_defects", "legacy_cli",
                   "pathguard", "reg_guard", "review", "security_scan", "self_evolve",
                   "test_harness", "approval_policy", "arch_review", "codeagent")

# 炫技/不极简信号（AST node → 说明）
_OPAQUE_CALLS = {"eval", "exec", "compile", "__import__", "getattr",
                 "setattr", "globals", "locals", "vars"}

# 硬编码绝对路径前缀（Windows 盘符 / POSIX 根）——破坏「可独立部署」
_ABS_PREFIX = ("C:\\", "D:\\", "E:\\", "F:\\", "c:\\", "d:\\", "e:\\", "f:\\",
               "/home/", "/usr/", "/opt/", "/Users/", "/root/")


def _py_files(root):
    if os.path.isfile(root) and root.endswith(".py"):
        return [root]
    out = []
    if os.path.isdir(root):
        for r, _d, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    out.append(os.path.join(r, f))
    return out


def _collect_imports(tree):
    """返回 {top_module: {kind, lineno}}。"""
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                imports.setdefault(top, {"kind": "import", "lineno": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top:
                imports.setdefault(top, {"kind": "from", "lineno": node.lineno})
    return imports


def _is_stdlib(top):
    return top in getattr(sys, "stdlib_module_names", set())


def _is_local(top):
    return any(top == p or top.startswith(p + "_") or p.startswith(top + "_")
               for p in _LOCAL_PREFIXES) or os.path.exists(os.path.join(REPO_ROOT, top + ".py"))


def _opaque_score(tree):
    """炫技/晦涩信号统计。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _OPAQUE_CALLS:
                found.append({"signal": f"晦涩动态调用 {node.func.id}()", "lineno": node.lineno})
        if isinstance(node, ast.NamedExpr):  # 海象
            found.append({"signal": "海象赋值 := (炫技)", "lineno": getattr(node, "lineno", 0)})
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for p in _ABS_PREFIX:
                if node.value.startswith(p):
                    found.append({"signal": f"硬编码绝对路径 {node.value[:40]}", "lineno": getattr(node, "lineno", 0)})
                    break
    return found


class MinimalistStyleAgent(AtomicAgent):
    name = "minimalist-style"
    version = "0.1.0"
    domain = "codereview"
    description = ("极简风格审查原子（P0）: 解析 AST 审查代码是否纯标准库/不过度依赖/不炫技/"
                   "可独立部署。纯 stdlib 数据不出厂。")
    provides = ["minimal.style", "minimal.deps", "minimal.independent"]
    depends_on = []
    inputs = ["path", "code", "strict"]
    outputs = ["ok", "score", "issues", "third_party", "signals", "verdict"]

    def _register_defaults(self):
        self.register("minimal.style", self._style)
        self.register("minimal.deps", self._deps)
        self.register("minimal.independent", self._independent)

    def _sources(self, path=None, code=None):
        """返回 [(relname, source)]。"""
        if code is not None:
            return [("<inline>", code)]
        files = _py_files(path) if path else []
        out = []
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    out.append((f, fh.read()))
            except (OSError, UnicodeDecodeError):
                pass
        return out

    def _style(self, path=None, code=None, strict=False):
        srcs = self._sources(path, code)
        if not srcs:
            return self._envelope(False, degraded=True, error="无 .py 可审查: path/code 为空")
        issues, third_party = [], {}
        opaque, total = 0, 0
        for name, src in srcs:
            try:
                tree = ast.parse(src)
            except SyntaxError:
                issues.append({"file": name, "signal": "语法错误, 无法解析", "lineno": 0})
                continue
            total += 1
            for top, info in _collect_imports(tree).items():
                if _is_stdlib(top) or _is_local(top):
                    continue
                third_party[top] = info["lineno"]
                issues.append({"file": name, "signal": f"第三方依赖 import {top}",
                               "lineno": info["lineno"]})
            for s in _opaque_score(tree):
                opaque += 1
                issues.append({"file": name, "signal": s["signal"], "lineno": s["lineno"]})
        score = 100
        if third_party:
            score -= 15 * min(len(third_party), 4)
        score -= 5 * min(opaque, 4)
        score = max(0, score)
        verdict = "极简合规" if (not third_party and opaque == 0) else \
                  ("基本极简(小瑕疵)" if score >= 70 else "过度复杂/依赖需收敛")
        return {"ok": not third_party and opaque == 0, "score": score,
                "issues": issues, "third_party": third_party,
                "signals": opaque, "files": total, "verdict": verdict}

    def _deps(self, path=None, code=None):
        srcs = self._sources(path, code)
        stdlib, third_party, local = {}, {}, {}
        for name, src in srcs:
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for top, info in _collect_imports(tree).items():
                bucket = local if _is_local(top) else (stdlib if _is_stdlib(top) else third_party)
                bucket.setdefault(top, {"lineno": info["lineno"], "files": set()})["files"].add(name)
        return {
            "stdlib": sorted(stdlib),
            "third_party": sorted(third_party),
            "local": sorted(local),
            "independent_deployable": not third_party,
            "verdict": "纯标准库, 可独立部署" if not third_party
                       else f"含 {len(third_party)} 个第三方依赖, 需 pip 安装/封装",
        }

    def _independent(self, path=None, code=None):
        d = self._deps(path, code)
        abs_refs = []
        for name, src in self._sources(path, code):
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value.startswith(_ABS_PREFIX):
                        abs_refs.append({"file": name, "value": node.value[:50],
                                         "lineno": getattr(node, "lineno", 0)})
        independent = not d["third_party"] and not abs_refs
        return {"ok": independent, "independent_deployable": independent,
                "third_party": d["third_party"],
                "hardcoded_abs_paths": abs_refs,
                "verdict": "可独立部署" if independent
                           else ("硬编码绝对路径破坏可移植性" if abs_refs
                                 else "第三方依赖需随包分发")}


agent = MinimalistStyleAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(MinimalistStyleAgent(), run_args={
        "capability": {"default": "minimal.style", "choices": list(MinimalistStyleAgent.provides)},
        "path": {}, "code": {}, "strict": {},
    }))
