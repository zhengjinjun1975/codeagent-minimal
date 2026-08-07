#!/usr/bin/env python3
"""test_review.py — codeagent-minimal 审查 + 测试 harness 的自测（pytest）"""
import os
import sys
import pathlib

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import review
import test_harness as th

BAD = '''import os
api_key = "sk-1234567890abcdef"
def run_cmd(u):
    os.system("ls " + u)
def unsafe_eval(c):
    return eval(c)
def divide(a, b):
    return a / b
'''

GOOD = '''def healthy(x):
    if x is None:
        return "empty"
    return str(x)
def add(a, b):
    return a + b
'''


def _tmp(name, content):
    p = HERE / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_review_catches_security():
    f = _tmp("_t_bad.py", BAD)
    r = review.review_file(f, use_llm=False)
    titles = {i["title"] for i in r["issues"]}
    assert any("eval" in t or "exec" in t for t in titles)
    assert any("硬编码" in t for t in titles)
    assert any("命令" in t for t in titles)
    os.remove(f)


def test_review_clean_file_high_score():
    f = _tmp("_t_good.py", GOOD)
    r = review.review_file(f, use_llm=False)
    assert r["static_score"] >= 90
    os.remove(f)


def test_harness_boundary_finds_edges():
    f = _tmp("_t_parse.py", "def parse(s):\n    return int(s)\n")
    rep = th.run_all(f, str(HERE), do_stability=False)
    assert rep["smoke"]["ok"]
    assert not rep["boundary"]["ok"]  # int("")/int(" ") 抛 ValueError，边界未处理
    os.remove(f)


def test_harness_stability():
    f = _tmp("_t_stab.py", "def ok():\n    return 42\n")
    rep = th.run_all(f, str(HERE), do_boundary=False, do_mutation=False)
    assert rep["smoke"]["ok"]
    assert rep["stability"]["ok"]  # 无崩溃无挂起
    os.remove(f)


def test_six_static_dimensions():
    # _static_analyze 返回含 bugs + architecture 维度
    r = review._static_analyze(GOOD)
    assert "bugs" in r and "architecture" in r
    assert "syntax" in r and "security" in r and "complexity" in r
