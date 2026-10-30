#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_contracts.py — 契约总账：把 42 个原子「声明的」和「实现的」摆在一起对账。

为什么要有这道账：
    test.run 的 timeout 在 manifest 里声明了，实现层一直没接，传进去直接 CAP_CONTRACT
    —— 2026-10-23 端到端测试才撞出来。声明与实现之间没有机器可查的对照，
    这类断裂只能靠人记。同一个坑：cli-providers 的 inputs 是从模板抄来的 ["text"]，
    和它三个能力的真实签名一处都对不上，从建库起就错着，没人发现。

两个方向都查：
    声明了但实现接不住  → 断链（硬伤），--check 时 exit 1
    实现接得住但没声明  → 漏记，只在总账里列出，不挡门（清单不完整 ≠ 骗人）

判不了的情况不硬判：能力签名吃 **kwargs 或静态解析不出来（opaque）时，
    声明与实现的对应关系本来就无法静态确定，这里如实标「判不了」，不冒充通过。

用法：
    python scripts/export_contracts.py --emit      # 生成 docs/CONTRACTS.md + config/contracts.json
    python scripts/export_contracts.py --check     # 只对账，有断链 exit 1
    python scripts/export_contracts.py --json      # 机读
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import agent_loader  # noqa: E402

SIG_PATH = os.path.join(ROOT, "config", "atoms_signatures.json")
MD_PATH = os.path.join(ROOT, "docs", "CONTRACTS.md")
JSON_PATH = os.path.join(ROOT, "config", "contracts.json")


