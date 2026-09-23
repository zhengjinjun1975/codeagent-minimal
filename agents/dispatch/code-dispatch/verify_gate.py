"""派单验收门核心：脚本断言 + 退出码，替代模型自评 score。

纯函数，返回普通 dict（不包信封），由 main.py 负责包信封。
只依赖标准库。
"""

import json
import os
import re
import subprocess
import sys

TIMEOUT = 300
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _check(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def _nonempty(path):
    """存在且非空（文件看大小，目录看是否有内容）。"""
    if not os.path.exists(path):
        return False, "不存在"
    if os.path.isdir(path):
        return (len(os.listdir(path)) > 0), "目录为空"
    try:
        return (os.path.getsize(path) > 0), "文件为空"
    except OSError as e:
        return False, str(e)


def _check_exists(target, names):
    missing = []
    for n in names:
        p = n if os.path.isabs(n) else os.path.join(target, n)
        ok, _ = _nonempty(p)
        if not ok:
            missing.append(n)
    detail = "全部存在且非空" if not missing else "缺失或为空: " + ", ".join(missing)
    return _check("exists", not missing, detail)


def _check_no_extra(target, names):
    if not os.path.isdir(target):
        return _check("no_extra_at_root", False, "target 不是目录")
    allowed = set()
    for n in names:
        allowed.add(n.split("/")[0].split(os.sep)[0])
    actual = set(os.listdir(target))
    extra = sorted(actual - allowed)
    detail = "顶层无额外文件" if not extra else "额外文件: " + ", ".join(extra)
    return _check("no_extra_at_root", not extra, detail)


def _extract_names(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return set(_NAME_RE.findall(f.read()))
    except OSError:
        return set()


def _check_namediff(names, names_source):
    if not names_source:
        return _check("namediff", False, "缺少 names_source")
    actual = _extract_names(names_source)
    missing = sorted(set(names) - actual)
    detail = "名字齐全" if not missing else "缺失名字: " + ", ".join(missing)
    return _check("namediff", not missing, detail)


def _iter_prod_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d != "__pycache__" and d != "tests" and not d.startswith("test_")
        ]
        for fn in filenames:
            if fn.endswith(".py") and not fn.startswith("test_"):
                yield os.path.join(dirpath, fn)


def _check_import(target, modules):
    root = target if os.path.isdir(target) else os.path.dirname(target)
    # 向上找到工程根：含 .git 或 setup.py 或 pyproject.toml
    cur = os.path.abspath(root)
    while True:
        if any(os.path.exists(os.path.join(cur, m))
               for m in (".git", "setup.py", "pyproject.toml")):
            root = cur
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    found = set()
    for path in _iter_prod_py(root):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for mod in modules:
            if mod in found:
                continue
            pat = r"(^|\n)\s*(import\s+%s\b|from\s+%s\b)" % (re.escape(mod), re.escape(mod))
            if re.search(pat, text):
                found.add(mod)
    missing = sorted(set(modules) - found)
    detail = "生产代码已引用" if not missing else "未被生产代码引用: " + ", ".join(missing)
    return _check("imported_by_prod", not missing, detail)


def _run_script(path):
    """返回 (exit_code, stdout_tail)。异常/超时 → (-1, 错误信息)。"""
    cmd = [sys.executable, path] if path.endswith(".py") else [path]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
        out = (r.stdout or b"").decode("utf-8", "replace")
        return r.returncode, out[-500:]
    except subprocess.TimeoutExpired:
        return -1, "超时(%ss)" % TIMEOUT
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def _check_script(path):
    code, tail = _run_script(path)
    detail = "退出码=%d; stdout末: %s" % (code, tail)
    return _check("assert_script", code == 0, detail)


def verify_artifact(target, expect=None, mode="auto"):
    """验收门核心。passed 只由 checks 决定，不读任何模型自评 score。"""
    expect = expect or {}
    checks = []

    def want(key, tag):
        return mode == "auto" and key in expect or mode == tag

    if want("exists", "exists"):
        checks.append(_check_exists(target, expect.get("exists", [])))
    if want("no_extra_at_root", "exists"):
        checks.append(_check_no_extra(target, expect.get("exists", [])))
    if want("names_source", "namediff"):
        checks.append(_check_namediff(expect.get("names", []), expect.get("names_source")))
    if want("imported_by_prod", "import"):
        checks.append(_check_import(target, expect.get("imported_by_prod", [])))
    if want("assert_script", "script"):
        checks.append(_check_script(expect.get("assert_script", "")))

    passed = all(c["ok"] for c in checks) and bool(checks)
    return {"passed": passed, "checks": checks, "exit_code": 0 if passed else 1}


def assert_exit(script_path):
    """跑脚本，ok = 退出码为 0。"""
    code, tail = _run_script(script_path)
    return {"exit_code": code, "stdout_tail": tail, "ok": code == 0}