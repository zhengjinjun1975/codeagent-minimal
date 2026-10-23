#!/usr/bin/env python3
"""空结果门：审计工具不许对「什么都没审」报成功。

为什么要有这条门
----------------
2026-10-23 实测撞出三个同源缺陷：

  · 审一个 15 个 .js 的小程序项目 → `ok=true, count=0, files=[]`
    一个文件都没审，却报告「一切正常」
  · 拿 Python 解析器读 .js，把一行合法注释报成 P0「语法错误」
  · 传目录被当文件 open，PermissionError 被包成 ATOM_FAIL

病根是同一句话：**遇到自己不认识的东西，默认按成功处理。**
仓库原有 8 道门全在检查代码结构，没有一道检查工具自己的输出是不是空的 ——
所以 `count=0` 能一路报绿到用户手上。

本门现场造最小 fixtures，钉住六件事：
  A 空目录            → 必须 ok=false 且 code=NO_FILES
  B 纯 JS 项目        → 必须 ok=true 且 count>0，coverage 要标出 lexical
  C JS 文件           → 不许出现「语法错误」类 issue（不能拿 Python 尺子量 JS）
  D 目录入参 review   → 不许 ATOM_FAIL / PermissionError
  E 路径不存在        → 必须 code=BAD_PATH
  F Python 文件       → 仍必须有 AST 级检查（未使用 import 要报、语法错误要报）

F 是防回退的关键：修 A–E 最省事的错法是「干脆所有文件都不做语法检查」，
那样本门会全绿，而 Python 的结构检查已经悄悄废掉。所以 F 必须与 C 同时成立：
**对 JS 不判语法，对 Python 必须判语法。**
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # fail-open: 输出编码不可调时不影响检查结论
    pass

# 一行合法 JS 注释里带全角冒号 —— 改动前它会被 ast.parse 报成 P0 语法错误
JS_OK = "// 后端选择：改这一个常量就能切换\nconst HOST = 'https://example.com';\nfunction ping() { return 1; }\n"
# 有未使用 import：只有 AST 检查才能报出来
PY_UNUSED = "import json\n\n\ndef f():\n    return 1\n"
# 真语法错误：必须被报出来，否则说明 Python 也不判语法了
PY_BROKEN = "def f(:\n    pass\n"


def _mk(base, name, tree):
    """在 base/name 下按 {相对路径: 内容} 造文件，返回该目录。"""
    root = Path(base) / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return str(root)


def _expect(fails, name, ok, detail):
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else " —— " + detail))
    if not ok:
        fails.append(name)


def _issues_of(envelope):
    data = envelope.get("data") or {}
    return data.get("issues") or []


def check_empty_dir(ca, base, fails):
    root = _mk(base, "empty", {})
    r = ca.project(root)
    ok = (not r.get("ok")) and r.get("code") == "NO_FILES"
    _expect(fails, "A 空目录必须非 ok/NO_FILES", ok,
            "实得 ok=%s code=%s" % (r.get("ok"), r.get("code")))


def check_js_project(ca, base, fails):
    root = _mk(base, "jsproj", {"app.js": JS_OK, "lib/helper.js": JS_OK,
                                "app.json": "{\"pages\": []}"})
    r = ca.project(root)
    data = r.get("data") or {}
    cov = data.get("coverage") or {}
    ok = bool(r.get("ok")) and (data.get("count") or 0) > 0 and (cov.get("lexical") or 0) > 0
    _expect(fails, "B 纯 JS 项目要真审出文件", ok,
            "ok=%s count=%s lexical=%s" % (r.get("ok"), data.get("count"), cov.get("lexical")))


def check_js_no_syntax(ca, base, fails):
    root = _mk(base, "jsone", {"a.js": JS_OK})
    r = ca.review(os.path.join(root, "a.js"))
    bad = [i for i in _issues_of(r) if "语法错误" in (i.get("title") or "")]
    _expect(fails, "C JS 不套 Python 语法检查", not bad,
            "JS 被报语法错误 %d 条：%s" % (len(bad), (bad[0].get("title") if bad else "")))


def check_dir_input(ca, base, fails):
    root = _mk(base, "dirin", {"sub/a.js": JS_OK, "sub/b.js": JS_OK})
    r = ca.review(os.path.join(root, "sub"))
    err = str(r.get("error") or "")
    bad = r.get("code") == "ATOM_FAIL" or "PermissionError" in err
    ok = (not bad) and (r.get("ok") is True)
    _expect(fails, "D 目录入参要能展开", ok,
            "ok=%s code=%s error=%s" % (r.get("ok"), r.get("code"), err[:60]))


def check_bad_path(ca, base, fails):
    r = ca.review(os.path.join(base, "definitely_not_here.js"))
    ok = (not r.get("ok")) and r.get("code") == "BAD_PATH"
    _expect(fails, "E 路径不存在要报 BAD_PATH", ok,
            "实得 ok=%s code=%s" % (r.get("ok"), r.get("code")))


def check_python_still_parsed(ca, base, fails):
    unused = _mk(base, "pyunused", {"u.py": PY_UNUSED})
    r1 = ca.review(os.path.join(unused, "u.py"))
    hit1 = [i for i in _issues_of(r1) if "未使用" in (i.get("title") or "")]
    _expect(fails, "F-1 Python 仍做 AST 检查（未使用 import）", bool(hit1),
            "没报出未使用 import ⇒ AST 检查被跳过")
    broken = _mk(base, "pybroken", {"bad.py": PY_BROKEN})
    r2 = ca.review(os.path.join(broken, "bad.py"))
    hit2 = [i for i in _issues_of(r2) if "语法错误" in (i.get("title") or "")]
    _expect(fails, "F-2 Python 语法错误仍能报", bool(hit2),
            "真语法错误没报 ⇒ Python 语法检查也没了")


CHECKS = (check_empty_dir, check_js_project, check_js_no_syntax,
          check_dir_input, check_bad_path, check_python_still_parsed)


def main():
    ap = argparse.ArgumentParser(description="空结果门：审计工具不许对空结果报成功")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    import codeagent
    ca = codeagent.CodeAgent()
    fails = []
    with tempfile.TemporaryDirectory(prefix="audit_gate_") as base:
        for fn in CHECKS:
            fn(ca, base, fails)
    if args.json:
        import json
        print(json.dumps({"fails": fails, "checks": len(CHECKS)}, ensure_ascii=False))
    print("检查 %d 项，失败 %d 项" % (len(CHECKS), len(fails)))
    code = 1 if fails else 0
    print("GATE_EXIT=%d" % code)
    return code


if __name__ == "__main__":
    sys.exit(main())
