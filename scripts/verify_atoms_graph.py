#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_atoms_graph.py — 原子图谱门（第五道 CI 门）

拦的是**编排错误**，不是风格问题：调了不存在的能力、传了接不住的参数、
组合里点了一个不存在的步骤、声明依赖和真实调用对不上、依赖成环、
组合配了却没人走得到、签名悄悄改了、盲区在无声长大。

一期的门只回答「有没有违规」（一条规则清单 → 红/绿）。二期改成回答**四个独立问题**
（见 docs/ATOMS_GRAPH_V2_PLAN.md §2）：
    Q1 契约  名字、参数、占位符、挂载对得上吗
    Q2 通路  声明过的组合，从哪个入口进得来
    Q3 波及  这次改动伤到谁（签名破坏 / 代码里写死第二套组合）
    Q4 自知  有多少调用点是门看不见的
四个问题的失败模式互相独立，一个红绿灯表示不了。

为什么不判全量：存量必然红（166 条能力里只有一小部分有调用点），
全量扫会让门变成噪声、被人无视 —— 和宪法门同一个道理。
所以默认只判「本次改动波及的原子/文件」，存量走 `config/atoms_graph_baseline.json`。
**但改公共层（atomic_base / agent_runtime / agent_loader / registry）就全仓硬红**：
那等于改了所有原子的契约，只判改动文件等于放行。

取不到基线 ref 时**明确打印"判不了"并 exit 0**：不假装通过，也不误报红。

用法：
    python scripts/verify_atoms_graph.py                 # 增量（默认）
    python scripts/verify_atoms_graph.py --all           # 全仓判（发版前跑）
    python scripts/verify_atoms_graph.py --json          # 机读，正文只有 JSON
    python scripts/verify_atoms_graph.py --emit          # 重新生成 docs 里的图与表
    python scripts/verify_atoms_graph.py --check-drift    # 生成物与代码是否同步
    python scripts/verify_atoms_graph.py --shrink        # 台账自动收缩（本地写回）
    python scripts/verify_atoms_graph.py --shrink --dry-run   # 只报可回收条目（CI 用）
    python scripts/verify_atoms_graph.py --impact test.run    # 改之前看波及面（工具，不拦）
    python scripts/verify_atoms_graph.py --update-baseline --expires 2027-01-31 --reason "存量"
    python scripts/verify_atoms_graph.py --update-signatures --reason "改了 test.run 签名"
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import atoms_graph_core as core  # noqa: E402

DOC_MD = os.path.join(ROOT, "docs", "ATOMS_GRAPH.md")
DOC_HTML = os.path.join(ROOT, "docs", "atoms_graph_compose.html")

LEVEL_MARK = {"green": "绿", "yellow": "黄", "red": "红"}


