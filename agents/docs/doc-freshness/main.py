#!/usr/bin/env python3
"""doc-freshness 原子壳（open_source:true）。

复用（零改动核心）：doc_freshness.audit_dir / audit_file / extract_anchors / audit_anchor。
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力：
  doc.anchor — 文档代码锚点审计（repo://path#L-L / path.py:NN / 符号引用 → 逐一确定性校验）
  doc.stale  — P0 新鲜度报告（stale 证据变更 + unresolved 证据消失）
  doc.draft  — P0-1 建议性增量重写（默认 off，独立能力，不改只读行为）：对 stale 条目按
               managed/手写两区纪律产出「补丁草案」（只针对 managed marker 区提，绝不直接改、
               绝不覆盖手写区）。仅当显式传 out 目录时才把草案 JSON 落盘到 out（源文档仍只读）。
借鉴 OpenWiki 证据版本化 + AGENTS.md marker 两区协议。零 LLM，数据不出厂。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import doc_freshness  # 复用核心：只读审计内核零改动，新增建议草案函数一并复用


class DocFreshnessAgent(AtomicAgent):
    name = "doc-freshness"
    version = "0.2.0"
    domain = "docs"
    description = ("文档新鲜度审计 + P0-1 建议性增量重写草案：代码锚点确定性校验（stale/unresolved），"
                   "对 managed 区 stale 条目产补丁草案供 review（只报不改，手写区保护）。借鉴 OpenWiki")
    provides = ["doc.anchor", "doc.stale", "doc.draft"]
    depends_on = []
    inputs = ["path", "root"]
    outputs = ["file", "anchors", "ok_count", "stale", "unresolved", "total_anchors",
               "stale_count", "unresolved_count", "draft_count", "guarded_count",
               "managed_zones", "drafts", "guarded"]

    def _register_defaults(self):
        self.register("doc.anchor", self._anchor)
        self.register("doc.stale", self._stale)
        self.register("doc.draft", self._draft)

    # ── 能力实现（复用 doc_freshness，一行不改只读审计核心）────────────────
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

    def _draft(self, path, root, out=None, **_):
        """P0-1 建议性增量重写草案（默认 off：仅当显式调用本能力/传 out 才产出，不改只读审计）。

        path 为单个 md（建议性重写以单文件为粒度）；对每个 stale 条目按 managed/手写两区
        纪律产补丁草案：managed marker 区（如 `<!-- DOC:START -->…<!-- DOC:END -->`）内
        的 stale → 产 draft（建议刷新锚点哈希）；marker 块外手写区 → guarded 保护，绝不覆盖。
        源文档始终只读；仅当传 out 目录时才把草案 JSON 落盘到 out/<basename>.draft.json。
        """
        md_files = [path] if os.path.isfile(path) else [
            os.path.join(path, p) for p in os.listdir(path)
            if p.lower().endswith((".md", ".txt", ".rst"))]
        drafts_all, guarded_all, zones_all = [], [], []
        for f in md_files:
            d = doc_freshness.suggest_drafts(root, f)
            if not d.get("readable"):
                continue
            drafts_all.extend(d["drafts"])
            guarded_all.extend(d["guarded"])
            if d["managed_zones"]:
                zones_all.extend(d["managed_zones"])
        result = {
            "managed_zone_count": len(zones_all),
            "drafts": drafts_all, "draft_count": len(drafts_all),
            "guarded": guarded_all, "guarded_count": len(guarded_all),
            "note": ("建议性增量重写草案：只针对 managed 区提，绝不直接改文档、绝不覆盖手写区。"
                     "CodeAgent/人 review 通过后才应用。"),
        }
        if out:
            os.makedirs(out, exist_ok=True)
            base = os.path.basename(os.path.normpath(path)).rsplit(".", 1)[0]
            outfile = os.path.join(out, f"{base}.draft.json")
            with open(outfile, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
            result["draft_file"] = outfile
            result["note"] = result["note"] + f" 草案已写入（源文档未改动）: {outfile}"
        return result


# 模块级实例（loader 也可直接取用）
agent = DocFreshnessAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="doc-freshness 原子独立自测入口")
    ap.add_argument("path", help="md 文件或 docs 目录")
    ap.add_argument("--root", default=REPO_ROOT, help="仓库根")
    ap.add_argument("--capability", default="doc.anchor",
                    choices=["doc.anchor", "doc.stale", "doc.draft"])
    ap.add_argument("--out", default=None,
                    help="doc.draft 专用：草案 JSON 落盘目录（源文档保持只读）；不传则仅内存返回")
    args = ap.parse_args()

    agent.load()
    print("══ doc-freshness 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, path=args.path, root=args.root,
                  **({"out": args.out} if args.out else {}))
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
