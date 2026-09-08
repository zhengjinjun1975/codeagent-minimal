#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""debug_history.py — 调试历史落盘 + 同形命中（FR3.2：提供过去调试结果）。

- errsig 提取：错误签名（异常名/Traceback 关键词/文件名/符号名）
- 同形相似度：错误签名重叠 + 文件名重叠 + 符号重叠 → 0~1 分
- 命中（≥阈值）→ 直接给上次修复补丁 + 回归证据（历史复用，行业 known-defects 语义）
全部数据只落本机 SQLite（debug_history 表）。
"""
import re

_THRESHOLD = 0.55

_ERR_RE = re.compile(
    r"(\w+(?:Error|Exception))|(Traceback|AssertionError|SyntaxError|NameError|"
    r"TypeError|ValueError|KeyError|IndexError|AttributeError|ImportError|"
    r"ModuleNotFoundError|ZeroDivisionError|FileNotFoundError|IndentationError|"
    r"RuntimeError|OSError|PermissionError|EOFError)", re.I)
_SYMBOL_RE = re.compile(r"\b([A-Za-z_]\w*)\b")


def extract_errsig(failure_text):
    """从失败描述/报错输出提取错误签名（去重、排序，取前 12）。"""
    if not failure_text:
        return []
    sigs = []
    for m in _ERR_RE.finditer(failure_text):
        tok = (m.group(1) or m.group(2)).strip()
        if tok and tok not in sigs:
            sigs.append(tok)
    return sigs[:12]


def extract_symbols(failure_text, file_content=None):
    """提取失败文本中出现的标识符（与目标文件符号做交集时用）。"""
    syms = set()
    for m in _SYMBOL_RE.finditer(failure_text or ""):
        w = m.group(1)
        if len(w) > 2 and w not in ("def", "class", "from", "import", "return",
                                    "The", "the", "This", "this", "Error", "File",
                                    "line", "Line"):
            syms.add(w)
    if file_content:
        for m in _SYMBOL_RE.finditer(file_content or ""):
            if m.group(1) in syms:
                syms.add(m.group(1))
    return sorted(syms)[:20]


def similarity(failure_text, errsig, file, symbols, hit):
    """候选历史 vs 当前失败 → 相似度 0~1。

    加权：错误签名重叠 0.55 + 文件名重叠 0.25 + 符号重叠 0.20。
    """
    score = 0.0
    hit_err = extract_errsig(hit.get("failure") or "")
    if errsig and hit_err:
        overlap = len(set(errsig) & set(hit_err))
        score += 0.55 * (overlap / max(1, max(len(errsig), len(hit_err))))
    if file and hit.get("file"):
        base = file.split("/")[-1]
        hit_base = str(hit["file"]).split("/")[-1]
        if base == hit_base:
            score += 0.25
        elif re.sub(r"[^A-Za-z0-9]", "", base) == re.sub(r"[^A-Za-z0-9]", "",
                                                         hit_base):
            score += 0.15
    if symbols:
        try:
            hit_syms = set(hit.get("symbols") or [])
        except TypeError:
            hit_syms = set()
        if hit_syms:
            score += 0.20 * (len(set(symbols) & hit_syms) /
                             max(1, max(len(symbols), len(hit_syms))))
    return round(min(score, 1.0), 3)


def find_hits(db, failure_text, file, symbols, limit=5):
    """检索同形历史：按相似度降序，返回 ≥阈值 的命中列表（含补丁+回归证据）。

    调用方（debug_orchestrator / verify 脚本）统一按
    hits[0]["hit"] / hits[0]["similarity"] 取用，故直接返回列表；
    错误签名由调用方自行 extract_errsig。
    """
    errsig = extract_errsig(failure_text)
    rows = db.list_debug("", limit=200)
    scored = []
    for r in rows:
        s = similarity(failure_text, errsig, file, symbols, r)
        if s >= _THRESHOLD:
            scored.append({"hit": r, "similarity": s})
    # 并列相似度时：带补丁/曾修复成功的记录优先（否则重试会命中\"上一次也失败的
    # 运行\"自身而无补丁可复用——历史补丁复用断链）。补丁字符串与成功标志同权。
    scored.sort(key=lambda x: (-x["similarity"],
                               -(1 if x["hit"].get("patch") else 0)))
    return scored[:limit]