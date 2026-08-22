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
import ast

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import review as rv
import dep_audit as da

class CodeReviewAgent(AtomicAgent):
    name = "code-review"
    version = "0.2.1"
    domain = "codereview"
    description = ("代码审查原子(多模+5方向): review.py静态+语义+依赖图+LSP(P1-3)+P0/P1/P2去噪+"
                   "人类在环; 5方向: 轻审light(增量git diff)+重审deep(数据流+双引擎对抗)")
    provides = ["codereview.review", "codereview.design", "codereview.layout", "codereview.content",
                "codereview.lsp", "codereview.semantic", "codereview.self_eval",
                "codereview.light", "codereview.deep"]
    depends_on = ["impact.analyze"]
    inputs = ["path", "code", "mode", "use_llm", "reuse_atoms", "max_complexity", "lsp", "lsp_server",
              "semantic", "denoise", "base"]
    outputs = ["score", "issues", "static_issues", "model", "reuse_suggestions", "lsp_diagnostics",
               "severity_summary", "self_eval", "security_baseline", "dataflow_findings",
               "adversarial", "changed_files"]

    def _register_defaults(self):
        self.register("codereview.review", self._review)
        self.register("codereview.design", lambda **kw: self._review(mode="design", **kw))
        self.register("codereview.layout", lambda **kw: self._review(mode="layout", **kw))
        self.register("codereview.content", lambda **kw: self._review(mode="content", **kw))
        self.register("codereview.lsp", self._lsp_diag)
        self.register("codereview.semantic", self._semantic_review)
        self.register("codereview.self_eval", self._self_eval)
        self.register("codereview.light", self._light_review)
        self.register("codereview.deep", self._deep_review)

    # ── 5方向: codereview.light 轻审（增量扫描 git diff，快速静态+安全基线）──
    def _light_review(self, path, base="HEAD", max_complexity=10):
        """轻审: git diff 只扫变更文件, 快速静态 + 安全基线(CI 增量门禁)。"""
        if not path:
            return self._envelope(False, degraded=True, error="缺 path 入参")
        return rv.light_review(str(path), base=base, max_complexity=max_complexity)

    # ── 5方向: codereview.deep 重审（数据流污点追踪 + 双引擎静态+对抗验证）──
    def _deep_review(self, path=None, code=None, max_complexity=10, denoise=True):
        """重审: 数据流分析(变量传播/污点追踪) + 双引擎(静态+AI语义对抗性审查先假设误报证伪)。"""
        if path:
            return rv.deep_review(str(path), max_complexity=max_complexity, denoise=denoise)
        if code:
            # code 字典 → 逐文件 deep_review 到临时文件（保持信封结构）
            import tempfile
            name = list(code.keys())[0]
            c = code[name]
            content = c.get("content", c) if isinstance(c, dict) else c
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                             encoding="utf-8") as f:
                f.write(content)
                tmp = f.name
            try:
                r = rv.deep_review(tmp, max_complexity=max_complexity, denoise=denoise)
                r["file"] = name
                return r
            finally:
                try: os.remove(tmp)
                except OSError: pass
        return self._envelope(False, degraded=True, error="缺 path 或 code 入参")

    # ── codereview.semantic：纯逐行语义审查（P0-1 深度模式）──
    def _semantic_review(self, path=None, code=None, denoise=True):
        """只跑逐行语义审查 + 缺陷根因库，定位高影响 bug/边界/断链（非静态规则）。
        返回 {issues, severity_summary, semantic_count, summary}。"""
        issues = []
        if path:
            content = open(str(path), encoding="utf-8", errors="ignore").read()
            try:
                tree = ast.parse(content)
                issues = rv._static_check_semantic(tree) + rv._check_known_defects(content, content.split("\n"))
            except SyntaxError:
                issues = [{"severity": "critical", "title": "语法错误", "line": 0,
                           "suggestion": "先修语法", "semantic": True}]
        elif code and isinstance(code, dict):
            for name, content in code.items():
                if not isinstance(content, str):
                    content = content.get("content", str(content))
                try:
                    tree = ast.parse(content)
                    issues += [dict(i, file=name) for i in
                               (rv._static_check_semantic(tree) + rv._check_known_defects(content, content.split("\n")))]
                except SyntaxError:
                    issues.append({"severity": "critical", "title": f"语法错误 {name}", "file": name,
                                   "line": 0, "semantic": True})
        else:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")
        if denoise:
            issues = [rv._classify_tier(i) for i in issues]
        by_tier = {"P0": 0, "P1": 0, "P2": 0}
        for i in issues:
            by_tier[i.get("tier", rv.SEVERITY_TIER.get(i.get("severity", "minor"), "P2"))] += 1
        return {"issues": issues, "severity_summary": by_tier,
                "semantic_count": len(issues),
                "summary": f"逐行语义审查 {len(issues)} 项 (P0={by_tier['P0']}/P1={by_tier['P1']}/P2={by_tier['P2']})",
                # P1-4 人类在环：语义审查结论为 AI 生成，需人工复核后再放行（渐进式，不自动放行）
                "human_review": {
                    "needs_review": True,
                    "reason": "逐行语义审查结论由 AI 生成，P0/P1/P2 分级仅供初筛，需人工复核高影响/边界/断链项真实性后再放行",
                    "progressive": True,
                    "auto_pass": False,
                }}

    # ── codereview.self_eval：审查质量自评（P2-7）──
    def _self_eval(self, file=None, findings=None, missed=None, fp=None, stats_only=False):
        """记录/查看审查自评（漏bug/误报率），迭代优化 review 原子。
        stats_only=True 只读累计统计；否则记录一次。"""
        if stats_only:
            return rv.self_eval_stats()
        if file is None:
            return self._envelope(False, degraded=True, error="需传 file 记录自评")
        return rv.self_eval_record(file, findings or [], missed=missed, extra_fp=fp)

    def _review(self, path=None, code=None, mode="code", use_llm=False,
                reuse_atoms=True, max_complexity=10, lsp=False, lsp_server=None,
                semantic=True, denoise=True):
        lsp_diags = []
        if lsp:
            lsp_diags = self._run_lsp(path=path, code=code, lsp_server=lsp_server)
        if path:
            # ── path 分支：直接复用 review_file 的完整审查结果。
            #    修复 P0-1：不再把审查结果 dict 当源码二次 _static_analyze（否则安全缺陷被漏报）。
            p = str(path)
            content = open(p, encoding="utf-8", errors="ignore").read()
            fr = rv.review_file(p, use_llm=use_llm,
                                max_complexity=max_complexity, reuse_atoms=reuse_atoms,
                                semantic=semantic, denoise=denoise)
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
            if fr.get("severity_summary"):
                r["severity_summary"] = fr["severity_summary"]
            if reuse_atoms and fr.get("reuse_suggestions"):
                r["reuse_suggestions"] = fr["reuse_suggestions"]
            if mode in ("design", "layout", "content"):
                extra = self._mode_rules(p, content, mode)
                r["issues"].extend(extra)
                r["mode_issues"] = extra
                if denoise:
                    extra = [rv._classify_tier(i) for i in extra]
                penalty = sum(rv.SEVERITY_WEIGHTS.get(i["severity"], 3) for i in extra)
                r["score"] = max(0, r["score"] - penalty)
            results, issues = [r], r["issues"]
        elif code:
            results, issues = [], []
            for name, content in code.items():
                if not isinstance(content, str):
                    content = content.get("content", str(content))
                static = rv._static_analyze(content, max_complexity=max_complexity,
                                            semantic=semantic, denoise=denoise)
                r = {"file": name, "score": static["score"],
                     "static_issues": static["all_issues"],
                     "issues": [dict(i, file=name) for i in static["all_issues"]],
                     "severity_summary": static.get("severity_summary", {})}
                if reuse_atoms:
                    r["reuse_suggestions"] = rv._reuse_suggestion(content)
                if mode in ("design", "layout", "content"):
                    extra = self._mode_rules(name, content, mode)
                    r["issues"].extend(extra)
                    r["mode_issues"] = extra
                    if denoise:
                        extra = [rv._classify_tier(i) for i in extra]
                    penalty = sum(rv.SEVERITY_WEIGHTS.get(i["severity"], 3) for i in extra)
                    r["score"] = max(0, r["score"] - penalty)
                results.append(r)
                issues.extend(r["issues"])
        else:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")

        score = int(round(sum(r["score"] for r in results) / len(results))) if results else 0
        # P0-2 严重度去噪：全量聚合 P0/P1/P2 分级计数
        by_tier = {"P0": 0, "P1": 0, "P2": 0}
        for r in results:
            for i in r.get("issues", []):
                t = i.get("tier", rv.SEVERITY_TIER.get(i.get("severity", "minor"), "P2"))
                by_tier[t] = by_tier.get(t, 0) + 1
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
                "severity_summary": by_tier,
                "lsp_diagnostics": lsp_diags,
                "static_issues": [i for r in results for i in r["static_issues"]],
                "summary": f"{mode} 审查 {len(results)} 文件, 平均分 {score}"
                           + (f", LSP 诊断 {len(lsp_diags)} 项" if lsp_diags else ""),
                # P1-4 人类在环：审查结论为 AI 生成，需人工复核后再放行（渐进式，不自动放行）
                "human_review": {
                    "needs_review": True,
                    "reason": "审查结论由 AI 静态+语义分析生成，P0/P1/P2 分级仅供初筛，需人工复核每条 issue 的真实性/影响面/修复建议后再放行",
                    "progressive": True,
                    "auto_pass": False,
                }}

    # ── LSP 诊断客户端（OpenCode P1-3）：连本地 LSP server 拉 diagnostics ──
    def _run_lsp(self, path=None, code=None, lsp_server=None, timeout=30):
        """连本地 LSP server（stdio JSON-RPC），对目标源码做 didOpen → 收 publishDiagnostics。
        lsp_server 为命令列表（参数列表执行，shell=False，防注入）。
        默认用仓库内置 mock LSP server（真实协议）。失败 → degraded 返回空。
        用后台 reader 线程 + 队列收帧，规避 Windows 管道 read(1) 阻塞。"""
        try:
            cmd = lsp_server or [sys.executable,
                                 os.path.join(HERE, "lsp_mock_server.py")]
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
            # 后台 reader 线程：逐帧解析 push，放进队列
            import queue, threading
            fq = queue.Queue()
            def _reader():
                try:
                    while True:
                        frame = self._lsp_read_frame(proc.stdout)
                        if frame is None:
                            break
                        fq.put(frame)
                except Exception:
                    pass
            thr = threading.Thread(target=_reader, daemon=True)
            thr.start()
            try:
                self._lsp_send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                      "params": {"processId": None, "rootUri": None,
                                                 "capabilities": {}}})
                diags = []
                for t in targets:
                    self._lsp_send(proc, {"jsonrpc": "2.0", "method": "textDocument/didOpen",
                                          "params": {"textDocument": {"uri": t["uri"],
                                                                       "languageId": "python",
                                                                       "version": 1,
                                                                       "text": t["text"]}}})
                    # 收 publishDiagnostics（带超时）
                    try:
                        while True:
                            push = fq.get(timeout=timeout)
                            if push.get("method") == "textDocument/publishDiagnostics":
                                ds = push.get("params", {}).get("diagnostics", [])
                                for d in ds:
                                    d["uri"] = t["uri"]
                                diags.extend(ds)
                                break
                    except Exception:
                        break   # 超时无 push → 跳过该文件
                return diags
            finally:
                try:
                    proc.stdin.close()
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            return []

    def _lsp_send(self, proc, msg):
        body = json.dumps(msg).encode("utf-8")
        proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
        proc.stdin.write(body)
        proc.stdin.flush()

    @staticmethod
    def _lsp_read_frame(stream):
        """从流读一个 LSP 帧（Content-Length 头 + body）。返回 dict 或 None(EOF)。"""
        import queue, threading
        # 读头（\r\n\r\n 结束）
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            ch = stream.read(1)
            if not ch:
                return None
            header += ch
            if len(header) > 4096:
                return None
        length = 0
        for part in header.decode("utf-8", "ignore").split("\r\n"):
            if part.lower().startswith("content-length:"):
                try:
                    length = int(part.split(":", 1)[1].strip())
                except Exception:
                    length = 0
        body = stream.read(length) if length else b""
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
