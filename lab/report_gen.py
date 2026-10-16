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

    def worker_atom(name, capability, paths, section_cb, extra=None):
        """对一组路径跑同一能力并合并为一个章节（目录目标 = 逐文件；单文件 = 一次）。

        合并规则：issues/findings 累加（去重交给 _dedup），任一路径失败则整节标 degraded。
        """
        merged, errs, n_ok = {"issues": [], "findings": []}, [], 0
        for p in paths:
            try:
                params = {"path": p}
                params.update(extra or {})
                env = run_capability(name, capability, params, timeout=240,
                                     registry=registry,
                                     codeagent_root=cfg.codeagent_root())
                if env.get("ok"):
                    n_ok += 1
                    d = env.get("data")
                    if isinstance(d, dict):
                        for k in ("issues", "findings", "dead", "warnings"):
                            v = d.get(k)
                            if isinstance(v, list):
                                merged.setdefault(k, []).extend(v)
                else:
                    errs.append(f"{os.path.basename(p)}: {str(env.get('error'))[:80]}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{os.path.basename(p)}: {type(e).__name__}: {e}")
        if n_ok == 0:
            sec = {"atom": name, "ok": False,
                   "error": ("全部路径失败: " + "; ".join(errs[:3])) if errs else "无目标文件"}
            section_cb(sec, {"ok": False}, False)
            return
        n_issues = len(merged.get("issues") or []) + len(merged.get("findings") or [])
        env = {"ok": True, "data": merged}
        sec = {"atom": name, "ok": True,
               "summary": f"覆盖 {n_ok}/{len(paths)} 个文件（目录目标按路径序）· 合并 {n_issues} 条问题"
                          + ("；部分失败: " + "; ".join(errs[:2]) if errs else ""),
               "data": _slim(merged, 60_000)}
        section_cb(sec, env, True)

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

    files, total_py = _py_files(real)
    is_dir = os.path.isdir(real)
    jobs = [
        ("code-review", "codereview.review", files or [real], "代码审查"),
        ("arch-review", "archreview.layers", [real], "架构分层"),
        ("security-scan", "security.project" if is_dir else "security.scan", [real], "安全扫描"),
        ("dep-scan", "depscan.scan", [real], "依赖/SCA"),
        ("deadcode", None, [], "死代码"),
    ]
    if not include or "deadcode" in include:
        pass  # 默认全量
    threads = []
    for name, cap, paths, label in jobs:
        if cap:
            t = threading.Thread(target=worker_atom, args=(name, cap, paths, cb(name)),
                                 daemon=True)
            t.start()
            threads.append(t)
    # deadcode / doc-freshness / complexity 走根层脚本
    for name, script, args_builder in [
        ("deadcode", "deadcode.py", lambda: [real]),
        ("doc-freshness", "doc_freshness.py", lambda: [real, "--root", base]),
        ("complexity", "complexity.py", lambda: [real, "--json"]),
    ]:
        t = threading.Thread(target=_run_root_script, args=(name, script, args_builder,
                                                            cb(name), cfg), daemon=True)
        t.start()
        threads.append(t)
    # method-impact（影响面 = 依赖拓扑；能力名 impact.method，见该原子 manifest）
    t = threading.Thread(target=worker_atom,
                         args=("method-impact", "impact.method", [real],
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
        if sec["atom"] == "code-review" and total_py > len(files):
            note = (f"｜注意: 目标共 {total_py} 个 .py，本次按路径序仅覆盖前 {len(files)} 个，"
                    f"未覆盖部分不在本报告结论内")
            sec["summary"] = (sec.get("summary") or "") + note
        if sec["atom"] == "arch-review" and isinstance(d.get("layers"), list):
            structure["layers"] = [{"name": str(l) if isinstance(l, str) else l}
                                   for l in d["layers"][:20]]
        if sec["atom"] == "method-impact":
            structure["dep_graph"] = _graph_from_impact(d)
        if sec["atom"] == "complexity":
            structure["complexity"] = _slim(d, 20_000)
    ok = all(s.get("ok") for s in sections) or len(sections) == 0
    findings = _dedup(findings)
    gate = _gate(sections, findings)
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
        _export(rid, target_rel, sections, findings, structure, summary, cfg, gate)
    except Exception as e:  # noqa: BLE001
        emit("report.failed", {"report_id": rid, "error": f"入库/导出失败: {e}"})
    with _runs_lock:
        _runs[rid].update(status="success", sections=sections, findings=findings,
                          structure=structure, summary=summary, ok=ok, gate=gate)
    emit("report.finished", {"report_id": rid, "ok": ok, "summary": summary,
                             "verdict": gate["verdict"]})


def _run_root_script(name, script, args_builder, section_cb, cfg):
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


def _py_files(real, limit=40):
    """目录目标 → 目录内 .py 文件（路径序，确定性）；文件目标 → [该文件]。

    返回 (files, total)：total 为目录内 .py 总数，用于在报告里如实标注覆盖率
    （只审前 N 个必须写出来，不能让审查人以为全量）。
    """
    if os.path.isfile(real):
        return [real], 1
    skip = {"__pycache__", ".venv", "venv", "node_modules", ".git", "build", "dist"}
    out, total = [], 0
    for root, dirs, names in os.walk(real):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        for fn in sorted(names):
            if fn.endswith(".py"):
                total += 1
                if len(out) < limit:
                    out.append(os.path.join(root, fn))
    return out, total


def _slim(d, limit):
    try:
        s = json.dumps(d, ensure_ascii=False, default=str)
        if len(s) <= limit:
            return d
        return {"truncated": True, "raw": s[:limit]}
    except Exception:  # noqa: BLE001
        return {"raw": repr(d)[:limit]}


def _pick(it, keys, default):
    """按顺序取第一个非空值（原先是 `it.get(a) or it.get(b) or 默认`）。"""
    for k in keys:
        v = it.get(k)
        if v:
            return v
    return default


def _f_code_review(dl, name):
    for it in (dl.get("issues") or dl.get("findings") or []):
        if isinstance(it, dict):
            yield _f(_pick(it, ("level", "severity"), "P2"),
                     _pick(it, ("file",), ""), _pick(it, ("line",), ""),
                     _pick(it, ("msg", "issue", "title"), ""),
                     _pick(it, ("advice", "suggestion"), ""), name)


def _f_security_scan(dl, name):
    for it in (dl.get("findings") or dl.get("issues") or []):
        if isinstance(it, dict):
            yield _f(_pick(it, ("level", "severity"), "P1"),
                     _pick(it, ("file",), ""), _pick(it, ("line",), ""),
                     _pick(it, ("msg", "issue"), ""), "", name)


def _f_dep_scan(dl, name):
    for it in (dl.get("issues") or dl.get("findings") or []):
        if isinstance(it, dict):
            yield _f("P1", _pick(it, ("file",), ""), "",
                     _pick(it, ("msg", "issue", "desc"), ""), "", name)


def _f_complexity(dl, name):
    for w in (dl.get("warnings") or []):
        if isinstance(w, dict):
            yield _f("P2", _pick(w, ("file",), ""), _pick(w, ("line",), ""),
                     _pick(w, ("suggestion",), ""), "", name)


def _f_deadcode(dl, name):
    # 注：调用方已把 data 归一成 dict，原先那条 isinstance(dl, list) 分支是不可达的
    for it in (dl.get("dead") or dl.get("unused") or [])[:100]:
        if isinstance(it, dict):
            yield _f("P2", "", _pick(it, ("line",), ""),
                     f"{it.get('name') or ''}: {it.get('reason') or '未使用符号'}",
                     "删除或标注用途", name)


def _f_doc_freshness(dl, name):
    for it in (dl.get("issues") or dl.get("stale") or []):
        if isinstance(it, dict):
            yield _f("P2", _pick(it, ("file",), ""), _pick(it, ("line",), ""),
                     _pick(it, ("msg", "issue"), ""), "", name)


# 原子名 → 提取器（原先是 6 支 if/elif 链：圈复杂度 62，几乎全由 `or` 兜底链贡献）
_FINDING_EXTRACTORS = {
    "code-review": _f_code_review,
    "security-scan": _f_security_scan,
    "dep-scan": _f_dep_scan,
    "complexity": _f_complexity,
    "deadcode": _f_deadcode,
    "doc-freshness": _f_doc_freshness,
}


def _findings_from(name, sec, env):
    """把各原子输出归一为 findings[{level, file, line, msg, advice, atom}]。"""
    d = sec.get("data") or {}
    dl = d if isinstance(d, dict) else {}
    fn = _FINDING_EXTRACTORS.get(name)
    return list(fn(dl, name))[:150] if fn else []


_SEV_P0 = {"critical", "blocker", "fatal", "严重", "阻断"}
_SEV_P1 = {"major", "high", "error", "高"}


def _f(level, file, line, msg, advice, atom):
    """严重度归一（照各原子实际词表: critical/major/minor → P0/P1/P2）。

    P0=阻断（critical 级缺陷/漏洞，必须修完才能放行）；P1=高（major）；其余（minor/中/低/未标注）= P2。
    未知词表按 P2 记账——宁可不高估风险，也不把噪声记成阻断项。
    """
    lv = str(level).strip().lower()
    if lv in _SEV_P0:
        out = "P0"
    elif lv in _SEV_P1:
        out = "P1"
    elif lv.upper() in ("P0", "P1", "P2"):
        out = lv.upper()
    else:
        out = "P2"
    return {"level": out, "file": str(file or ""), "line": str(line or ""),
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


# ── 质量门（照 SonarQube「Quality Gate」范式：阈值 + 放行判定置顶）──
# 判定只用**已有的实测结果**，不引入新指标；证据不全时明确判「无法判定」，绝不默认放行。
# 只用**硬门槛**（P0=0、证据完整），不给 P1/P2 设阈值——对存量代码设任意条数阈值等于制造噪声判决；
# P1 数量作为「放行前需处理」的说明给出，不参与拦截。
_GATE_RULES = (
    ("阻断缺陷 P0 = 0（critical 级缺陷/漏洞）", "P0", 0, "P0"),
)


def _gate(sections, findings):
    """返回放行判定：verdict ∈ 放行 / 有条件放行 / 不放行 / 无法判定。

    conditions: 指标 / 实测 / 阈值 / 结论（给审查人看的一页纸）。
    """
    counts = {lv: sum(1 for f in findings if f.get("level") == lv) for lv in ("P0", "P1", "P2")}
    failed_atoms = [s.get("atom") for s in sections if not s.get("ok")]
    conditions = []
    blocking = []
    for label, lv, limit, key in _GATE_RULES:
        actual = counts.get(lv, 0)
        ok = actual <= limit
        conditions.append({"name": label, "actual": actual, "threshold": limit, "pass": ok,
                           "level": key})
        if not ok:
            blocking.append(label)
    conditions.append({"name": "高风险 P1（仅计数，不拦截）", "actual": counts.get("P1", 0),
                       "threshold": "—", "pass": counts.get("P1", 0) == 0, "level": "P1"})
    conditions.append({"name": "证据完整性（全部原子执行成功）", "actual": len(failed_atoms),
                       "threshold": 0, "pass": not failed_atoms, "level": "证据"})
    if failed_atoms:
        verdict = "无法判定"
        reasons = ["以下原子未成功，结论不完整: " + ", ".join(str(a) for a in failed_atoms)]
    elif blocking:
        verdict = "不放行"
        reasons = ["存在阻断项: " + "; ".join(blocking)
                   + f"（共 {counts.get('P0', 0)} 条，修完并复跑本报告后重新判定）"]
    elif counts.get("P1", 0) > 0:
        verdict = "有条件放行"
        reasons = [f"无阻断项，但有 {counts['P1']} 条 P1 需在放行前处理"]
    else:
        verdict = "放行"
        reasons = ["全部条件达标"]
    return {"verdict": verdict, "reasons": reasons, "conditions": conditions,
            "counts": counts, "failed_atoms": failed_atoms}


# ── SARIF 2.1.0 导出（OASIS 标准；GitHub Code Scanning / VS Code 可直接消费）──
_SARIF_LEVEL = {"P0": "error", "P1": "warning", "P2": "note"}


def _sarif(target, findings, sections):
    import hashlib
    try:
        from version import __version__ as _v
    except Exception:  # fail-open: 版本号取不到不影响报告生成
        _v = "0"
    rules, seen = [], set()
    for f in findings:
        rid_ = str(f.get("atom") or "codeagent")
        if rid_ in seen:
            continue
        seen.add(rid_)
        rules.append({
            "id": rid_,
            "name": rid_,
            "shortDescription": {"text": f"{rid_} 检出的问题"},
        })
    results = []
    for f in findings[:500]:
        fp = hashlib.sha256(("%s|%s|%s" % (f.get("file"), f.get("line"), f.get("msg")))
                            .encode("utf-8")).hexdigest()[:32]
        results.append({
            "ruleId": str(f.get("atom") or "codeagent"),
            "level": _SARIF_LEVEL.get(str(f.get("level")), "warning"),
            "message": {"text": "%s（%s）%s" % (f.get("msg", ""), f.get("level", ""),
                                                ("建议: " + str(f.get("advice"))) if f.get("advice") else "")},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": str(f.get("file") or target).replace("\\", "/")},
                    "region": {"startLine": int(f.get("line") or 1)},
                }
            }],
            "partialFingerprints": {"primaryLocationLineHash": fp},
            "properties": {"severity": f.get("level"), "source": f.get("atom")},
        })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "CodeAgent", "version": str(_v),
                                "informationUri": "https://github.com/zhengjinjun1975/codeagent-minimal",
                                "rules": rules}},
            "originalUriBaseIds": {"SRCROOT": {"uri": "file:///"}},
            "results": results,
            "properties": {"target": target,
                           "atoms": [s.get("atom") for s in sections]},
        }],
    }


