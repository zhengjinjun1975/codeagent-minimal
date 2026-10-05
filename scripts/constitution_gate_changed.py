#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""constitution_gate_changed.py — 代码宪法门（只判「本次改动」的那几个文件）

为什么不判全量：仓里有**存量**越限（`config/constitution_baseline.json` 记的就是这批，
2026-10-05 实测 809 条）。全量扫必然红 ⇒ 门变成噪声、被人无视。
CI 上要拦的是「新增/改动引入的越限」，存量走基线台账慢慢清。

所以本件：只看相对某个基线 ref 变更过的 `.py`（新增/改名/修改都算），逐个过
`constitution_gate.check_file`，任一未登记越限即 exit 1。已登记的 `# 宪法例外:` 与
`.constitutionignore` 的排除照旧生效（复用同一个门，不另写一份判定）。

基线 ref 的取法（依次）：`--base` / 环境变量 `CONSTITUTION_BASE` → `origin/HEAD` → `HEAD~1`。
拿不到（浅克隆、无历史、全零 SHA）时**明确打印并 exit 0**：判不了就不假装判过，也不误报红。

用法：
    python scripts/constitution_gate_changed.py                # 自动取基线
    python scripts/constitution_gate_changed.py --base origin/main
    python scripts/constitution_gate_changed.py --base HEAD~1 --json
"""
import argparse
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import constitution_gate as g  # noqa: E402

NULL_SHA = "0" * 40


def _git(*args):
    r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, text=True,
                       errors="ignore", timeout=120)
    return r.returncode, (r.stdout or "").strip()


def pick_base(explicit):
    for cand in (explicit, os.environ.get("CONSTITUTION_BASE")):
        if cand and cand.strip() and cand.strip() != NULL_SHA:
            rc, _ = _git("rev-parse", "--verify", cand.strip() + "^{commit}")
            if rc == 0:
                return cand.strip(), "参数/环境变量"
    rc, out = _git("rev-parse", "--verify", "origin/HEAD^{commit}")
    if rc == 0:
        return "origin/HEAD", "默认(origin/HEAD)"
    rc, _ = _git("rev-parse", "--verify", "HEAD~1^{commit}")
    if rc == 0:
        return "HEAD~1", "默认(HEAD~1)"
    return None, "取不到基线"


def load_baseline():
    """存量台账：{文件: {越限种类: 条数}}（不记行号，行号会漂）。读不到返回空表。"""
    p = os.path.join(ROOT, "config", "constitution_baseline.json")
    try:
        d = json.load(io.open(p, encoding="utf-8"))
        return {k: dict(v) for k, v in (d.get("files") or {}).items() if isinstance(v, dict)}
    except Exception as e:
        # 读不到台账就按空表处理，但必须留痕（静默吞错是宪法禁止项）
        print("（提示）存量台账读不到（%s）：本件不扣存量，可能偏严。" % type(e).__name__,
              file=sys.stderr)
        return {}


def changed_py(base):
    """基线→HEAD 的改动 + 工作区未提交的改动，合并去重（本地跑也能拦住手上的活）。"""
    files = set()
    for args in (("diff", "--name-only", "--diff-filter=ACMR", base, "HEAD"),
                 ("diff", "--name-only", "--diff-filter=ACMR"),          # 未暂存
                 ("diff", "--name-only", "--cached", "--diff-filter=ACMR")):  # 已暂存
        rc, out = _git(*args)
        if rc == 0:
            for l in out.splitlines():
                files.add(l.strip())
    return sorted(f for f in files if f.endswith(".py"))


def main():
    ap = argparse.ArgumentParser(description="代码宪法门（只判本次改动的文件）")
    ap.add_argument("--base", default=None, help="基线 ref（默认 origin/HEAD → HEAD~1）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--thresholds", default=None)
    a = ap.parse_args()

    th = g.load_thresholds(a.thresholds)
    marker = th["例外机制"]["标记"]
    pats = g.load_ignore()

    base, how = pick_base(a.base)
    if not base:
        print("[宪法门·改动] 取不到基线 ref（浅克隆/无历史），本次不判 —— 不算通过，是判不了。")
        print("             要在 CI 里生效需 checkout 至少两个提交（fetch-depth: 2 或 PR 的 base）。")
        return 0
    # 注意：--json 时正文只能有 JSON（下面几处人读的说明都进 stderr，否则调用方解析不了）
    (sys.stderr if a.json else sys.stdout).write("[宪法门·改动] 基线 %s（%s）\n" % (base, how))

    cands = []
    for rel in changed_py(base):
        fp = os.path.join(ROOT, rel)
        if not os.path.isfile(fp) or os.path.basename(fp).startswith("_"):
            continue
        cands.append(fp)

    found, waived, hints, excluded = [], [], [], []
    for fp in cands:
        why = g.is_ignored(fp, pats)
        if why:
            excluded.append({"file": os.path.relpath(fp, ROOT).replace("\\", "/"), "by": why})
            continue
        f, w, h = g.check_file(fp, th)
        found += f
        waived += w
        hints += h

    # 存量扣减：只判「本次改动新引入」的越限（按 文件+种类 计数比对，容忍行号漂移）
    BL = load_baseline()
    if BL:
        quotas, kept = {}, []
        for v in found:
            rel = str(v["file"]).replace("\\", "/")
            key = (rel, v["kind"])
            allowed = (BL.get(rel) or {}).get(v["kind"], 0)
            if quotas.get(key, 0) < allowed:
                quotas[key] = quotas.get(key, 0) + 1
                waived.append({"file": rel, "line": v["line"], "kind": v["kind"],
                               "reason": "存量（基线台账已记）"})
                continue
            kept.append(v)
        found = kept
    else:
        print("（提示）未读到 config/constitution_baseline.json：本件不扣存量，可能偏严。",
              file=sys.stderr)

    if a.json:
        print(json.dumps({"base": base, "files": len(cands), "violations": found,
                          "hints": hints, "waived": waived, "excluded": excluded},
                         ensure_ascii=False, indent=1))
        return 1 if found else 0

    print("             改动文件 %d 个（排除 %d）" % (len(cands), len(excluded)))
    for v in found:
        print("  [%s] %s:%s  %s — %s" % (v["tier"], v["file"], v["line"], v["kind"], str(v.get("detail"))[:90]))
    for v in waived:
        print("  [例外] %s:%s  %s — %s" % (v["file"], v["line"], v["kind"], str(v.get("reason"))[:80]))
    for v in hints:
        print("  [提示] %s:%s  %s" % (v["file"], v["line"], v["kind"]))
    if found:
        print("\n结论：本次改动引入 %d 条未登记越限 ⇒ 不通过（改代码，或在越限处写 `%s <理由>`）"
              % (len(found), marker))
        return 1
    print("\n结论：本次改动无未登记越限 ⇒ 通过（已登记例外 %d 条、提示 %d 条照常打印）"
          % (len(waived), len(hints)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
