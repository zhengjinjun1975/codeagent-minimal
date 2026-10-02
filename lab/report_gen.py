#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report_gen.py — 详细软件审查报告生成（FR6，多原子聚合，纯 stdlib）。

一键聚合原子：code-review / arch-review / security-scan / deadcode / dep-scan /
doc-freshness / complexity / method-impact（子进程真实执行，每完成一个发事件，
进度条事件驱动无死角）。

报告结构：
  sections  各原子章节（含 degraded 诚实标注）
  findings  P0/P1/P2 分级 + 文件:行定位 + 建议
  structure 代码结构：分层(layers) / 依赖拓扑(nodes,edges) / 复杂度分布 / 方法级影响
导出：HTML（自包含可离线打开）+ Markdown；入库 reports 表可回看。
"""
import html
import json
import os
import re
import subprocess
import sys
import threading
import time

from events import emit

_runs = {}
_runs_lock = threading.Lock()
_seq = 0


def new_report_id():
    global _seq
    with _runs_lock:
        _seq += 1
        return f"rep-{time.strftime('%Y%m%d%H%M%S')}-{_seq}"


def start_report(target_rel, include=None):
    """异步启动报告生成。target_rel 为 target_root 内相对路径（文件或目录）。"""
    from lab_config import get_config
    cfg = get_config()
    rid = new_report_id()
    with _runs_lock:
        _runs[rid] = {"status": "running", "target": target_rel, "sections": [],
                      "findings": [], "structure": {}, "summary": "", "ok": None,
                      "started": time.time(), "done_atoms": [], "include": include or []}
    emit("report.started", {"report_id": rid, "target": target_rel})
    t = threading.Thread(target=_worker, args=(rid, target_rel, include or []), daemon=True)
    t.start()
    return {"ok": True, "report_id": rid, "status": "running"}


def get_report(rid):
    with _runs_lock:
        r = _runs.get(rid)
        return dict(r) if r else None


def _worker(rid, target_rel, include):
    from lab_config import get_config
    from history_db import get_db
    from atom_loader_ext import merged_registry
    from atom_runner import run_capability
    cfg = get_config()
    base = cfg.target_root()
    real = os.path.realpath(os.path.join(base, target_rel))
    if os.path.commonpath([real, base]) != base or not os.path.exists(real):
        with _runs_lock:
            _runs[rid].update(status="failed", ok=False, summary="目标越界或不存在")
        emit("report.failed", {"report_id": rid, "error": "目标越界或不存在"})
        return
    registry = merged_registry(cfg.codeagent_root())
    sections, findings, issues = [], [], []

    def fmt(name, env):
        data = env.get("data")
        d = data if isinstance(data, dict) else {}
        summary = d.get("summary") or d.get("verdict") or d.get("score") or ""
        ok = bool(env.get("ok"))
        sec = {"atom": name, "ok": ok, "summary": str(summary)[:300]}
        if ok:
            sec["data"] = _slim(d, 60_000)
        else:
            sec["error"] = str(env.get("error"))[:300]
        return sec, ok

    def worker_atom(name, capability, params, section_cb):
        try:
            env = run_capability(name, capability, params, timeout=240,
                                 registry=registry,
                                 codeagent_root=cfg.codeagent_root())
            sec, ok = fmt(name, env)
            section_cb(sec, env, ok)
        except Exception as e:  # noqa: BLE001
            sec = {"atom": name, "ok": False, "error": f"{type(e).__name__}: {e}"}
            section_cb(sec, env if 'env' in locals() else {}, False)

    done = {"n": 0}
    lock = threading.Lock()

    def cb(name):
        def inner(sec, env, ok):
            with lock:
                done["n"] += 1
                sections.append(sec)
                _runs[rid]["done_atoms"].append(name)
                findings.extend(_findings_from(name, sec, env))
                emit("report.atom_done", {"report_id": rid, "atom": name, "ok": ok,
                                          "summary": sec.get("summary", "")[:150]})
        return inner

    jobs = [
        ("code-review", "codereview.review", {"path": real}, "代码审查"),
        ("arch-review", "archreview.layers", {"path": real}, "架构分层"),
        ("security-scan", "security.scan", {"path": real}, "安全扫描"),
        ("dep-scan", "depscan.scan", {"path": real}, "依赖/SCA"),
        ("deadcode", None, {}, "死代码"),
    ]
    if not include or "deadcode" in include:
        pass  # 默认全量
    threads = []
    for name, cap, params, label in jobs:
        if cap:
            t = threading.Thread(target=worker_atom, args=(name, cap, params, cb(name)),
                                 daemon=True)
            t.start()
            threads.append(t)
    # deadcode / doc-freshness / complexity 走根层脚本
    for name, script, args_builder, label in [
        ("deadcode", "deadcode.py", lambda: [real], "死代码"),
        ("doc-freshness", "doc_freshness.py", lambda: [real, "--root", base], "文档新鲜度"),
        ("complexity", "complexity.py", lambda: [real, "--json"], "复杂度分布"),
    ]:
        t = threading.Thread(target=_run_root_script, args=(rid, name, script, args_builder,
                                                            cb(name), label, cfg), daemon=True)
        t.start()
        threads.append(t)
    # method-impact（影响面 = 依赖拓扑）
    t = threading.Thread(target=worker_atom,
                         args=("method-impact", "impact.analyze", {"path": real},
                               cb("method-impact")), daemon=True)
    t.start()
    threads.append(t)
    # P2-4 进度总数动态计算（不再前端硬编码）
    with _runs_lock:
        _runs[rid]["total_atoms"] = len(threads)
    for th in threads:
        th.join(timeout=300)

    # 代码结构：分层 + 依赖拓扑 + 复杂度分布
    structure = {"layers": [], "dep_graph": {"nodes": [], "edges": []},
                 "complexity": {}, "method_impact": {}}
    for sec in sections:
        d = sec.get("data") or {}
        if sec["atom"] == "arch-review" and isinstance(d.get("layers"), list):
            structure["layers"] = [{"name": str(l) if isinstance(l, str) else l}
                                   for l in d["layers"][:20]]
        if sec["atom"] == "method-impact":
            structure["dep_graph"] = _graph_from_impact(d)
        if sec["atom"] == "complexity":
            structure["complexity"] = _slim(d, 20_000)
    ok = all(s.get("ok") for s in sections) or len(sections) == 0
    findings = _dedup(findings)
    summary = (f"报告完成: {len(sections)} 个分析章节, "
               f"P0={sum(1 for f in findings if f['level']=='P0')} "
               f"P1={sum(1 for f in findings if f['level']=='P1')} "
               f"P2={sum(1 for f in findings if f['level']=='P2')}")
    # 入库 + 导出（先落盘再置 success：避免进度轮询在导出完成前读到 success 后
    # 立即请求 /api/report/{id}.html 拿到 404 的竞态）
    try:
        db = get_db()
        db.add_report(target_rel, f"软件审查报告 - {os.path.basename(target_rel)}",
                      sections, findings, structure, summary, ok)
        _export(rid, target_rel, sections, findings, structure, summary, cfg)
    except Exception as e:  # noqa: BLE001
        emit("report.failed", {"report_id": rid, "error": f"入库/导出失败: {e}"})
    with _runs_lock:
        _runs[rid].update(status="success", sections=sections, findings=findings,
                          structure=structure, summary=summary, ok=ok)
    emit("report.finished", {"report_id": rid, "ok": ok, "summary": summary})


def _run_root_script(rid, name, script, args_builder, section_cb, label, cfg):
    from events import emit
    script_path = os.path.join(cfg.codeagent_root(), script)
    if not os.path.isfile(script_path):
        section_cb({"atom": name, "ok": False, "error": f"根层脚本缺失: {script}"},
                   {"ok": False}, False)
        return
    try:
        args = args_builder()
        r = subprocess.run([sys.executable, script_path] + args,
                           capture_output=True, text=True, timeout=240,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        env = {}
        if out:
            env["data"] = json.loads(out) if out.startswith(("{", "[")) else {"raw": out}
        else:
            env = {"ok": False, "error": f"空输出(rc={r.returncode})"}
        sec, ok = _fmt_root(name, env)
        section_cb(sec, env, ok)
    except Exception as e:  # noqa: BLE001
        section_cb({"atom": name, "ok": False, "error": f"{type(e).__name__}: {e}"},
                   {"ok": False}, False)


def _fmt_root(name, env):
    d = env.get("data") if isinstance(env, dict) else None
    if isinstance(d, dict):
        summary = str(d.get("summary") or d.get("verdict") or "")[:300]
        return {"atom": name, "ok": True, "summary": summary, "data": _slim(d, 60_000)}, True
    if isinstance(d, list) and name == "deadcode":
        return {"atom": name, "ok": True, "summary": f"{len(d)} 个未使用符号",
                "data": d[:200]}, True
    return {"atom": name, "ok": False,
            "error": str((env or {}).get("error") or "未知")[:300]}, False


def _slim(d, limit):
    try:
        s = json.dumps(d, ensure_ascii=False, default=str)
        if len(s) <= limit:
            return d
        return {"truncated": True, "raw": s[:limit]}
    except Exception:  # noqa: BLE001
        return {"raw": repr(d)[:limit]}


def _findings_from(name, sec, env):
    """把各原子输出归一为 findings[{level, file, line, msg, advice, atom}]。"""
    out = []
    d = sec.get("data") or {}
    dl = d if isinstance(d, dict) else {}
    if name == "code-review":
        for it in (dl.get("issues") or dl.get("findings") or []):
            if isinstance(it, dict):
                out.append(_f(it.get("level") or it.get("severity") or "P2",
                               it.get("file") or "", it.get("line") or "",
                               it.get("msg") or it.get("issue") or it.get("title") or "",
                               it.get("advice") or it.get("suggestion") or "", name))
    elif name == "security-scan":
        for it in (dl.get("findings") or dl.get("issues") or []):
            if isinstance(it, dict):
                out.append(_f(it.get("level") or it.get("severity") or "P1",
                              it.get("file") or "", it.get("line") or "",
                              it.get("msg") or it.get("issue") or "", "", name))
    elif name == "dep-scan":
        for it in (dl.get("issues") or dl.get("findings") or []):
            if isinstance(it, dict):
                out.append(_f("P1", it.get("file") or "", "", it.get("msg") or
                              it.get("issue") or it.get("desc") or "", "", name))
    elif name == "complexity":
        for w in (dl.get("warnings") or []):
            if isinstance(w, dict):
                out.append(_f("P2", w.get("file") or "", w.get("line") or "",
                              w.get("suggestion") or "", "", name))
    elif name == "deadcode":
        items = dl if isinstance(dl, list) else (dl.get("dead") or dl.get("unused") or [])
        for it in items[:100]:
            if isinstance(it, dict):
                out.append(_f("P2", "", it.get("line") or "",
                              f"{it.get('name') or ''}: {it.get('reason') or '未使用符号'}",
                              "删除或标注用途", name))
    elif name == "doc-freshness":
        for it in (dl.get("issues") or dl.get("stale") or []):
            if isinstance(it, dict):
                out.append(_f("P2", it.get("file") or "", it.get("line") or "",
                              it.get("msg") or it.get("issue") or "", "", name))
    return out[:150]


def _f(level, file, line, msg, advice, atom):
    lv = str(level).upper()
    if lv not in ("P0", "P1", "P2"):
        lv = "P1" if lv in ("HIGH", "高", "CRITICAL") else ("P2" if lv in ("MED", "中")
                                                           else "P1")
    return {"level": lv, "file": str(file or ""), "line": str(line or ""),
            "msg": str(msg or "")[:300], "advice": str(advice or "")[:300],
            "atom": atom}


def _dedup(findings):
    seen, out = set(), []
    for f in findings:
        k = (f["level"], f["file"], f["line"], f["msg"][:80])
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def _graph_from_impact(d):
    nodes, edges = [], []
    impacted = d.get("impacted") if isinstance(d, dict) else None
    if isinstance(impacted, dict):
        for k, v in impacted.items():
            nodes.append({"id": str(k), "label": str(k)})
            for t in (v if isinstance(v, list) else [v]):
                edges.append({"from": str(k), "to": str(t)})
    return {"nodes": nodes[:80], "edges": edges[:150]}


# ── 导出（HTML 自包含 + Markdown）──────────────
def _export(rid, target_rel, sections, findings, structure, summary, cfg):
    out_dir = cfg.get("report_dir")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(target_rel) or "root"
    rid_safe = re.sub(r"[^A-Za-z0-9_-]", "_", rid)
    md_path = os.path.join(out_dir, f"{rid_safe}.md")
    html_path = os.path.join(out_dir, f"{rid_safe}.html")
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_md(target_rel, sections, findings, structure, summary))
    with open(html_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_html(target_rel, sections, findings, structure, summary))
    with _runs_lock:
        _runs[rid]["md_path"] = md_path
        _runs[rid]["html_path"] = html_path


def _md(target, sections, findings, structure, summary):
    lines = [f"# 软件审查报告", "", f"- 目标: `{target}`", f"- 生成: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             "", f"## 摘要", "", summary, "", "## 问题清单 (P0/P1/P2)", "",
             "| 级别 | 文件 | 行 | 问题 | 建议 | 来源原子 |", "|---|---|---|---|---|---|"]
    for f in findings[:200]:
        lines.append(f"| {f['level']} | {f['file']} | {f['line']} | {f['msg'][:120]} | {f['advice'][:80]} | {f['atom']} |")
    lines += ["", "## 代码结构", ""]
    if structure.get("layers"):
        lines += ["### 分层", ""] + [f"- {l.get('name')}" for l in structure["layers"]]
    dep = structure.get("dep_graph") or {}
    if dep.get("nodes"):
        lines += ["", "### 依赖拓扑", "",
                  f"节点 {len(dep['nodes'])} 个, 边 {len(dep['edges'])} 条:",
                  f"`{' > '.join(n['label'] for n in dep['nodes'][:20])}`"]
    lines += ["", "## 原子章节", ""]
    for s in sections:
        mark = "✅" if s.get("ok") else "❌"
        lines += [f"### {mark} {s['atom']}", "", str(s.get("summary") or s.get("error") or "")]
    lines += ["", "---", "本地生成，数据不出私域。"]
    return "\n".join(lines)


def _html(target, sections, findings, structure, summary):
    esc = html.escape
    rows = "".join(
        f"<tr class='lv-{esc(f['level'])}'><td>{esc(f['level'])}</td>"
        f"<td>{esc(f['file'])}</td><td>{esc(f['line'])}</td>"
        f"<td>{esc(f['msg'])}</td><td>{esc(f['advice'])}</td>"
        f"<td>{esc(f['atom'])}</td></tr>" for f in findings[:300])
    secs = ""
    for s in sections:
        mark = "✅" if s.get("ok") else "❌"
        detail = str(s.get("summary") or s.get("error") or "")
        secs += f"<div class='sec'><h3>{mark} {esc(s.get('atom',''))}</h3><p>{esc(detail)}</p></div>"
    layers = "".join(f"<li>{esc(str(l.get('name')))}</li>" for l in structure.get("layers") or [])
    dep = structure.get("dep_graph") or {}
    dep_txt = f"节点 {len(dep.get('nodes') or [])} 个 / 边 {len(dep.get('edges') or [])} 条" \
        if dep.get("nodes") else "（原子无输出）"
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>软件审查报告</title>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}}
h1{{color:#1a4f8a}} h2{{border-bottom:2px solid #1a4f8a;padding-bottom:4px;margin-top:32px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border:1px solid #bbb;padding:6px;text-align:left;vertical-align:top}}
th{{background:#eef4fb}} .lv-P0 td{{background:#fdecea}} .lv-P1 td{{background:#fff7e6}}
.lv-P2 td{{background:#f5f8fb}} .sec{{background:#f7f9fc;border:1px solid #dde3ec;border-radius:6px;padding:8px 12px;margin:6px 0}}
.meta{{color:#666}} .badge{{display:inline-block;background:#1a4f8a;color:#fff;border-radius:4px;padding:2px 8px;font-size:12px}}
</style></head><body>
<h1>软件审查报告</h1>
<p class="meta">目标: <code>{esc(target)}</code> · 生成: {time.strftime('%Y-%m-%d %H:%M:%S')} · 本地生成，数据不出私域</p>
<h2>摘要</h2><p>{esc(summary)}</p>
<h2>问题清单 (P0/P1/P2)</h2>
<table><tr><th>级别</th><th>文件</th><th>行</th><th>问题</th><th>建议</th><th>来源</th></tr>{rows}</table>
<h2>代码结构</h2>
<div class="sec"><h3>分层</h3><ul>{layers or '<li>（无分层输出）</li>'}</ul></div>
<div class="sec"><h3>依赖拓扑</h3><p>{dep_txt}</p></div>
<h2>原子章节</h2>{secs}
<hr><p class="meta">由 lab/report_gen.py 聚合 8 个指定原子生成（code-review / arch-review / security-scan / dep-scan / deadcode / doc-freshness / complexity / method-impact）</p>
</body></html>"""


def load_export(rid, kind):
    """读取已导出文件（html/md）。"""

    def _find():
        with _runs_lock:
            r = _runs.get(rid)
            return (r or {}).get(f"{kind}_path")
    p = _find()
    if not p or not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()