#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""constitution_survey.py — 全仓宪法体检 + 基线趋势对比（把 1117 这条基线变成"能看趋势"）。

用法：
    python scripts/constitution_survey.py                     # 体检 + 与基线比趋势
    python scripts/constitution_survey.py --write-baseline    # 把当前结果写成新基线（只有人来定）
    python scripts/constitution_survey.py --top 15 --json     # 机器可读

为什么要有基线：单次 1000+ 条不是"1000 个 bug"，是阈值分布 + 少量真问题；
只有当它变成可比较的量，才谈得上"这次改造让复杂度降了多少"。
"""
import argparse
import collections
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import constitution_gate as gate  # noqa: E402

BASELINE = os.path.join(ROOT, "config", "constitution_baseline.json")


def survey(root=ROOT):
    th = gate.load_thresholds()
    pats = gate.load_ignore()
    found, waived, hints, excluded, n = [], [], [], [], 0
    for fp in gate.iter_files(root):
        if os.path.basename(fp).startswith("_"):
            continue
        why = gate.is_ignored(fp, pats)
        if why:
            excluded.append({"file": fp, "by": why})
            continue
        n += 1
        f, w, h = gate.check_file(fp, th)
        found += f
        waived += w
        hints += h
    return {"files": n, "violations": found, "waived": waived, "hints": hints, "excluded": excluded}


def summarize(res, top=10):
    out = {
        "files": res["files"],
        "counts": {"unregistered": len(res["violations"]), "waived": len(res["waived"]),
                   "hints": len(res["hints"]), "excluded": len(res["excluded"])},
        "by_kind": dict(collections.Counter(v["kind"] for v in res["violations"]).most_common()),
        "top_files": dict(collections.Counter(v["file"] for v in res["violations"]).most_common(top)),
    }
    return out


# 宪法例外: CLI 入口（体检→汇总→比基线→打印），拆开要把三类结果集与基线来回传
def main():
    ap = argparse.ArgumentParser(description="全仓宪法体检 + 基线趋势")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    res = survey(args.root)
    s = summarize(res, args.top)

    if args.write_baseline:
        io.open(BASELINE, "w", encoding="utf-8").write(json.dumps(s, ensure_ascii=False, indent=1))
        print("基线已写:", BASELINE)
        print(json.dumps(s["counts"], ensure_ascii=False))
        return 0

    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=1))
        return 0

    print("宪法体检 · %d 个文件（排除 %d 个夹具/归档/产物）" % (s["files"], s["counts"]["excluded"]))
    print("未登记越限 %d ／ 已登记例外 %d ／ 提示 %d"
          % (s["counts"]["unregistered"], s["counts"]["waived"], s["counts"]["hints"]))
    print("\n按类型:")
    for k, v in s["by_kind"].items():
        print("   %-18s %d" % (k, v))
    print("\n最集中的文件（前 %d）:" % args.top)
    for k, v in s["top_files"].items():
        print("   %-52s %d" % (k, v))

    if os.path.isfile(BASELINE):
        b = json.load(io.open(BASELINE, encoding="utf-8"))
        print("\n对比基线（%s）:" % os.path.relpath(BASELINE, ROOT))
        for key, cur in (("unregistered", s["counts"]["unregistered"]), ("waived", s["counts"]["waived"]),
                         ("hints", s["counts"]["hints"])):
            old = b.get("counts", {}).get(key)
            if old is None:
                continue
            delta = cur - old
            arrow = "→ 持平" if delta == 0 else ("↓ 改善 %d" % -delta if delta < 0 else "↑ 变差 %d" % delta)
            print("   %-14s 基线 %-6s 现在 %-6s %s" % (key, old, cur, arrow))
        bk = b.get("by_kind", {})
        grew = [(k, v, bk.get(k, 0), v - bk.get(k, 0)) for k, v in s["by_kind"].items() if v > bk.get(k, 0)]
        if grew:
            print("   类型变差的是:", ", ".join("%s +%d" % (k, d) for k, _, _, d in grew))
    else:
        print("\n（还没有基线；确认口径后用 --write-baseline 落一份）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
