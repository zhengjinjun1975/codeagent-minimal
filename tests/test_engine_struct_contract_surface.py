# -*- coding: utf-8 -*-
"""结构契约必须对外暴露 check_source（引擎按源码校验时调它）。

回归背景：开源副本的 check_file 曾把检查逻辑内联，没有 check_source，
而 code_agent_engine.py 有两处 `_esc.check_source(...)` 调用 —— 那两条路径
在真实跑到时是 AttributeError，被 fail-open 吞掉，表现为「结构契约门静默不生效」。
契约 schema：{"functions": [{"name": <函数名>, "params": [<形参名>...]}]}
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine_struct_contract as esc

CONTRACT = {"functions": [{"name": "f", "params": ["a", "b"]}]}
GOOD = "def f(a, b):\n    return a\n"


def test_check_source_is_exposed_and_passes_on_contract_satisfying_source():
    assert hasattr(esc, "check_source"), "引擎依赖 esc.check_source，缺了这条路径就是静默失效"
    r = esc.check_source(GOOD, CONTRACT)
    assert r["passed"] is True, r


def test_check_source_reports_missing_function():
    r = esc.check_source("def g():\n    return 1\n", CONTRACT)
    assert r["passed"] is False
    assert r["missing"] == ["f"], r


def test_check_source_reports_param_mismatch():
    r = esc.check_source("def f(a):\n    return a\n", CONTRACT)
    assert r["passed"] is False
    assert r["param_mismatch"], r


def test_check_file_delegates_to_check_source(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(GOOD, encoding="utf-8")
    assert esc.check_file(str(p), CONTRACT) == esc.check_source(GOOD, CONTRACT)
