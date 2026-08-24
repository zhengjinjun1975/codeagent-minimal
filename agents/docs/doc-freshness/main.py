#!/usr/bin/env python3
"""doc-freshness 原子壳（open_source:true）。

复用（零改动核心）：doc_freshness.audit_dir / audit_file / extract_anchors / audit_anchor。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力：
  doc.anchor — 文档代码锚点审计（repo://path#L-L / path.py:NN / 符号引用 → 逐一确定性校验）
  doc.stale  — P0 新鲜度报告（stale 证据变更 + unresolved 证据消失）
借鉴 OpenWiki 证据版本化 + preflight 确定性检测。零 LLM，数据不出厂。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import doc_freshness  # 复用核心：核心零改动


class DocFreshnessAgent(AtomicAgent):
    name = "doc-freshness"
    version = "0.1.0"
    domain = "docs"
    description = "文档新鲜度审计：代码锚点确定性校验（stale/unresolved），借鉴 OpenWiki 证据版本化"
    provides = ["doc.anchor", "doc.stale"]
    depends_on = []
    inputs = ["path", "root"]
    outputs = ["file", "anchors", "ok_count", "stale", "unresolved", "total_anchors", "stale_count", "unresolved_count"]

    def _register_defaults(self):
        self.register("doc.anchor", self._anchor)
        self.register("doc.stale", self._stale)

    # ── 能力实现（复用 doc_freshness，一行不改核心）────────────────
    def _anchor(self, path, root):
        """文档锚点审计。path 为 md 或 docs 目录；root 为仓库根（锚点解析基准）。"""
        if os.path.isdir(path):
            return doc_freshness.audit_dir(root, path)
        return doc_freshness.audit_file(root, path)

    def _stale(self, path, root):
        """P0 新鲜度报告：仅返回 stale（证据变更）+ unresolved（证据消失）清单。"""
        r = doc_freshness.audit_dir(root, path) if os.path.isdir(path) else doc_freshness.audit_file(root, path)
        stale, unresolved = [], []
        for fr in r.get("reports", [r]):
            stale.extend({"file": fr["file"], "anchor": a} for a in fr.get("stale", []))
            unresolved.extend({"file": fr["file"], "anchor": a} for a in fr.get("unresolved", []))
        return {"stale": stale, "unresolved": unresolved,
                "stale_count": len(stale), "unresolved_count": len(unresolved)}


# 模块级实例（loader 也可直接取用）
agent = DocFreshnessAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="doc-freshness 原子独立自测入口")
    ap.add_argument("path", help="md 文件或 docs 目录")
    ap.add_argument("--root", default=REPO_ROOT, help="仓库根")
    ap.add_argument("--capability", default="doc.anchor", choices=["doc.anchor", "doc.stale"])
    args = ap.parse_args()

    agent.load()
    print("══ doc-freshness 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, path=args.path, root=args.root)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
