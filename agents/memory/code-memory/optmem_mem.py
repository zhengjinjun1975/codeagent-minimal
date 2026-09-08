#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""optmem_mem.py — code-memory 原子接入「可选外部共享记忆库」的零依赖包装桥。

目标：把代码经验从"本地独享 experience/*.json"打通到外部共享记忆命令行工具
（memo / memo_search，可指向任意兼容实现；未配置则默认关闭并本地降级），实现
一边记、另一边(其他进程/主机)能语义检索到。

设计原则(全部满足)：
  - 纯 stdlib subprocess 包装 memo / memo_search，不加任何第三方依赖。
  - MEMORY_DIR 必须注入 env，否则工具会写默认目录。
  - 条目带 [code] 域前缀 + [conf:X.X] 置信度标记(0.3试探/0.5普通/0.7较可信/0.9确定)。
  - 单条 ≤280 utf-8 字节(字节级截断，不截半中文)。
  - 域隔离：召回用 memo_search --domain code，只命中 [code] 域。
  - 静默降级：记忆库不可达(未配置/异常/超时/无 Ollama)一律返回 (False, msg)，
    由上层回落本地 experience/*.json，绝不因记忆层失败中断业务主流程。

用法：
  被 main.py(CodeMemoryAgent) import；本文件亦可独立 `python optmem_mem.py` 自测。
环境开关(可选覆盖，默认未配置→整桥禁用并静默降级)：
  OPTMEM_MEMO_DIR    外部记忆工具目录(默认空=未启用)
  OPTMEM_MEMORY_DIR  记忆库目录(默认 <OPTMEM_MEMO_DIR>/memory)
  OPTMEM_OFF=1        彻底关闭，只走本地降级"""


import os
import re
import sys
import subprocess

# ═══════════ 路径与常量(可被环境覆盖) ═══════════
# 默认不指向任何本机路径：未配置 OPTMEM_MEMO_DIR → 整桥禁用并静默降级。
OPTMEM_DIR = (os.environ.get("OPTMEM_MEMO_DIR", "") or "").strip()
MEMORY_DIR = os.environ.get(
    "OPTMEM_MEMORY_DIR", ""
) or (os.path.join(OPTMEM_DIR, "memory") if OPTMEM_DIR else "")
MEMO = os.path.join(OPTMEM_DIR, "memo") if OPTMEM_DIR else ""              # 外部记忆工具(无扩展名可执行)
MEMO_SEARCH = os.path.join(OPTMEM_DIR, "memo_search.py") if OPTMEM_DIR else ""  # hybrid 语义检索

DOMAIN = "code"        # 共享记忆库里的域前缀([code])，召回按此隔离
MAX_BYTES = 280        # 外部记忆库单条上限(字节)

# 注入 MEMORY_DIR，确保写/读到共享库而非工具默认目录
_ENV = dict(os.environ)
_ENV["MEMORY_DIR"] = MEMORY_DIR


def _clip(text, max_bytes=MAX_BYTES):
    """按 utf-8 字节截断到 ≤max_bytes(不清真中文字符)。"""
    s = text or ""
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore")


def _run(argv, timeout=60):
    """跑一个外部记忆命令(memo 或 memo_search)。任何异常 → (False, 原因)，静默降级。"""
    try:
        p = subprocess.run(
            [sys.executable] + list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_ENV,
            timeout=timeout,
        )
        if p.returncode == 0:
            return True, ((p.stdout or "") or "").strip()
        return False, (p.stderr or p.stdout or f"exit {p.returncode}").strip()
    except Exception as e:  # 不可达/超时等 → 上层回落本地
        return False, f"{type(e).__name__}: {e}"


def _conf_label(severity=None, conf=None):
    """拼 [code][conf:X.X] 前缀。显式 conf 优先；否则按 severity 映射置信度。"""
    if conf is not None:
        c = max(0.0, min(1.0, float(conf)))
    else:
        c = {"major": 0.7, "minor": 0.5, "info": 0.3}.get(
            (severity or "").lower(), 0.5  # 普通经验默认 0.5
        )
    return f"[{DOMAIN}][conf:{c:.1f}]"


def available():
    """探活：记忆库目录真实存在且未显式关闭。写前用，可被 OPTMEM_OFF=1 关闭。"""
    if os.environ.get("OPTMEM_OFF") == "1":
        return False
    if not OPTMEM_DIR:
        return False
    return os.path.isdir(MEMORY_DIR)


def save(text, severity=None, conf=None, timeout=60):
    """主写共享记忆库(memo note)。条目 = [code][conf:X.X] + 内容，≤280 字节。

    返回 (True, note_id形如 #N 或 "?") 成功 / (False, msg) 记忆库不可达。
    """
    if not (text or "").strip():
        return False, "empty text"
    if not OPTMEM_DIR:
        return False, "降级跳过(未配置 OPTMEM_MEMO_DIR)"
    line = _clip(_conf_label(severity, conf) + " " + text)
    ok, out = _run([MEMO, "note", line], timeout=timeout)
    if not ok:
        return False, out
    m = re.search(r"Saved as #(\d+)", out)
    return True, ("#" + m.group(1)) if m else "?"


def recall(query, top_k=3, timeout=90):
    """从共享记忆库召回。主: memo_search hybrid(域隔离 [code])；失败降级 memo recall 词法。

    返回 (True, hits:[{id,score,text}]) / (False, msg) 记忆库整体不可达。
    """
    q = (query or "").strip()
    if not q:
        return False, "empty query"
    if not OPTMEM_DIR:
        return False, "降级跳过(未配置 OPTMEM_MEMO_DIR)"
    ok, out = _run([MEMO_SEARCH, q, "--domain", DOMAIN, "--top", str(top_k)],
                   timeout=timeout)
    if ok:
        hits = _parse_search(out)
        return True, hits if hits else []
    # hybrid 失败(如 Ollama nomic-embed-text 停/超时) → 词法降级 memo recall(regex)
    return _recall_keyword(q, top_k, timeout)


def _parse_search(out):
    """解析 memo_search 输出行: `#id  date  score=N.N  [code]  text[:120]`。"""
    hits = []
    for ln in (out or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = re.match(r"#(\d+)\s+\S+\s+score=([\d.]+)\s+(?:\[[a-z]+\]\s*)?(.*)", ln)
        if m:
            hits.append({
                "id": int(m.group(1)),
                "score": float(m.group(2)),
                "text": (m.group(3) or "").strip(),
            })
    return hits


def _recall_keyword(query, top_k=3, timeout=60):
    """词法降级：memo recall <regex>，输出行 `#id date text`(全文，无需 Ollama)。"""
    ok, out = _run([MEMO, "recall", query], timeout=timeout)
    if not ok:
        return False, out
    hits = []
    for ln in (out or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = re.match(r"#(\d+)\s+\S+\s+(.*)", ln)
        if m:
            hits.append({"id": int(m.group(1)), "score": 0.0,
                         "text": (m.group(2) or "").strip()})
    return (True, hits[:top_k]) if hits else (True, [])


def _fmt_prompt(hits):
    """把命中经验拼成注入 prompt 的历史经验块。"""
    if not hits:
        return ""
    lines = []
    for h in hits:
        txt = h.get("text", "").strip()
        if not txt:
            continue
        # 保留 conf 信息性标记供按置信度参考；域前缀 [code] 冗余可留
        lines.append(f"- [共享经验 #{h.get('id')}] {txt[:MAX_BYTES]}")
    if not lines:
        return ""
    return "\n\n【相关历史经验(共享代码经验记忆库 · 越用越准)】\n" + "\n".join(lines[:10])


# ═══════════ 独立自测入口 ═══════════
if __name__ == "__main__":
    import json
    print(json.dumps({
        "memo": MEMO, "memory_dir": MEMORY_DIR,
        "available": available(),
    }, ensure_ascii=False, indent=2))