def _emit(graph, md_path=DOC_MD, html_path=DOC_HTML):
    md = core.render_markdown(graph)
    html = core.render_html(graph)
    for path, text in ((md_path, md), (html_path, html)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return md_path, html_path


def _drift(graph):
    """生成物是否与代码同步。不同步就红 —— 手改的图比没有图更误导人。"""
    bad = []
    for path, text in ((DOC_MD, core.render_markdown(graph)),
                       (DOC_HTML, core.render_html(graph))):
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        if not os.path.isfile(path):
            bad.append("%s 不存在（先 --emit）" % rel)
            continue
        with open(path, encoding="utf-8") as f:
            cur = f.read()
        if cur != text:
            bad.append("%s 与代码不同步（改了代码没重新生成，或手改过）" % rel)
    return bad


def _chain_step_order(ch, cap):
    return next(st["order"] for st in ch["steps"] if st.get("capability") == cap)


def _impact_capability(graph, t):
    info = graph["capability_index"][t]
    node = next((n for n in graph["nodes"] if n["id"] == info["atom"]), {}) or {}
    chs = [c for c in graph["chains"] if any(st.get("capability") == t for st in c["steps"])]
    return {"kind": "capability", "target": t, "atom": info["atom"],
            "domain": node.get("domain"), "sites": info["sites"],
            "chains": [{"name": c["name"], "reach": c.get("reach"),
                        "order": _chain_step_order(c, t)} for c in chs],
            "entries": sorted({e for c in chs for e in (c.get("reach_by") or [])}),
            "signature": (graph["signatures"]["capabilities"].get(t) or {})}


def _impact_file(graph, t):
    files = {c["file"] for c in graph["call_sites"]} | {n["path"] for n in graph["nodes"]}
    hit = sorted(f for f in files if f and (f == t or f.endswith("/" + t) or t in f))
    picked = set(hit)
    sites = [c for c in graph["call_sites"] if c["file"] in picked]
    return {"kind": "file", "target": t, "files": hit,
            "atoms": sorted({n["id"] for n in graph["nodes"] if n["path"] in picked}),
            "sites": [c["site_id"] for c in sites],
            "evidence": {lv: sum(1 for c in sites if c["evidence"] == lv)
                         for lv in core.EVIDENCE_LEVELS
                         if any(c["evidence"] == lv for c in sites)},
            "chains": [{"name": c["name"], "reach": c.get("reach")}
                       for c in graph["chains"] if c["file"] in picked]}


def _impact(graph, target):
    """改动前的影响面预演：改这条能力/这个文件，会波及谁。

    定位是**给人看的工具**，不拦 CI。目的是让"改之前先看"成为习惯，
    而不是改完被门拦下来再回头查。
    """
    t = str(target or "")
    if t in graph["capability_index"]:
        return _impact_capability(graph, t)
    return _impact_file(graph, t)


def _print_impact_file(prob):
    print("  命中文件：%s" % ("、".join(prob["files"]) or "—（没有文件匹配）"))
    print("  涉及原子：%s" % ("、".join(prob["atoms"]) or "—"))
    print("  调用点 %d 处：%s" % (len(prob["sites"]), "、".join(prob["sites"]) or "—"))
    print("  证据等级：%s" % (prob["evidence"] or "—"))
    print("  涉及组合：%s" % ("、".join("%s(%s)" % (c["name"], c["reach"])
                                      for c in prob["chains"]) or "—"))


def _print_impact_capability(prob):
    sig = prob["signature"] or {}
    print("  所在原子：%s（domain=%s）" % (prob["atom"], prob["domain"]))
    print("  当前签名：%s" % (sig.get("params") or "—"))
    print("  调用点 %d 处：%s" % (len(prob["sites"]), "、".join(prob["sites"]) or "—"))
    for c in prob["chains"]:
        print("  被组合用到：%s 第%d步（%s）" % (c["name"], c["order"], c["reach"]))
    print("  受影响入口：%s" % ("、".join(prob["entries"]) or "—（没有入口走到它）"))


def _print_impact(prob):
    print("影响面 %s（%s）" % (prob["target"], prob["kind"]))
    if prob["kind"] == "capability":
        _print_impact_capability(prob)
    else:
        _print_impact_file(prob)


def _print_questions(qs, m):
    print("四问：")
    for q in ("Q1", "Q2", "Q3", "Q4"):
        d = qs.get(q) or {}
        extra = []
        if d.get("blocking"):
            extra.append("%d 条须修" % d["blocking"])
        if d.get("tracking"):
            extra.append("%d 条待跟踪" % d["tracking"])
        if d.get("waived"):
            extra.append("台账 %d 条" % d["waived"])
        note = ("；" + d["note"]) if d.get("note") else ""
        print("  %s %-4s %s   %s%s" % (q, d.get("name", ""),
                                       LEVEL_MARK.get(d.get("level"), "?"),
                                       "，".join(extra) or "无未登记问题", note))


# ────────────────────── 各子命令的处理函数 ──────────────────────
def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def _do_emit(graph):
    md, html = _emit(graph)
    print("已生成：%s" % os.path.relpath(md, ROOT))
    print("已生成：%s" % os.path.relpath(html, ROOT))
    for c in graph["chains"]:
        if not c.get("dynamic"):
            print("  组合 %-16s %d 步  %-14s %s"
                  % (c["name"], len(c["steps"]), c.get("reach"),
                     "、".join(c.get("reach_by") or [])))
    return 0


def _do_drift(graph):
    bad = _drift(graph)
    if not bad:
        print("[原子图谱门] 生成物与代码同步（docs/ATOMS_GRAPH.md、docs/atoms_graph_compose.html）")
        return 0
    print("[原子图谱门] 生成物与代码不同步：")
    for b in bad:
        print("  - " + b)
    print("结论：不通过。跑 `python scripts/verify_atoms_graph.py --emit` 重新生成后一起提交。")
    return 1


def _do_update_baseline(a, graph):
    if not (a.expires and a.reason):
        print("--update-baseline 需要同时给 --expires 与 --reason（台账不许无期限、不许无理由）",
              file=sys.stderr)
        return 2
    data = core.dump_baseline(graph, a.reason, a.expires)
    path = core.baseline_path(ROOT)
    _write_json(path, data)
    print("基线台账已重写：%s（%d 条，到期 %s）"
          % (os.path.relpath(path, ROOT), len(data["entries"]), a.expires))
    return 0


SIG_NOTE = ("本文件由 `scripts/verify_atoms_graph.py --update-signatures` 生成，"
            "禁手写。它是签名基线：删参数/加必填/改名会触发 G17，"
            "改完必须重新生成并写明理由。")


def _do_update_signatures(a, graph):
    if not a.reason:
        print("--update-signatures 需要 --reason：改签名要留下认领说明（谁改的、为什么、"
              "受影响调用点怎么处理）", file=sys.stderr)
        return 2
    data = dict(graph["signatures"])
    data["_note"] = SIG_NOTE
    data["reason"] = a.reason
    path = core.sig_path(ROOT)
    _write_json(path, data)
    print("签名快照已重写：%s（%d 条能力）—— 理由：%s"
          % (os.path.relpath(path, ROOT), len(data["capabilities"]), a.reason))
    return 0


def _do_shrink(a, graph, bl):
    kept, removed = core.shrink_baseline(bl, graph["findings"])
    if not removed:
        print("[台账收缩] 没有可回收的条目（%d 条都还有效）" % len(bl.get("entries") or []))
        return 0
    print("[台账收缩] 可回收 %d 条（已不再触发）：" % len(removed))
    for e in removed[:40]:
        print("  - %s  %s" % (e.get("key"), e.get("subject")))
    if len(removed) > 40:
        print("  - ……另 %d 条" % (len(removed) - 40))
    if a.dry_run:
        print("（--dry-run：没写盘。本地跑一次 `--shrink` 收紧台账）")
        return 0
    bl["entries"] = kept
    path = core.baseline_path(ROOT)
    _write_json(path, bl)
    print("已写回 %s（%d → %d 条）"
          % (os.path.relpath(path, ROOT), len(kept) + len(removed), len(kept)))
    return 0


# ────────────────────── 判门 ──────────────────────
def _in_scope(a, res):
    """本次要判哪些问题。取不到基线 ref 时返回 None（调用方负责说"判不了"）。"""
    if a.all:
        inside, outside, whole, why = core.scope(res["open"], None, {})
        return {"inside": inside, "outside": outside, "whole": whole,
                "why": why, "changed": None}
    base, _how = core.pick_base(ROOT, a.base)
    if not base:
        return None
    changed = core.changed_files(ROOT, base)
    atoms = core.load_registry(ROOT)["agents"]
    inside, outside, whole, why = core.scope(res["open"], changed, atoms)
    return {"inside": inside, "outside": outside, "whole": whole,
            "why": why, "changed": changed}


def _questions_of(res, inside, m):
    """四问按**本次范围内的未登记问题**重算档位（全仓图上的档位只用于文档展示）。"""
    waived_by_q = {}
    for f in res["waived"]:
        q = core.QUESTION_OF.get(f["rule"])
        if q:
            waived_by_q[q] = waived_by_q.get(q, 0) + 1
    base_problems = [p for p in res["problems"] if p["rule"].startswith("BASELINE_")]
    return core.questions(inside + base_problems, m, waived=waived_by_q)


def _json_out(a, graph, qs, res, sc):
    """机读输出。fail 由 _do_gate 预先算好放进 sc（判定口径只在一个地方，避免两份逻辑分叉）。"""
    changed = sc["changed"]
    out = {"metrics": graph["metrics"], "questions": qs,
           "mode": "all" if a.all else ("增量：" + sc["why"]),
           "changed_files": len(changed) if changed is not None else None,
           "whole_repo": sc["whole"],
           "open": sc["inside"], "out_of_scope": len(sc["outside"]),
           "waived": len(res["waived"]), "baseline_problems": res["problems"],
           "verdict": "fail" if sc["fail"] else "pass"}
    for q in ("Q1", "Q2", "Q3", "Q4"):
        out[q] = qs.get(q)
    return out


def _print_findings(inside, problems, nwaived, noutside):
    if inside:
        print("\n本次范围内的问题（%d 条，未在台账登记）：" % len(inside))
        for f in inside:
            loc = ("  @%s" % f["file"]) if f.get("file") else ""
            print("  [%s] %-26s %s%s\n        %s"
                  % (f["severity"], f["rule"], f["subject"], loc, f["detail"]))
    if problems:
        print("\n台账问题（%d 条）：" % len(problems))
        for p in problems:
            print("  [%s] %-18s %s — %s" % (p["severity"], p["rule"], p["subject"], p["detail"]))
    if nwaived:
        print("\n台账已登记（不挡门）：%d 条" % nwaived)
    if noutside:
        print("本次范围外（存量，未计入）：%d 条" % noutside)


def _print_gate_report(a, m, qs, res, sc):
    err = sys.stderr
    err.write("[原子图谱门] %s\n" % ("全仓判" if a.all else ("增量判｜" + sc["why"])))
    err.write("             原子 %d / 能力 %d / 调用点 %d（看得见 %d / 看不见 %d）/ 组合 %d 条 %d 步\n"
              % (m["atoms"], m["capabilities"], m["call_sites"], m["call_sites_static"],
                 m["call_sites"] - m["call_sites_static"], m["chains"], m["chain_steps"]))
    if sc["whole"] and not a.all:
        err.write("             ⚠ 改了公共层 → 全仓进入硬红区间（只判改动文件等于放行全部契约）\n")
    _print_questions(qs, m)
    print("\n规则命中（全仓）：")
    for rule in core.RULES:
        n = m["rules_fired"].get(rule, 0)
        if n:
            print("  %-26s %s  %d" % (rule, core.RULES[rule][0], n))
    _print_findings(sc["inside"], res["problems"], len(res["waived"]), len(sc["outside"]))


def _print_conclusion(fail, a, res, sc):
    if fail:
        print("\n结论：不通过 ⇒ 修代码，或在台账里登记（必须写 reason 与 expires；P0/P1 不许进台账）。")
        return 1
    if not a.all and not sc["whole"] and not sc["changed"]:
        print("\n结论：通过 —— 本次没有任何文件改动，没有可判的东西（不是「全仓都干净」）。")
        return 0
    print("\n结论：通过（本次范围内 0 条未登记问题；台账已登记 %d 条，本次范围外存量 %d 条）"
          % (len(res["waived"]), len(sc["outside"])))
    return 0


def _do_gate(a, graph, m, bl):
    res = core.apply_baseline(graph["findings"], bl)
    sc = _in_scope(a, res)
    if sc is None:
        print("[原子图谱门] 取不到基线 ref（浅克隆/无历史），本次不判 —— 这不是通过，是判不了。")
        print("             要在 CI 里生效需 checkout 至少两个提交（fetch-depth: 2 或 PR 的 base）。")
        return 0
    inside = sc["inside"]
    # 基线问题（过期/无理由）与范围无关，一律算不通过；失效条目只在全仓判时追究，
    # 免得无关 PR 被台账噪声挡住。
    blocking = list(inside) + [p for p in res["problems"]
                               if p["rule"] in ("BASELINE_EXPIRED", "BASELINE_NO_REASON")]
    if a.all:
        blocking += [p for p in res["problems"] if p["rule"] == "BASELINE_STALE"]
    qs = _questions_of(res, inside, m)
    hard = whole_has_blocker(sc["whole"], inside)
    sc["fail"] = bool(blocking) or hard or qs["overall"] == "fail"

    if a.json:
        print(json.dumps(_json_out(a, graph, qs, res, sc), ensure_ascii=False, indent=1))
        return 1 if sc["fail"] else 0
    _print_gate_report(a, m, qs, res, sc)
    return _print_conclusion(sc["fail"], a, res, sc)


def whole_has_blocker(whole, inside):
    """改公共层 → 全仓进入硬红区间：范围里只要还有 P0/P1 就不能放行。"""
    return bool(whole and any(f["severity"] in ("P0", "P1") for f in inside))


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="原子图谱门（默认只判本次改动）")
    ap.add_argument("--base", default=None, help="基线 ref（默认 origin/HEAD → HEAD~1）")
    ap.add_argument("--all", action="store_true", help="判全仓（忽略增量范围）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（人读信息走 stderr）")
    ap.add_argument("--emit", action="store_true", help="重新生成 docs/ATOMS_GRAPH.md 与组合图")
    ap.add_argument("--check-drift", action="store_true", help="只检查生成物是否与代码同步")
    ap.add_argument("--update-baseline", action="store_true", help="用当前结果重写基线台账")
    ap.add_argument("--update-signatures", action="store_true", help="用当前实现重写签名快照（认领）")
    ap.add_argument("--shrink", action="store_true", help="台账自动收缩：删掉已不再触发的条目")
    ap.add_argument("--dry-run", action="store_true", help="配 --shrink：只报告，不写盘（CI 用）")
    ap.add_argument("--impact", default=None, help="看某条能力/某个文件改动后的波及面（不判门）")
    ap.add_argument("--expires", default=None, help="配 --update-baseline：台账整体到期日")
    ap.add_argument("--reason", default=None, help="配 --update-*：理由/认领说明")
    return ap.parse_args(argv)


def main(argv=None):
    a = _parse_args(argv)
    graph = core.build_graph(ROOT)
    m = graph["metrics"]

    if a.impact:
        prob = _impact(graph, a.impact)
        if a.json:
            print(json.dumps(prob, ensure_ascii=False, indent=1))
            return 0
        _print_impact(prob)
        return 0
    if a.emit:
        return _do_emit(graph)
    if a.check_drift:
        return _do_drift(graph)
    if a.update_baseline:
        return _do_update_baseline(a, graph)
    if a.update_signatures:
        return _do_update_signatures(a, graph)
    bl = core.load_baseline(ROOT)
    if a.shrink:
        return _do_shrink(a, graph, bl)
    return _do_gate(a, graph, m, bl)


if __name__ == "__main__":
    sys.exit(main())