def _load_sigs(path=SIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["capabilities"]


def _index_by_atom(sigs):
    """{能力名: 签名} → {原子名: [能力名]}"""
    idx = {}
    for cap, sig in sigs.items():
        idx.setdefault(sig["atom"], []).append(cap)
    return {k: sorted(v) for k, v in idx.items()}


def _accepts(caps, sigs):
    """一个原子所有能力的参数并集，外加两个「判不了」标记。"""
    params, kw, opaque = set(), False, False
    for cap in caps:
        sig = sigs[cap]
        params |= set(sig.get("params") or [])
        kw = kw or bool(sig.get("has_kwargs"))
        opaque = opaque or bool(sig.get("opaque"))
    return params, kw, opaque


def _atom_row(name, manifest, sigs, idx):
    """一个原子的契约对账结果。判不了就明说判不了，不当成通过。"""
    caps = idx.get(name, [])
    params, kw, opaque = _accepts(caps, sigs)
    declared = list(manifest.get("inputs") or [])
    judgeable = not (kw or opaque)
    broken = [i for i in declared if i not in params] if judgeable else []
    return {
        "atom": name,
        "domain": manifest.get("domain"),
        "version": manifest.get("version"),
        "open_source": bool(manifest.get("open_source")),
        "entry": manifest.get("entry"),
        "path": manifest.get("path"),
        "provides": list(manifest.get("provides") or []),
        "depends_on": list(manifest.get("depends_on") or []),
        "declared_inputs": declared,
        "declared_outputs": list(manifest.get("outputs") or []),
        "accepted_params": sorted(params),
        "broken": broken,
        "undeclared": sorted(params - set(declared)),
        "judgeable": judgeable,
        "permission": manifest.get("permission") or {},
        "resource": manifest.get("resource") or {},
        "capabilities": caps,
    }


def build_rows(sig_path=SIG_PATH):
    """扫全部 manifest + 签名基线，产出按原子排好序的对账行。"""
    manifests = agent_loader.scan()["data"]["manifests"]
    sigs = _load_sigs(sig_path)
    idx = _index_by_atom(sigs)
    rows = [_atom_row(n, m, sigs, idx) for n, m in sorted(manifests.items())]
    return rows


def _summary(rows):
    return {
        "atoms": len(rows),
        "capabilities": sum(len(r["capabilities"]) for r in rows),
        "declared_inputs": sum(len(r["declared_inputs"]) for r in rows),
        "broken": sum(len(r["broken"]) for r in rows),
        "undeclared": sum(len(r["undeclared"]) for r in rows),
        "judgeable": sum(1 for r in rows if r["judgeable"]),
        "unjudgeable_atoms": [r["atom"] for r in rows if not r["judgeable"]],
    }


def _md_head(stats):
    lines = [
        "# 契约总账（声明 ↔ 实现）",
        "",
        "> 本文件由 `scripts/export_contracts.py --emit` 生成，**禁手写**。",
        "> 改完 manifest 或能力签名后重跑一次；`--check` 会在有断链时失败。",
        "",
        "## 这份账在查什么",
        "",
        "| 方向 | 含义 | 处理 |",
        "|------|------|------|",
        "| 断链 | manifest 声明了这个输入，但该原子所有能力都不接受 | **硬伤**，`--check` 时 exit 1 |",
        "| 漏记 | 能力接受这个参数，manifest 没声明 | 清单不完整，只列出不挡门 |",
        "| 判不了 | 能力吃 `**kwargs` 或签名静态解析不出 | 如实标注，不冒充通过 |",
        "",
        "## 总数",
        "",
        "| 项 | 数 |",
        "|----|----|",
        "| 原子 | %d |" % stats["atoms"],
        "| 能力 | %d |" % stats["capabilities"],
        "| 声明输入 | %d |" % stats["declared_inputs"],
        "| 断链 | %d |" % stats["broken"],
        "| 漏记 | %d |" % stats["undeclared"],
        "| 可静态判定的原子 | %d / %d |" % (stats["judgeable"], stats["atoms"]),
    ]
    if stats["unjudgeable_atoms"]:
        lines.append("")
        lines.append("判不了的原子（吃 `**kwargs` 或签名不透明）："
                     + "、".join("`%s`" % a for a in stats["unjudgeable_atoms"]))
    return lines


def _md_broken(rows):
    bad = [r for r in rows if r["broken"]]
    lines = ["", "## 断链（必须为 0）", ""]
    if not bad:
        lines.append("无。所有声明的输入都能被实现接住。")
        return lines
    lines.append("| 原子 | 接不住的声明输入 | 实现实际接受 |")
    lines.append("|------|------------------|--------------|")
    for r in bad:
        lines.append("| `%s` | %s | %s |" % (
            r["atom"],
            "、".join("`%s`" % b for b in r["broken"]),
            "、".join("`%s`" % p for p in r["accepted_params"]) or "（无）"))
    return lines


def _md_table(rows):
    lines = [
        "", "## 按域汇总", "",
        "| 原子 | 域 | 能力数 | 声明输入 | 断链 | 漏记 |",
        "|------|----|--------|----------|------|------|",
    ]
    for r in rows:
        lines.append("| `%s` | %s | %d | %d | %s | %d |" % (
            r["atom"], r["domain"], len(r["capabilities"]), len(r["declared_inputs"]),
            "**%d**" % len(r["broken"]) if r["broken"] else "0", len(r["undeclared"])))
    return lines


def _brief(items):
    """把列表拼成反引号顿号串；空就给「（无）」，别留个空冒号让人猜。"""
    return "、".join("`%s`" % i for i in items) or "（无）"


def _atom_head(r):
    """原子身份与声明面：哪个域、什么版本、提供哪些能力、依赖谁。"""
    return [
        "- 域：%s ｜ 版本：%s ｜ 开源：%s ｜ 入口：`%s`" % (
            r["domain"], r["version"], "是" if r["open_source"] else "否", r["entry"]),
        "- 能力（%d）：%s" % (len(r["capabilities"]), _brief(r["capabilities"])),
        "- 依赖：%s" % _brief(r["depends_on"]),
        "- 声明输入：%s" % _brief(r["declared_inputs"]),
        "- 声明输出：%s" % _brief(r["declared_outputs"]),
        "- 实现接受：%s" % _brief(r["accepted_params"]),
    ]


def _atom_verdict(r):
    """对账结论：判不了就明说判不了，断链和漏记分开写。"""
    if not r["judgeable"]:
        return ["- 对账：**判不了**（吃 `**kwargs` 或签名不透明）"]
    if r["broken"]:
        return ["- 断链：%s" % _brief(r["broken"])]
    return []


def _atom_block(r):
    lines = ["", "### %s" % r["atom"], ""]
    lines.extend(_atom_head(r))
    lines.extend(_atom_verdict(r))
    if r["undeclared"]:
        lines.append("- 漏记（清单没写、实现接受）：%s" % _brief(r["undeclared"]))
    perm = r["permission"]
    if perm:
        lines.append("- 权限：%s%s" % (
            "/".join(perm.get("needs") or []) or "无",
            "，需要网络" if perm.get("network") else "，不联网"))
    return lines


def _md_details(rows):
    lines = ["", "## 逐原子明细", ""]
    for r in rows:
        lines.extend(_atom_block(r))
    return lines


def render_md(rows):
    stats = _summary(rows)
    lines = _md_head(stats) + _md_broken(rows) + _md_table(rows) + _md_details(rows)
    return "\n".join(lines).rstrip() + "\n", stats


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def emit(rows):
    md, stats = render_md(rows)
    _write(MD_PATH, md)
    _write(JSON_PATH, json.dumps({"schema": "codeagent-contracts-v1",
                                  "summary": stats, "atoms": rows},
                                 ensure_ascii=False, indent=1) + "\n")
    print("已生成：%s" % os.path.relpath(MD_PATH, ROOT))
    print("已生成：%s" % os.path.relpath(JSON_PATH, ROOT))
    print("原子 %d ／ 能力 %d ／ 断链 %d ／ 漏记 %d"
          % (stats["atoms"], stats["capabilities"], stats["broken"], stats["undeclared"]))
    return 0


def check(rows):
    bad = [r for r in rows if r["broken"]]
    if not bad:
        print("[契约总账] %d 个原子的声明输入全部能被实现接住" % len(rows))
        return 0
    print("[契约总账] 断链 %d 处：" % sum(len(r["broken"]) for r in bad))
    for r in bad:
        print("  - %s 声明了 %s，实现只接受 %s"
              % (r["atom"], r["broken"], r["accepted_params"] or "（无）"))
    print("结论：不通过。改 manifest 对齐实现，或把参数接到能力层。")
    return 1


def main():
    ap = argparse.ArgumentParser(description="契约总账：manifest 声明 ↔ 能力签名对账")
    ap.add_argument("--emit", action="store_true", help="生成 docs/CONTRACTS.md 与 config/contracts.json")
    ap.add_argument("--check", action="store_true", help="只对账，有断链 exit 1")
    ap.add_argument("--json", action="store_true", help="机读输出")
    args = ap.parse_args()
    rows = build_rows()
    if args.json:
        print(json.dumps({"summary": _summary(rows),
                          "broken": [{"atom": r["atom"], "broken": r["broken"]} for r in rows if r["broken"]]},
                         ensure_ascii=False, indent=1))
        return 0
    if args.check:
        return check(rows)
    if args.emit:
        return emit(rows)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
