#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：review.py._static_analyze/review_file/_dep_enrich + dep_audit.build_graph
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：codereview。数据不出厂，可独立运行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import review as rv
import dep_audit as da

class CodeReviewAgent(AtomicAgent):
    name = "code-review"
    version = "0.1.0"
    domain = "codereview"
    description = "代码审查原子(多模): review.py静态+依赖图感知+复用建议"
    provides = ["codereview.review", "codereview.design", "codereview.layout", "codereview.content"]
    depends_on = ["impact.analyze"]
    inputs = ["path", "code", "mode", "use_llm", "reuse_atoms", "max_complexity"]
    outputs = ["score", "issues", "static_issues", "model", "reuse_suggestions"]

    def _register_defaults(self):
        self.register("codereview.review", self._review)
        self.register("codereview.design", lambda **kw: self._review(mode="design", **kw))
        self.register("codereview.layout", lambda **kw: self._review(mode="layout", **kw))
        self.register("codereview.content", lambda **kw: self._review(mode="content", **kw))

    def _review(self, path=None, code=None, mode="code", use_llm=False,
                reuse_atoms=True, max_complexity=10):
        if path:
            fr = rv.review_file(str(path), use_llm=use_llm,
                                max_complexity=max_complexity, reuse_atoms=reuse_atoms)
            files = {str(path): fr}
            if mode == "code":
                try:
                    graph = da.build_graph([str(path)])
                    rv._dep_enrich(fr, open(str(path), encoding="utf-8", errors="ignore").read(), graph)
                except Exception:
                    pass
        elif code:
            files = {k: (v if isinstance(v, str) else v.get("content", str(v))) for k, v in code.items()}
        else:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")

        results, issues = [], []
        for name, content in files.items():
            if isinstance(content, dict):
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

        score = int(round(sum(r["score"] for r in results) / len(results))) if results else 0
        return {"files": results, "score": score, "issues": issues,
                "static_issues": [i for r in results for i in r["static_issues"]],
                "summary": f"{mode} 审查 {len(results)} 文件, 平均分 {score}"}

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

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="code-review 原子自测入口")
    ap.add_argument("path", help="要审查的文件")
    ap.add_argument("--mode", default="code", choices=["code", "design", "layout", "content"])
    ap.add_argument("--reuse-atoms", action="store_true")
    args = ap.parse_args()
    agent.load()
    print("══ code-review 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    r = agent.run(_capability="codereview.review", path=args.path, mode=args.mode, reuse_atoms=args.reuse_atoms)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
