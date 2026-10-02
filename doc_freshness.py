#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc_freshness.py — 文档新鲜度审计（纯标准库零依赖）。

借鉴 OpenWiki 的「证据版本化 + preflight 确定性检测」：把"文档过期"变成可被程序
确定性检测的事实。扫 Markdown/文本里对源码的引用锚点，对每个锚点做三件事：
  1. 文件在不在            → 不在 = unresolved（证据消失）
  2. 行区间内容哈希变没变   → 变了 = stale（版本变更），并给出当前哈希
  3. 符号引用还在不在       → 不在了 = unresolved（符号被删除/改名）

支持的锚点语法（写文档时的约定）：
  - repo://path/to/file.py#L10-20            行区间锚点（可带 @sha256:xxxx 期望哈希）
  - repo://path/to/file.py#L10               单行锚点
  - `path/to/file.py:10-20` 或 path.py:10-20 内联行锚点
  - `module.func` / `module.Class.method`    符号锚点（校验符号仍定义）
  - `path/to/file.py`                        文件存在锚点

用法：
    python doc_freshness.py <docs 目录> [--root <仓库根>]   # 输出新鲜度报告
    python doc_freshness.py <单个 md> --root <仓库根>

零 LLM，数据不出厂。
"""
import ast
import hashlib
import json
import os
import sys
import re
from pathlib import Path

# 锚点正则：repo://path#L10-20@sha256  /  path.py:10-20
_RE_REPO_ANCHOR = re.compile(r'repo://([^\s#)\]]+)#L(\d+)(?:-(\d+))?(?:@(?:sha256:)?([0-9a-f]{8,}))?')
_RE_LINE_ANCHOR = re.compile(r'`?([\w./\\-]+\.py):(\d+)(?:-(\d+))?`?')
# 符号锚点：反引号包住的点分符号（不含路径分隔符/后缀）
_RE_SYMBOL = re.compile(r'`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*){1,4})`')
_RE_FILE = re.compile(r'`([\w./\\-]+\.py)`')


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _resolve(root, p):
    """把锚点路径解析到绝对路径（相对仓库根）。"""
    cand = Path(root) / p
    if cand.exists():
        return cand
    return None


def _symbol_defined(file_path, sym):
    """符号 fqn（module.func / module.Class.method / module.Class）是否仍定义。"""
    try:
        src = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return False
    parts = sym.split(".")
    # parts[0] 应为模块名（文件名 stem）
    mod = Path(file_path).stem
    if parts[0] != mod:
        return False
    target = parts[1:]
    cur = tree
    for i, seg in enumerate(target):
        found = None
        for node in cur.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == seg:
                found = node
                break
        if found is None:
            return False
        if i == len(target) - 1:
            return True
        if not isinstance(found, ast.ClassDef):
            return False
        cur = found
    return False


def _file_has_symbol(file_path, sym):
    """宽松符号检查：任意名字段匹配（用于跨模块引用）。"""
    name = sym.split(".")[-1]
    try:
        src = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return True
    return False


def extract_anchors(root, text):
    """从文档文本提取锚点，返回 [dict]。"""
    anchors = []
    seen = set()
    for m in _RE_REPO_ANCHOR.finditer(text):
        a = {"type": "repo", "path": m.group(1), "line": int(m.group(2)),
             "line_end": int(m.group(3)) if m.group(3) else None,
             "expect_hash": m.group(4) or None, "pos": m.start()}
        key = (a["type"], a["path"], a["line"], a["line_end"])
        if key not in seen:
            seen.add(key)
            anchors.append(a)
    for m in _RE_LINE_ANCHOR.finditer(text):
        a = {"type": "line", "path": m.group(1).strip("`"), "line": int(m.group(2)),
             "line_end": int(m.group(3)) if m.group(3) else None, "expect_hash": None, "pos": m.start()}
        key = (a["type"], a["path"], a["line"], a["line_end"])
        if key not in seen:
            seen.add(key)
            anchors.append(a)
    for m in _RE_SYMBOL.finditer(text):
        a = {"type": "symbol", "path": None, "symbol": m.group(1), "pos": m.start()}
        key = ("sym", a["symbol"])
        if key not in seen:
            seen.add(key)
            anchors.append(a)
    return anchors


def audit_anchor(root, a):
    """校验单个锚点。返回 dict（status 等）。"""
    if a["type"] == "symbol":
        # 符号锚点：先找同名模块文件；找不到 → 可能不是代码符号（能力名/属性名），标记 skipped
        # （不算 unresolved，避免把 `impact.analyze` 这类能力标识误报为断链）。
        mod = a["symbol"].split(".")[0]
        cands = sorted(Path(root).rglob(f"{mod}.py"))
        found_file = None
        for c in cands:
            if ".venv" in str(c) or "node_modules" in str(c):
                continue
            found_file = str(c)
            break
        if found_file is None:
            return {"status": "skipped", "symbol": a["symbol"], "reason": "not_a_code_symbol"}
        if _symbol_defined(found_file, a["symbol"]):
            return {"status": "ok", "symbol": a["symbol"], "file": found_file}
        return {"status": "unresolved", "symbol": a["symbol"], "reason": "symbol_not_found",
                "file": found_file}

    f = _resolve(root, a["path"])
    if f is None:
        return {"status": "unresolved", "path": a["path"], "reason": "file_missing"}
    try:
        lines = Path(f).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return {"status": "unresolved", "path": a["path"], "reason": "read_error"}
    start = a["line"]
    end = a["line_end"] or start
    if start < 1 or start > len(lines):
        return {"status": "unresolved", "path": a["path"], "reason": f"line_out_of_range({start}/{len(lines)})"}
    slice_text = "\n".join(lines[start - 1:min(end, len(lines))])
    cur_hash = _sha(slice_text)
    if a["expect_hash"] and not cur_hash.startswith(a["expect_hash"]):
        return {"status": "stale", "path": a["path"], "line": start, "line_end": end,
                "expect_hash": a["expect_hash"], "current_hash": cur_hash}
    return {"status": "ok", "path": a["path"], "line": start, "line_end": end,
            "current_hash": cur_hash}


def audit_file(root, md_path):
    """审计单个 Markdown 文件：提取锚点并逐个校验。返回 dict。"""
    try:
        text = Path(md_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"file": str(md_path), "ok": False, "anchors": 0}
    anchors = extract_anchors(root, text)
    results = [audit_anchor(root, a) for a in anchors]
    ok = [r for r in results if r["status"] == "ok"]
    stale = [r for r in results if r["status"] == "stale"]
    unresolved = [r for r in results if r["status"] == "unresolved"]
    skipped = [r for r in results if r["status"] == "skipped"]
    return {"file": str(md_path), "ok": True, "anchors": len(results),
            "ok_count": len(ok), "stale": stale, "unresolved": unresolved,
            "skipped_count": len(skipped)}


def audit_dir(root, docs_path):
    """审计目录下所有 .md/.txt/.rst 文件。返回报告 dict。"""
    docs = Path(docs_path)
    md_files = sorted(p for p in docs.rglob("*")
                      if p.suffix.lower() in (".md", ".txt", ".rst") and ".venv" not in str(p))
    file_reports = [audit_file(root, str(f)) for f in md_files]
    file_reports = [r for r in file_reports if r.get("ok")]
    total_anchors = sum(r["anchors"] for r in file_reports)
    total_stale = sum(len(r["stale"]) for r in file_reports)
    total_unresolved = sum(len(r["unresolved"]) for r in file_reports)
    return {
        "docs_dir": str(docs_path), "root": str(root), "files": len(file_reports),
        "total_anchors": total_anchors, "stale_count": total_stale,
        "unresolved_count": total_unresolved,
        "reports": file_reports,
    }


# ═══════════════════════════════════════════════════════════════════════════
# P0-1「只报 → 建议性增量重写」：managed/手写 两区纪律 + 补丁草案生成。
# 借鉴 OpenWiki 的 AGENTS.md marker 协议：HTML 注释包起的
# `<!-- <LABEL>:START -->…<!-- <LABEL>:END -->` 区域 = 机器可重建（managed），
# 块外 = 手写区（保留、绝不覆盖）。本组函数**只读**：产出待 CodeAgent/人 review
# 的补丁草案结构，绝不直接改文档。零 LLM，数据不出厂。
# ═══════════════════════════════════════════════════════════════════════════

# managed 区 marker：开闭各为独立 HTML 注释，标签须一致（对齐 OpenWiki OPENWIKI:START/END）
_RE_MANAGED = re.compile(
    r'<!--\s*([A-Za-z0-9_][A-Za-z0-9_:.-]*):START\s*-->(.*?)<!--\s*\1:END\s*-->',
    re.S)


def managed_zones(text):
    """识别文档里的 managed（机器可重建）marker 区。

    返回 [{"label", "start", "end"}]，start/end 为该区**内容**（两块注释之间）
    的字符偏移。手写区 = 这些区间之外的部分（保留不动）。
    """
    zones = []
    for m in _RE_MANAGED.finditer(text):
        zones.append({"label": m.group(1), "start": m.start(2), "end": m.end(2)})
    return zones


def zone_of(zones, pos):
    """按字符偏移判断某个锚点落在 managed 区（返回 {zone:'managed', label}）
    还是手写区（{zone:'handwritten'}）。"""
    for z in zones:
        if z["start"] <= pos < z["end"]:
            return {"zone": "managed", "label": z["label"]}
    return {"zone": "handwritten"}


def _propose_hash_refresh(token_text, cur_hash):
    """对 `repo://…#L?-?@…` 锚点文本，提出仅刷新哈希的替换 token（其余原样保留）。"""
    if not token_text.startswith("repo://"):
        return None
    idx = token_text.rfind("@")
    if idx == -1:
        return token_text + "@sha256:" + cur_hash
    return token_text[:idx] + "@sha256:" + cur_hash


def suggest_drafts(root, md_path):
    """对单个文档产出「建议性增量重写」草案结构（只读，不落盘、不改源文档）。

    复用 audit_file 定位 stale；再按 managed/手写两区纪律决定是否建议补丁：
      - stale 落在 managed 区  → 产补丁草案（建议把锚点哈希刷到 current_hash）
      - stale 落在手写区       → guarded（保护，绝不建议覆盖）→ 只提示不进草案
      - 文档无任何 managed 区  → 全部 guarded（无处可机器重建，只报人工处理）
    返回 dict（含 report / zones / drafts / guarded / note）。
    """
    try:
        text = Path(md_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"file": str(md_path), "readable": False}
    zones = managed_zones(text)
    report = audit_file(root, md_path)
    # stale 是「锚点身份」（path#line-end@expect_hash）。同一身份可能在文档出现多次，
    # 且可能一次落在 managed 区、一次落在手写区 → 须逐次出现按各自 zone 分类。
    stale_ids = {(st["path"], st.get("line"), st.get("line_end"), st.get("expect_hash"))
                 for st in report.get("stale", [])}
    cur_by_id = {(st["path"], st.get("line"), st.get("line_end"), st.get("expect_hash")): st["current_hash"]
                 for st in report.get("stale", [])}
    drafts, guarded = [], []
    seen_ids = set()
    for m in _RE_REPO_ANCHOR.finditer(text):
        ident = (m.group(1), int(m.group(2)),
                 (int(m.group(3)) if m.group(3) else None), m.group(4))
        if ident not in stale_ids:
            continue
        seen_ids.add(ident[:3])
        cur_hash = cur_by_id[ident]
        zn = zone_of(zones, m.start())
        entry = {
            "file": str(md_path),
            "anchor": {"path": ident[0], "line": ident[1], "line_end": ident[2],
                       "expect_hash": ident[3], "current_hash": cur_hash},
            "original_anchor": m.group(0),
            "zone": zn["zone"], "label": zn.get("label"),
            "reason": "evidence_hash_changed",
        }
        if zn["zone"] == "managed":
            proposed = _propose_hash_refresh(m.group(0), cur_hash)
            if proposed and proposed != m.group(0):
                entry.update({
                    "kind": "managed_patch",
                    "proposed_anchor": proposed,
                    "original_hash": ident[3],
                    "proposed_hash": cur_hash,
                })
                drafts.append(entry)
                continue
            guarded.append(dict(entry, reason="managed_no_refresh_available"))
        else:
            # 手写区或无处可重建 → 保护，不进草案
            if not zones:
                reason = "no_managed_marker_no_auto_rewrite"
            elif zn["zone"] == "handwritten":
                reason = "protected_handwritten_zone"
            else:
                reason = "managed_no_refresh_available"
            guarded.append(dict(entry, reason=reason))
    # 兜底：万一 stale 锚点身份在文本里没被 repo 正则再次命中（极端），仍保底记录一次 guarded
    for st in report.get("stale", []):
        ident3 = (st["path"], st.get("line"), st.get("line_end"))
        if ident3 not in seen_ids:
            guarded.append({
                "file": str(md_path),
                "anchor": {"path": st["path"], "line": st.get("line"),
                           "line_end": st.get("line_end"),
                           "expect_hash": st.get("expect_hash"),
                           "current_hash": st.get("current_hash")},
                "zone": "handwritten", "label": None,
                "reason": "stale_anchor_token_not_relocated_manual_review",
            })
    return {
        "file": str(md_path), "readable": True,
        "has_managed_zone": bool(zones),
        "managed_zones": zones,
        "report": report,
        "drafts": drafts,
        "guarded": guarded,
        "draft_count": len(drafts),
        "guarded_count": len(guarded),
        "note": ("只产出补丁草案，绝不直接改文档；草案仅针对 managed 区提，"
                 "手写区（marker 块外）保护不覆盖。CodeAgent/人 review 通过后才应用。"),
    }



def main():
    import argparse
    ap = argparse.ArgumentParser(description="doc_freshness 文档新鲜度审计")
    ap.add_argument("target", help="docs 目录或单个 md")
    ap.add_argument("--root", default=".", help="仓库根（锚点路径解析基准）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    t = os.path.abspath(args.target)
    if os.path.isdir(t):
        r = audit_dir(root, t)
    else:
        r = audit_file(root, t)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0
    if isinstance(r, dict) and "stale_count" in r:
        print(f"文档新鲜度: 文件={r['files']} 锚点={r['total_anchors']} "
              f"stale={r['stale_count']} unresolved={r['unresolved_count']}")
        for fr in r["reports"]:
            if fr["stale"] or fr["unresolved"]:
                print(f"  [P0] {os.path.relpath(fr['file'], root)}: "
                      f"stale={len(fr['stale'])} unresolved={len(fr['unresolved'])}")
    else:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
