#!/usr/bin/env python3
"""guard 原子壳（open_source:true）——护栏钩子（P2）。

借鉴 Codex `guardian`（预/后置审查流水线），把 security-scan 复用为可注入审查钩子：
code 变更前 guard.pre 快速安全门（10维度+secret），变更后 guard.post 复核，guard.pipeline
对一批文件跑钩子流水线。复用 security_scan.scan_security/detect_secrets/govern_false_positives。

能力（纯 stdlib，数据不出厂）：
  guard.pre    — 前置安全钩子（快速门禁）：10维度+secret检测，输出 verdict
  guard.post   — 后置复核钩子（同 pre + 误报治理），可给变更文件复核
  guard.pipeline — 对路径列表跑 guard.pre 流水线，聚合 verdict
  guard.check  — 通用安全门禁（单文件/代码串）
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import security_scan as ss


def _verdict(issues, secrets):
    """门禁判定：对齐 security_scan 严重度 schema（tier∈{P0,P1,P2}, severity∈{critical,major,minor}）：
    有 P0 / critical / high 严重 issue 或 secret → fail；有其它 issue → warn；否则 pass。"""
    def _block(i):
        t = str(i.get("tier") or "").upper()
        s = str(i.get("severity") or "").lower()
        return t == "P0" or s in ("critical", "high")
    if any(_block(i) for i in issues) or secrets:
        return "fail"
    if issues:
        return "warn"
    return "pass"


_COMMAND_EXEC_PATTERNS = [
    (r"\bos\.system\s*\(", "os.system 命令执行"),
    (r"\bos\.popen\s*\(", "os.popen 命令执行"),
    (r"\bos\.spawn[a-z]*\s*\(", "os.spawn 命令执行"),
    (r"subprocess\.(?:Popen|run|call|check_call)\s*\([^)]*\bshell\s*=\s*True", "subprocess shell=True 命令注入"),
    (r"\beval\s*\(|\bexec\s*\(", "动态代码执行(eval/exec)"),
]


def _command_exec_issues(content: str) -> list:
    """护栏自检：补 scan_security 未覆盖的危险命令执行（静态 os.system / subprocess shell=True 等），
    一律 critical/P0。scan_security 仅当 os.system 参数是字符串拼接时才报注入，静态 `rm -rf /`
    不报，故 guard 需独立补一道命令执行门禁，方能拦住此类危险调用。"""
    out = []
    if not isinstance(content, str):
        return out
    for pat, title in _COMMAND_EXEC_PATTERNS:
        if re.search(pat, content):
            out.append({"dimension": "注入", "severity": "critical", "tier": "P0",
                        "title": f"危险命令执行: {title}", "source": "guard", "line": 0})
    return out


class GuardAgent(AtomicAgent):
    name = "guard"
    version = "0.1.0"
    domain = "guard"
    description = ("护栏钩子原子（P2，借鉴Codex guardian）: code变更前置/后置安全审查钩子流水线, "
                   "复用security_scan 10维度+secret+误报治理做门禁(pre/post/pipeline/check)。纯stdlib数据不出厂。")
    provides = ["guard.pre", "guard.post", "guard.pipeline", "guard.check"]
    depends_on = []
    inputs = ["path", "code", "paths", "govern"]
    outputs = ["issues", "secrets", "verdict", "files", "summary", "total"]

    def _register_defaults(self):
        self.register("guard.pre", self._pre)
        self.register("guard.post", self._post)
        self.register("guard.pipeline", self._pipeline)
        self.register("guard.check", self._check)

    def _content(self, path=None, code=None):
        if path:
            return open(str(path), encoding="utf-8", errors="ignore").read()
        if isinstance(code, str):
            return code
        if isinstance(code, dict):
            return list(code.values())[0]
        return None

    def _scan(self, path=None, code=None, govern=False):
        content = self._content(path, code)
        if content is None:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")
        issues = ss.scan_security(content).get("issues", [])
        issues = issues + _command_exec_issues(content)
        secrets = ss.detect_secrets(content)
        if govern:
            issues = ss.govern_false_positives(issues)
        return {"issues": issues, "secrets": secrets,
                "total": len(issues) + len(secrets),
                "verdict": _verdict(issues, secrets),
                "file": str(path) if path else None,
                "summary": f"guard: {len(issues)} issue / {len(secrets)} secret → {_verdict(issues, secrets)}"}

    def _pre(self, path=None, code=None):
        return self._scan(path=path, code=code)

    def _post(self, path=None, code=None, govern=True):
        return self._scan(path=path, code=code, govern=govern)

    def _check(self, path=None, code=None, govern=False):
        return self._scan(path=path, code=code, govern=govern)

    def _pipeline(self, paths=None):
        if not paths:
            return self._envelope(False, degraded=True, error="缺 paths 入参")
        files = []
        for p in paths:
            r = self._scan(path=p)
            if r.get("ok", True) is False:
                continue
            files.append({"path": str(p), "verdict": r["verdict"],
                          "total": r["total"]})
        worst = "pass"
        for f in files:
            if f["verdict"] == "fail":
                worst = "fail"
                break
            if f["verdict"] == "warn":
                worst = "warn"
        return {"files": files, "count": len(files), "verdict": worst,
                "summary": f"guard pipeline: {len(files)} 文件, 门禁={worst}"}


agent = GuardAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(GuardAgent(), run_args={
        "capability": {"default": "guard.check", "choices": list(GuardAgent.provides)},
        "path": {}, "code": {}, "paths": {},
    }))
