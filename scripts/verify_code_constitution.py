#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_code_constitution.py — 代码宪法落地的**接线门**（纯标准库，可复跑）。

判的不是"文档写没写"，而是"**每一次 codeagent 写码任务的系统提示里到底有没有它**"，
以及"文档与阈值单点有没有漂"。

断言（任一不成立即退出码非 0）：
  ① docs/代码宪法.md 存在、BEGIN/END 成对、正文非空且在预算内；
  ② 三条档位都在正文里（强宪法 P0 / 重核心 P1 / 轻外围 P2），条文编号完整（S1..S5 / C1..C6 / L1..L5）；
  ③ code.implement 路径：code_agent_engine.PONYTAIL_SYSTEM 含宪法正文；
  ④ code.runloop 路径：code-runloop/prompts.build_system_prompt() 含宪法正文；
  ⑤ 注入幂等；缺文件/缺标记时抛错（不静默降级）；
  ⑥ 阈值单点 config/constitution_thresholds.json 存在，且文档正文写的阈值与它一致（防两边漂）；
  ⑦ 执法者 scripts/constitution_gate.py 在"干净样本"上 PASS、在"违规样本"上 FAIL（门真的会咬）。

用法: python scripts/verify_code_constitution.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agents", "code", "code-runloop"))

FAILS = []
N_ASSERT = 0


def chk(name, cond, extra=""):
    global N_ASSERT
    N_ASSERT += 1
    print("[%s] %s %s" % ("OK" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


def _clean_sample(tmpdir):
    p = os.path.join(tmpdir, "clean_sample.py")
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        '"""干净样本。"""\nimport os\n\n\ndef join_name(parts):\n    """拼名字。"""\n'
        '    return os.path.join(*parts)\n\n\nif __name__ == "__main__":\n    print(join_name(["a", "b"]))\n')
    return p


def _bad_sample(tmpdir):
    p = os.path.join(tmpdir, "bad_sample.py")
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        '"""反面样本。"""\nimport json\n\n\ndef empty_stub():\n    pass\n'
        '\n\ndef swallow():\n    try:\n        int("x")\n    except Exception:\n        pass\n'
        '\n\nKEY = "sk-abcdefghijklmnopqrstuvwxyz0123"\n')
    return p


# 宪法例外: 逐项断言的清单，拆开要把"失败计数/路径常量"当参数传（收益 < 依赖成本）
def main():
    import code_constitution as cc

    doc = os.path.join(ROOT, "docs", "代码宪法.md")
    chk("①a 宪法文件存在", os.path.isfile(doc), doc)
    raw = io.open(doc, encoding="utf-8").read() if os.path.isfile(doc) else ""
    chk("①b 标记成对", raw.count(cc.BEGIN) == 1 and raw.count(cc.END) == 1)
    body = cc.constitution_text()
    chk("①c 正文非空", len(body) > 500, "%d 字符" % len(body))
    chk("①d 正文在预算内", len(body) <= cc.MAX_CHARS, "上限 %d" % cc.MAX_CHARS)

    # v3：常驻核心只放 P0 与按阈值判的 P1；P2（L1..L5）在评审条文里，按需加载。
    for tier in ("强宪法", "重核心"):
        chk("② 常驻核心含%s档" % tier, tier in body)
    for pref, nums in (("S", (1, 2, 3, 4, 5)), ("C", (1, 3, 4, 5, 6))):
        missing = [i for i in nums if "\n%s%d " % (pref, i) not in body]
        chk("② 常驻核心条文 %s 齐全" % ",".join("%s%d" % (pref, i) for i in nums),
            not missing, "缺 %s" % missing)
    chk("② 轻外围不在常驻（分级生效）", "轻外围" not in body and "\nL1 " not in body)
    rev = cc.review_text()          # 装载器已支持按层读取（v3 第二步）
    # 功能性分层完整性：两层不重复、无孤儿条文；写时层要写明"判定层"是谁在判
    _core_nums = ["S1 ", "S2 ", "S3 ", "S4 ", "S5 ", "C1 ", "C3 ", "C4 ", "C5 ", "C6 "]
    _rev_nums = ["L1 ", "L2 ", "L3 ", "L4 ", "L5 "]
    chk("② 分层：两层无重复条文", not [x for x in _core_nums if x in _rev_nums])
    chk("② 分层：写时层指明判定层（门）", "constitution_gate.py" in body)
    missing = [i for i in range(1, 6) if "\nL%d " % i not in rev]
    chk("② 评审条文 L1..L5 齐全", not missing, "缺 %s" % missing)
    chk("② 评审条文与常驻不重叠", "强宪法" not in rev and "\nS1 " not in rev)

    import importlib
    el = importlib.import_module("code_agent_engine")
    pony = getattr(el, "PONYTAIL_SYSTEM", "")
    chk("③ code_agent_engine.PONYTAIL_SYSTEM 含宪法", body[:80] in pony, "prompt %d 字符" % len(pony))
    chk("③ 未出现缺失标记", "代码宪法未加载" not in pony)

    pr = importlib.import_module("prompts")
    sysp = pr.build_system_prompt()
    chk("④ runloop build_system_prompt 含宪法", body[:80] in sysp, "prompt %d 字符" % len(sysp))
    chk("④ 未出现缺失标记", "代码宪法未加载" not in sysp)

    chk("⑤a 注入幂等", cc.inject(sysp).count(body[:80]) == 1)
    missing = os.path.join(ROOT, "_no_such_constitution.md")
    try:
        cc.constitution_text(path=missing, use_cache=False)
        chk("⑤b 缺文件抛错", False, "竟然没抛")
    except cc.CodeConstitutionError:
        chk("⑤b 缺文件抛错", True)
    chk("⑤c 缺失留显式标记", "代码宪法未加载" in cc.inject_or_mark("SYS", path=missing))

    tf = os.path.join(ROOT, "config", "constitution_thresholds.json")
    chk("⑥a 阈值文件存在", os.path.isfile(tf), tf)
    th = json.load(io.open(tf, encoding="utf-8")) if os.path.isfile(tf) else {}
    p1 = th.get("P1_阈值", {})
    for key, tmpl in (("fn_max_lines", "≤%d 行"), ("nest_max_depth", "≤%d 层"), ("cc_max", "≤%d")):
        val = p1.get(key)
        chk("⑥b 文档写明 %s=%s" % (key, val), val is not None and (tmpl % val) in body)
    chk("⑥c 例外标记在文档里一致", th.get("例外机制", {}).get("标记", "X") in body)

    gate = os.path.join(ROOT, "scripts", "constitution_gate.py")
    chk("⑦a 执法者存在", os.path.isfile(gate), gate)
    with tempfile.TemporaryDirectory() as td:
        rc_clean = subprocess.run([sys.executable, gate, _clean_sample(td)],
                                  capture_output=True, text=True).returncode
        rc_bad = subprocess.run([sys.executable, gate, _bad_sample(td)],
                                capture_output=True, text=True).returncode
    chk("⑦b 干净样本 PASS", rc_clean == 0, "rc=%d" % rc_clean)
    chk("⑦c 违规样本 FAIL", rc_bad == 1, "rc=%d" % rc_bad)

    print("\n结论: %s（%d 项断言，失败 %d）" % ("PASS" if not FAILS else "FAIL", N_ASSERT, len(FAILS)))
    if FAILS:
        print("失败项: %s" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
