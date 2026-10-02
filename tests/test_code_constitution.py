#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代码宪法接线测试（不调模型，纯接线断言）。

要证的四件事：
  1. 两条写码路径的系统提示里**都**含宪法正文（code.implement 用 engine_llm.PONYTAIL_SYSTEM；
     code.runloop 用 agents/code/code-runloop/prompts.build_system_prompt()）；
  2. 注入幂等；
  3. 宪法缺失/标记缺失时**抛错**，不静默降级；
  4. 宪法条文完整（1..14 条都在提示里，防止只注入半截）。

对应文档：docs/代码宪法.md 第三节（那段是承诺，这里是执行）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "agents", "code", "code-runloop")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

import code_constitution as cc  # noqa: E402


def test_implement_path_carries_constitution():
    import code_agent_engine as engine_llm
    body = cc.constitution_text()
    assert body[:80] in engine_llm.PONYTAIL_SYSTEM
    assert "代码宪法未加载" not in engine_llm.PONYTAIL_SYSTEM


def test_runloop_path_carries_constitution():
    import prompts
    body = cc.constitution_text()
    sysp = prompts.build_system_prompt()
    assert body[:80] in sysp
    assert "代码宪法未加载" not in sysp


def test_all_rules_present_in_both_paths():
    import code_agent_engine as engine_llm
    import prompts
    body = cc.constitution_text()
    pony, sysp = engine_llm.PONYTAIL_SYSTEM, prompts.build_system_prompt()
    # v3：常驻核心 = P0 五条 + 按阈值判的 P1（C1/C3/C4/C6）；P2（L1..L5）在评审条文里，写码路径不带
    for tier in ("强宪法", "重核心"):
        assert tier in body
    for pref, nums in (("S", (1, 2, 3, 4, 5)), ("C", (1, 3, 4, 5, 6))):
        for i in nums:
            token = "\n%s%d " % (pref, i)
            assert token in body, "常驻核心缺 %s%d" % (pref, i)
            assert token in pony, "code.implement 路径缺 %s%d" % (pref, i)
            assert token in sysp, "code.runloop 路径缺 %s%d" % (pref, i)
    # 分级生效：写码路径不带评审条文
    for k in ("轻外围", "\nL1 "):
        assert k not in pony and k not in sysp, "写码路径不该带评审条文: %r" % k


def test_thresholds_file_matches_doc():
    """阈值单点与注入正文不许漂：文档写的数必须等于 config/constitution_thresholds.json。"""
    import io as _io
    import json as _json
    import os as _os
    tf = _os.path.join(ROOT, "config", "constitution_thresholds.json")
    th = _json.load(_io.open(tf, encoding="utf-8"))
    p1 = th["P1_阈值"]
    body = cc.constitution_text()
    for key, tmpl in (("fn_max_lines", "≤%d 行"), ("nest_max_depth", "≤%d 层"), ("cc_max", "≤%d")):
        assert (tmpl % p1[key]) in body, "文档未写明 %s=%s" % (key, p1[key])
    assert th["例外机制"]["标记"] in body, "例外标记未写进正文"


def test_injection_is_idempotent():
    once = cc.inject("SYS")
    assert cc.inject(once) == once


def test_missing_file_raises_not_silent():
    with pytest.raises(cc.CodeConstitutionError):
        cc.constitution_text(path=os.path.join(ROOT, "_no_such_constitution.md"), use_cache=False)


def test_markerless_file_raises(tmp_path):
    p = tmp_path / "no_marker.md"
    p.write_text("# 没有标记\n", encoding="utf-8")
    with pytest.raises(cc.CodeConstitutionError):
        cc.constitution_text(path=str(p), use_cache=False)


def test_inject_or_mark_leaves_visible_marker():
    marked = cc.inject_or_mark("SYS", path=os.path.join(ROOT, "_no_such_constitution.md"))
    assert "代码宪法未加载" in marked


def test_constitution_size_budget():
    assert len(cc.constitution_text()) <= cc.MAX_CHARS