# ── 导出（HTML 自包含 + Markdown + SARIF）──────────────
def _export(rid, target_rel, sections, findings, structure, summary, cfg, gate=None):
    out_dir = cfg.get("report_dir")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(target_rel) or "root"
    rid_safe = re.sub(r"[^A-Za-z0-9_-]", "_", rid)
    md_path = os.path.join(out_dir, f"{rid_safe}.md")
    html_path = os.path.join(out_dir, f"{rid_safe}.html")
    sarif_path = os.path.join(out_dir, f"{rid_safe}.sarif")
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_md(target_rel, sections, findings, structure, summary, gate))
    with open(html_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_html(target_rel, sections, findings, structure, summary, gate))
    with open(sarif_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(_sarif(target_rel, findings, sections), f, ensure_ascii=False, indent=2)
    with _runs_lock:
        _runs[rid]["md_path"] = md_path
        _runs[rid]["html_path"] = html_path
        _runs[rid]["sarif_path"] = sarif_path


def _cond_label(c):
    """门条件的结论列：仅计数项不判达标/未达标（避免"未达标"被误读成拦截项）。"""
    if str(c.get("threshold")) == "—":
        return "计数（不拦截）"
    return "达标" if c.get("pass") else "未达标"


def _gate_md(gate):
    if not gate:
        return []
    lines = ["## 放行判定", "",
             f"**{gate['verdict']}**（" + "; ".join(gate["reasons"]) + "）", "",
             "| 条件 | 实测 | 阈值 | 结论 |", "|---|---|---|---|"]
    for c in gate["conditions"]:
        lines.append("| %s | %s | %s | %s |" % (c["name"], c["actual"], c["threshold"],
                                                _cond_label(c)))
    lines += ["", f"问题计数: P0={gate['counts'].get('P0', 0)} P1={gate['counts'].get('P1', 0)} "
                  f"P2={gate['counts'].get('P2', 0)}", ""]
    return lines


def _md(target, sections, findings, structure, summary, gate=None):
    lines = [f"# 软件审查报告", "", f"- 目标: `{target}`", f"- 生成: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             "", f"## 摘要", ""]
    lines += _gate_md(gate)
    lines += [summary, "", "## 问题清单 (P0/P1/P2)", "",
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


_GATE_COLOR = {"放行": "#1e7b34", "有条件放行": "#b06a00", "不放行": "#b3261e", "无法判定": "#5f6368"}


def _gate_html(gate):
    esc = html.escape
    if not gate:
        return ""
    color = _GATE_COLOR.get(gate.get("verdict"), "#5f6368")
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (esc(str(c["name"])), esc(str(c["actual"])), esc(str(c["threshold"])),
           esc(_cond_label(c)))
        for c in gate["conditions"])
    return (f"<div class='gate' style='border-color:{color}'>"
            f"<div class='verdict' style='color:{color}'>{esc(gate['verdict'])}</div>"
            f"<div class='reasons'>{esc('；'.join(gate['reasons']))}</div>"
            f"<table class='cond'><tr><th>条件</th><th>实测</th><th>阈值</th><th>结论</th></tr>"
            f"{rows}</table></div>")


def _html(target, sections, findings, structure, summary, gate=None):
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
.gate{{border:2px solid #5f6368;border-radius:8px;padding:12px 16px;margin:16px 0;background:#fbfcfd}}
.gate .verdict{{font-size:26px;font-weight:700;letter-spacing:2px}}
.gate .reasons{{color:#444;margin:4px 0 10px}}
.gate table.cond{{font-size:13px}}
</style></head><body>
<h1>软件审查报告</h1>
<p class="meta">目标: <code>{esc(target)}</code> · 生成: {time.strftime('%Y-%m-%d %H:%M:%S')} · 本地生成，数据不出私域</p>
{_gate_html(gate)}
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
    """读取已导出文件（kind ∈ html/md/sarif）。"""

    def _find():
        with _runs_lock:
            r = _runs.get(rid)
            return (r or {}).get(f"{kind}_path")
    p = _find()
    if not p or not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()