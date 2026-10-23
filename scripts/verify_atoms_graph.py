#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_atoms_graph.py — 原子图谱门（第五道 CI 门）

拦的是**编排错误**，不是风格问题：调了不存在的能力、传了接不住的参数、
组合里点了一个不存在的步骤、声明依赖和真实调用对不上、依赖成环。

为什么不判全量：存量必然红（当前 165 条能力里只有 27 条有调用点），
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
    python scripts/verify_atoms_graph.py --update-baseline --expires 2027-01-31 --reason "存量"
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


def main():
    ap = argparse.ArgumentParser(description="原子图谱门（默认只判本次改动）")
    ap.add_argument("--base", default=None, help="基线 ref（默认 origin/HEAD → HEAD~1）")
    ap.add_argument("--all", action="store_true", help="判全仓（忽略增量范围）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（人读信息走 stderr）")
    ap.add_argument("--emit", action="store_true", help="重新生成 docs/ATOMS_GRAPH.md 与组合图")
    ap.add_argument("--check-drift", action="store_true", help="只检查生成物是否与代码同步")
    ap.add_argument("--update-baseline", action="store_true", help="用当前结果重写基线台账")
    ap.add_argument("--expires", default=None, help="配 --update-baseline：台账整体到期日")
    ap.add_argument("--reason", default=None, help="配 --update-baseline：基线条目理由")
    a = ap.parse_args()

    graph = core.build_graph(ROOT)
    m = graph["metrics"]

    if a.emit:
        md, html = _emit(graph)
        print("已生成：%s" % os.path.relpath(md, ROOT))
        print("已生成：%s" % os.path.relpath(html, ROOT))
        return 0

    if a.check_drift:
        bad = _drift(graph)
        if bad:
            print("[原子图谱门] 生成物与代码不同步：")
            for b in bad:
                print("  - " + b)
            print("结论：不通过。跑 `python scripts/verify_atoms_graph.py --emit` 重新生成后一起提交。")
            return 1
        print("[原子图谱门] 生成物与代码同步（docs/ATOMS_GRAPH.md、docs/atoms_graph_compose.html）")
        return 0

    bl = core.load_baseline(ROOT)
    if a.update_baseline:
        if not (a.expires and a.reason):
            print("--update-baseline 需要同时给 --expires 与 --reason（台账不许无期限、不许无理由）",
                  file=sys.stderr)
            return 2
        data = core.dump_baseline(graph, a.reason, a.expires)
        path = core.baseline_path(ROOT)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("基线台账已重写：%s（%d 条，到期 %s）"
              % (os.path.relpath(path, ROOT), len(data["entries"]), a.expires))
        return 0

    res = core.apply_baseline(graph["findings"], bl)

    if a.all:
        inside, outside, whole, why = core.scope(res["open"], None, {})
        changed = None
    else:
        base, how = core.pick_base(ROOT, a.base)
        if not base:
            print("[原子图谱门] 取不到基线 ref（浅克隆/无历史），本次不判 —— 这不是通过，是判不了。")
            print("             要在 CI 里生效需 checkout 至少两个提交（fetch-depth: 2 或 PR 的 base）。")
            return 0
        changed = core.changed_files(ROOT, base)
        inside, outside, whole, why = core.scope(res["open"], changed, core.load_registry(ROOT)["agents"])

    # 基线问题（过期/失效/无理由）与范围无关，一律算不通过
    blocking = list(inside) + [p for p in res["problems"]
                               if p["rule"] in ("BASELINE_EXPIRED", "BASELINE_NO_REASON")]
    stale = [p for p in res["problems"] if p["rule"] == "BASELINE_STALE"]
    if a.all:
        blocking += stale  # 全仓判时才追究失效条目，免得无关 PR 被台账噪声挡住

    fail = bool(blocking) or bool(whole and any(f["severity"] in ("P0", "P1") for f in inside))

    if a.json:
        print(json.dumps({
            "metrics": m,
            "mode": "all" if a.all else ("增量：" + why),
            "changed_files": len(changed) if changed is not None else None,
            "whole_repo": whole,
            "open": inside, "out_of_scope": len(outside),
            "waived": len(res["waived"]), "baseline_problems": res["problems"],
            "verdict": "fail" if fail else "pass",
        }, ensure_ascii=False, indent=1))
        return 1 if fail else 0

    err = sys.stderr
    err.write("[原子图谱门] %s\n" % ("全仓判" if a.all else ("增量判｜" + why)))
    err.write("             原子 %d / 能力 %d / 调用点 %d（可解析 %d）/ 组合 %d 条 %d 步\n"
              % (m["atoms"], m["capabilities"], m["call_sites_raw"], m["call_sites"],
                 m["chains"], m["chain_steps"]))
    if whole and not a.all:
        err.write("             ⚠ 改了公共层 → 全仓进入硬红区间（只判改动文件等于放行全部契约）\n")

    print("规则命中（全仓）：")
    for rule in core.RULES:
        n = m["rules_fired"].get(rule, 0)
        if n:
            print("  %-26s %s  %d" % (rule, core.RULES[rule][0], n))

    if inside:
        print("\n本次范围内的问题（%d 条，未在台账登记）：" % len(inside))
        for f in inside:
            loc = ("  @%s" % f["file"]) if f.get("file") else ""
            print("  [%s] %-26s %s%s\n        %s"
                  % (f["severity"], f["rule"], f["subject"], loc, f["detail"]))
    if res["problems"]:
        print("\n台账问题（%d 条）：" % len(res["problems"]))
        for p in res["problems"]:
            print("  [%s] %-18s %s — %s" % (p["severity"], p["rule"], p["subject"], p["detail"]))
    if res["waived"]:
        print("\n台账已登记（不挡门）：%d 条" % len(res["waived"]))
    if outside:
        print("本次范围外（存量，未计入）：%d 条" % len(outside))

    if fail:
        print("\n结论：不通过 ⇒ 修代码，或在台账里登记（必须写 reason 与 expires）。")
        return 1
    if not a.all and not whole and not changed:
        print("\n结论：通过 —— 本次没有任何文件改动，没有可判的东西（不是「全仓都干净」）。")
        return 0
    print("\n结论：通过（本次范围内 0 条未登记问题；台账已登记 %d 条，本次范围外存量 %d 条）"
          % (len(res["waived"]), len(outside)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
