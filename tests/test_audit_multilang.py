"""三缺陷回归：语言识别 / 空结果判定 / 入口校验。

2026-10-23 实测抓到的三个缺陷，逐条钉在这里：

  · 审一个 15 个 .js 的项目返回 ok=true / count=0 —— 一个文件没审却报「一切正常」
  · 合法 JS 注释被 Python 解析器报成 P0 语法错误
  · 传目录被当文件 open，PermissionError 被执行层包成 ATOM_FAIL

三者的病根是同一句话：遇到自己不认识的东西，默认按成功处理。
本文件把「按语言分流」和「空结果非 ok」两件事钉死，并且**同时**要求
Python 仍做结构级检查 —— 否则最省事的修法会把语法检查整体砍掉。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import audit_scan
import lang_profile
import review

# 一行合法 JS 注释，里面带全角冒号：Python 解析器会报 invalid character
JS_FULLWIDTH = "// 后端选择：改这一个常量\nconst A = 1;\n"

OPTS = {"max_complexity": 10, "strict_undefined": False,
        "semantic": True, "denoise": True}


def test_classify_three_levels():
    assert lang_profile.classify("a.py")["support"] == lang_profile.SUPPORT_NATIVE
    assert lang_profile.classify("a.js")["support"] == lang_profile.SUPPORT_LEXICAL
    assert lang_profile.classify("a.wxml")["support"] == lang_profile.SUPPORT_LEXICAL
    assert lang_profile.classify("a.zzz")["support"] == lang_profile.SUPPORT_UNKNOWN


def test_unknown_ext_has_no_language():
    info = lang_profile.classify("weird.zzz")
    assert info["language"] is None
    assert lang_profile.supported("weird.zzz") is False


def test_empty_coverage_flags_empty():
    cov = lang_profile.coverage([])
    assert cov["empty"] is True and cov["total"] == 0


def test_coverage_groups_by_language():
    cov = lang_profile.coverage(["a.py", "b.js", "c.js"])
    assert cov["native"] == 1 and cov["lexical"] == 2
    assert cov["by_language"]["javascript"] == 2


def test_collect_multilang(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.js").write_text("const x = 1;\n", encoding="utf-8")
    (tmp_path / "c.zzz").write_text("?", encoding="utf-8")
    names = {os.path.basename(f) for f in audit_scan.collect(str(tmp_path))}
    assert names == {"a.py", "b.js"}, "不认识的扩展名不该被收进来"


def test_collect_skips_node_modules(tmp_path):
    sub = tmp_path / "node_modules" / "pkg"
    sub.mkdir(parents=True)
    (sub / "a.js").write_text("const x = 1;\n", encoding="utf-8")
    (tmp_path / "b.js").write_text("const x = 1;\n", encoding="utf-8")
    files = audit_scan.collect(str(tmp_path))
    assert len(files) == 1, "node_modules 属于「不该看」，不是「没看」"


def test_collect_pattern_still_works(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.js").write_text("const x = 1;\n", encoding="utf-8")
    files = audit_scan.collect(str(tmp_path), "*.py")
    assert {os.path.basename(f) for f in files} == {"a.py"}


def test_js_does_not_get_python_syntax_error():
    """缺陷 2：合法 JS 注释不许被判成语法错误。"""
    out = review._analyze_dispatch("a.js", JS_FULLWIDTH, OPTS)
    titles = [i["title"] for i in out["all_issues"]]
    assert not [t for t in titles if "语法错误" in t], titles
    assert out["parser"] is None
    assert out["language"] == "javascript"


def test_js_result_declares_coverage():
    """看不懂就要写清看不懂到什么程度，不能只说「没发现问题」。"""
    out = review._analyze_dispatch("a.js", JS_FULLWIDTH, OPTS)
    assert "lexical" in out["coverage"]
    assert out["syntax"] == []


def test_python_still_checks_syntax():
    """修 JS 不能顺手把 Python 的语法检查砍掉。"""
    out = review._analyze_dispatch("a.py", "def f(:\n    pass\n", OPTS)
    assert [i for i in out["all_issues"] if "语法错误" in i["title"]]


def test_python_unused_import_detected():
    """未使用 import 只有 AST 才查得出来，是 Python 结构检查还在的活证据。"""
    out = review._analyze_dispatch("a.py", "import json\n\n\ndef f():\n    return 1\n", OPTS)
    assert [i for i in out["all_issues"] if "未使用" in i["title"]]


def test_empty_verdict_is_not_ok():
    """缺陷 1：空清单必须非 ok —— 空结果不等于没问题。"""
    env = audit_scan.empty_verdict("/tmp/nowhere", lang_profile.coverage([]))
    assert env["ok"] is False
    assert env["code"] == "NO_FILES"
    assert "不代表没有问题" in env["error"]


def test_read_all_reports_unreadable(tmp_path):
    """读不了的文件要单列 errors，不能静默当成空文件。"""
    missing = str(tmp_path / "nope.py")
    contents, errors = audit_scan.read_all([missing])
    assert missing not in contents
    assert len(errors) == 1 and errors[0]["file"] == missing


def test_exit_code_reflects_failure():
    """ok=false 必须能被机器看见：脚本只看退出码。"""
    import codeagent
    assert codeagent._exit_code({"x": {"ok": False}}) == 1
    assert codeagent._exit_code({"x": {"ok": True}}) == 0
    assert codeagent._exit_code({"status": {"atoms": 41}}) == 0


def test_describe_mentions_lexical():
    cov = lang_profile.coverage(["a.py", "b.js"])
    text = audit_scan.describe(cov)
    assert "结构级" in text and "文本级" in text
