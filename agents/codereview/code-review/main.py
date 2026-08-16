#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：review.py._static_analyze/review_file/_dep_enrich + dep_audit.build_graph
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：codereview。数据不出厂，可独立运行。
"""

import os
import sys
import json
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import review as rv
import dep_audit as da

class CodeReviewAgent(AtomicAgent):
    name = "code-review"
    version = "0.2.0"
    domain = "codereview"
    description = "代码审查原子(多模): review.py静态+依赖图感知+复用建议+LSP诊断(OpenCode P1-3)"
    provides = ["codereview.review", "codereview.design", "codereview.layout", "codereview.content",
                "codereview.lsp"]
    depends_on = ["impact.analyze"]
    inputs = ["path", "code", "mode", "use_llm", "reuse_atoms", "max_complexity", "lsp", "lsp_server"]
    outputs = ["score", "issues", "static_issues", "model", "reuse_suggestions", "lsp_diagnostics"]

    def _register_defaults(self):
        self.register("codereview.review", self._review)
        self.register("codereview.design", lambda **kw: self._review(mode="design", **kw))
        self.register("codereview.layout", lambda **kw: self._review(mode="layout", **kw))
        self.register("codereview.content", lambda **kw: self._review(mode="content", **kw))
        self.register("codereview.lsp", self._lsp_diag)

    def _review(self, path=None, code=None, mode="code", use_llm=False,
                reuse_atoms=True, max_complexity=10, lsp=False, lsp_server=None):
        lsp_diags = []
        if lsp:
            lsp_diags = self._run_lsp(path=path, code=code, lsp_server=lsp_server)
        if path:
            # ── path 分支：直接复用 review_file 的完整审查结果。
            #    修复 P0-1：不再把审查结果 dict 当源码二次 _static_analyze（否则安全缺陷被漏报）。
            p = str(path)
            content = open(p, encoding="utf-8", errors="ignore").read()
            fr = rv.review_file(p, use_llm=use_llm,
                                max_complexity=max_complexity, reuse_atoms=reuse_atoms)
            if mode == "code":
                try:
                    graph = da.build_graph([p])
                    rv._dep_enrich(fr, content, graph)
                except Exception:
                    pass
            r = {"file": p,
                 "score": fr.get("static_score", fr.get("score", 0)),
                 "static_issues": fr["static_issues"],
                 "issues": fr["issues"]}   # 已带 file=p 与依赖图增强
            if reuse_atoms and fr.get("reuse_suggestions"):
                r["reuse_suggestions"] = fr["reuse_suggestions"]
            if mode in ("design", "layout", "content"):
                extra = self._mode_rules(p, content, mode)
                r["issues"].extend(extra)
                r["mode_issues"] = extra
                penalty = sum(rv.SEVERITY_WEIGHTS.get(i["severity"], 3) for i in extra)
                r["score"] = max(0, r["score"] - penalty)
            results, issues = [r], r["issues"]
        elif code:
            results, issues = [], []
            for name, content in code.items():
                if not isinstance(content, str):
                    content = content.get("content", str(content))
                static = rv._static_analyze(content, max_complexity=max_complexity)
                r = {"file": name, "score": static["score"],
                     "static_issues": static["all_issues"],
                     "issues": [dict(i, file=name) for i in static["all_issues"]]}
                if reuse_atoms:
                    r["reuse_suggestions"] = rv._reuse_suggestion(content)
                if mode in ("design", "layout", "content"):
                    extra = self._mode_rules(name, content, mode)
                    r["issues"].extend(extra)
                    r["mode_issues"] = extra
                    penalty = sum(rv.SEVERITY_WEIGHTS.get(i["severity"], 3) for i in extra)
                    r["score"] = max(0, r["score"] - penalty)
                results.append(r)
                issues.extend(r["issues"])
        else:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")

        score = int(round(sum(r["score"] for r in results) / len(results))) if results else 0
        # LSP 诊断并入评分（OpenCode P1-3）：severity 1=error→重罚, 2=warn→中罚, 3=info→轻罚
        if lsp_diags:
            lsp_issues = [{"severity": _sev_map(d.get("severity", 3)),
                           "title": f"LSP[{d.get('source','lsp')}] {d.get('message','')[:80]}",
                           "suggestion": "按 LSP 诊断修复",
                           "file": path or (list(code.keys())[0] if isinstance(code, dict) and code else "?")}
                          for d in lsp_diags]
            penalty = sum(rv.SEVERITY_WEIGHTS.get(i["severity"], 3) for i in lsp_issues)
            score = max(0, score - penalty)
            issues = list(issues) + lsp_issues
        return {"files": results, "score": score, "issues": issues,
                "lsp_diagnostics": lsp_diags,
                "static_issues": [i for r in results for i in r["static_issues"]],
                "summary": f"{mode} 审查 {len(results)} 文件, 平均分 {score}"
                           + (f", LSP 诊断 {len(lsp_diags)} 项" if lsp_diags else "")}

    # ── LSP 诊断客户端（OpenCode P1-3）：连本地 LSP server 拉 diagnostics ──
    def _run_lsp(self, path=None, code=None, lsp_server=None, timeout=30):
        """连本地 LSP server（stdio JSON-RPC），对目标源码做 didOpen → 收 publishDiagnostics。
        lsp_server 为命令列表（参数列表执行，shell=False，防注入）。
        默认用仓库内置 mock LSP server（真实协议）。失败 → degraded 返回空。"""
        try:
            cmd = lsp_server or [sys.executable,
                                 os.path.join(HERE, "lsp_mock_server.py")]
            # 收集待诊断源码：优先 path，其次 code dict
            targets = []
            if path:
                p = str(path)
                try:
                    targets.append({"uri": "file:///" + p.replace("\\", "/"),
                                    "text": open(p, encoding="utf-8", errors="ignore").read()})
                except Exception:
                    pass
            if code and isinstance(code, dict):
                for name, content in code.items():
                    if not isinstance(content, str):
                        content = content.get("content", str(content))
                    targets.append({"uri": "file:///" + os.path.basename(name),
                                    "text": content})
            if not targets:
                return []
            proc = subprocess.Popen(cmd, shell=False, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            diags = []
            try:
                self._lsp_send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                      "params": {"processId": None, "rootUri": None,
                                                 "capabilities": {}}})
                self._lsp_recv(proc, timeout)   # 消费 initialize 响应，避免与 didOpen push 错位
                for t in targets:
                    self._lsp_send(proc, {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                                          "params": {"textDocument": {"uri": t["uri"],
                                                                       "languageId": "python",
                                                                       "version": 1,
                                                                       "text": t["text"]}}})
                    push = self._lsp_recv(proc, timeout)
                    if push and push.get("method") == "textDocument/publishDiagnostics":
                        ds = push.get("params", {}).get("diagnostics", [])
                        for d in ds:
                            d["uri"] = t["uri"]
                        diags.extend(ds)
            finally:
                try:
                    proc.stdin.close()
                    proc.kill()
                except Exception:
                    pass
            return diags
        except Exception:
            return []

    def _lsp_send(self, proc, msg):
        body = json.dumps(msg).encode("utf-8")
        proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
        proc.stdin.write(body)
        proc.stdin.flush()

    def _lsp_recv(self, proc, timeout):
        import time
        end = time.time() + timeout
        line = b""
        while time.time() < end:
            ch = proc.stdout.read(1)
            if not ch:
                return None
            line += ch
            if line.endswith(b"\r\n\r\n"):
                break
        else:
            return None
        length = 0
        for part in line.decode("utf-8", "ignore").split("\r\n"):
            if part.lower().startswith("content-length:"):
                try:
                    length = int(part.split(":", 1)[1].strip())
                except Exception:
                    length = 0
        body = proc.stdout.read(length) if length else b""
        try:
            return json.loads(body.decode("utf-8", "ignore"))
        except Exception:
            return None

    def _lsp_diag(self, path=None, code=None, lsp_server=None, timeout=30):
        """codereview.lsp 能力：单独跑 LSP 诊断，返回原始 diagnostics + 精简 issues。"""
        diags = self._run_lsp(path=path, code=code, lsp_server=lsp_server, timeout=timeout)
        issues = [{"severity": _sev_map(d.get("severity", 3)),
                   "title": f"LSP[{d.get('source','lsp')}] {d.get('message','')[:80]}",
                   "message": d.get("message", ""), "range": d.get("range"),
                   "uri": d.get("uri")} for d in diags]
        return {"lsp_diagnostics": diags, "issues": issues, "count": len(diags),
                "merged_into_score": len(diags) > 0}

    def _mode_rules(self, name, content, mode):
        issues = []
        if mode == "design":
            if content.count("\n\n") > 30:
                issues.append({"severity": "minor", "title": "结构松散: 过多连续空行", "suggestion": "合并连续空行为<=1"})
            if len(content.split()) < 40:
                issues.append({"severity": "minor", "title": "内容单薄: 字数过少"})
        if mode == "layout":
            if name.endswith((".html", ".css", ".vue", ".jsx")):
                if "@media" not in content and ".html" in name:
                    issues.append({"severity": "minor", "title": "缺响应式断点", "suggestion": "补 @media 适配移动端"})
            for i, line in enumerate(content.split("\n")):
                if len(line) > 160:
                    issues.append({"severity": "minor", "title": f"行过长 L{i+1}({len(line)}>160)", "suggestion": "换行拆分"})
                    break
        if mode == "content":
            for w in ("TODO", "FIXME", "lorem", "placeholder", "示例", "待补充"):
                if w.lower() in content.lower():
                    issues.append({"severity": "minor", "title": f"含占位/示例标记: {w}", "suggestion": "替换为真实内容"})
                    break
        return issues


agent = CodeReviewAgent()

# LSP severity → CodeAgent severity 映射（1=Error→critical, 2=Warning→major, 3=Info/Hint→minor）
def _sev_map(lsp_sev):
    return {1: "critical", 2: "major", 3: "minor", 4: "minor"}.get(lsp_sev, "minor")

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="code-review 原子自测入口")
    ap.add_argument("path", help="要审查的文件")
    ap.add_argument("--mode", default="code", choices=["code", "design", "layout", "content"])
    ap.add_argument("--reuse-atoms", action="store_true")
    ap.add_argument("--lsp", action="store_true", help="启用 LSP 诊断(并入评分)")
    ap.add_argument("--capability", default="codereview.review",
                    choices=["codereview.review", "codereview.lsp"])
    args = ap.parse_args()
    agent.load()
    print("══ code-review 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    if args.capability == "codereview.lsp":
        r = agent.run(_capability="codereview.lsp", path=args.path)
    else:
        r = agent.run(_capability="codereview.review", path=args.path, mode=args.mode,
                      reuse_atoms=args.reuse_atoms, lsp=args.lsp)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
