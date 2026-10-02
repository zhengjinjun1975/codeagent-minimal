"""guard 原子的 harness 中间件核心：纯函数、纯标准库、无副作用（除 loop_guard 落盘计数）。"""
import json
import os
import re

_PROBE_PATTERNS = [re.compile(r"print\(\s*open\("), re.compile(r"print\(os\.listdir"),
                   re.compile(r"sys\.exit\(print\(")]
_CMD_HINT = re.compile(r"\b(grep|ls |find )")
_CODE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx")
_TS_EXT = (".ts", ".tsx", ".js", ".jsx")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return None


def _is_probe(text):
    if any(p.search(text) for p in _PROBE_PATTERNS):
        return True
    return "print(" in text and bool(_CMD_HINT.search(text))


def _project_root(path):
    """产物所属工程根：向上最多 4 层找含 agents/ 或 .git/ 的目录。"""
    base = d = os.path.dirname(os.path.abspath(path))
    for _ in range(4):
        if os.path.isdir(os.path.join(d, "agents")) or os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return base


def _iter_prod_files(root, exts):
    """遍历工程内生产文件（排除 tests/__pycache__/.git/node_modules 与 test_*）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [x for x in dirnames if x not in ("tests", "__pycache__", ".git", "node_modules")]
        for fn in filenames:
            if not fn.endswith(exts) or fn.lower().startswith("test_") or fn.endswith("_test.py"):
                continue
            yield os.path.join(dirpath, fn)


def _check_exists(artifacts, since=None):
    failed = []
    for a in artifacts:
        try:
            if not os.path.isfile(a) or os.path.getsize(a) <= 0:
                failed.append({"item": "exists", "detail": "产物不存在或为空: %s" % a})
            elif since and os.path.getmtime(a) < float(since):
                failed.append({"item": "mtime", "detail": "产物本次未更新(mtime 早于 since): %s" % a})
        except Exception as e:
            failed.append({"item": "exists", "detail": "产物检查异常 %s: %s" % (a, e)})
    return failed


def _check_not_probe(artifacts):
    failed = []
    for a in artifacts:
        if not a.endswith(_CODE_EXT):
            continue
        text = _read(a)
        if text is not None and _is_probe(text):
            failed.append({"item": "not_probe", "detail": "疑似探测/调试代码: %s" % a})
    return failed


def _check_imported_by_prod(artifacts):
    failed = []
    for a in artifacts:
        if not a.endswith(_CODE_EXT):
            continue
        mod = os.path.splitext(os.path.basename(a))[0]
        if a.endswith(".py"):
            pat = re.compile(r"^\s*(?:import\s+%s\b|from\s+%s\s+import\b)" % (re.escape(mod), re.escape(mod)), re.M)
            exts = (".py",)
        else:
            pat = re.compile(r"(?:from\s+[\"'][^\"']*%s[\"']|require\(\s*[\"'][^\"']*%s[\"'])" % (re.escape(mod), re.escape(mod)))
            exts = _TS_EXT
        found = False
        for f in _iter_prod_files(_project_root(a), exts):
            if os.path.abspath(f) == os.path.abspath(a):
                continue
            text = _read(f)
            if text and pat.search(text):
                found = True
                break
        if not found:
            failed.append({"item": "imported_by_prod", "detail": "未被生产代码引用: %s" % a})
    return failed


def _check_tests_ran(artifacts):
    failed = []
    for a in artifacts:
        if not a.endswith(".log"):
            continue
        if not os.path.isfile(a) or os.path.getsize(a) <= 0:
            failed.append({"item": "tests_ran", "detail": "测试日志不存在或为空: %s" % a})
            continue
        low = (_read(a) or "").lower()
        if "failed" in low or "error" in low or "traceback" in low:
            failed.append({"item": "tests_ran", "detail": "测试日志含失败/错误: %s" % a})
    return failed


def exit_intercept(task, artifacts, checklist=None, since=None):
    """退出拦截：按固定清单核验产物；未通过 → passed=False + next_prompt（供干净重投）。
    since（epoch 秒）给定时，额外要求产物 mtime 不早于它（即"本次真跑过"）。"""
    try:
        artifacts = list(artifacts or [])
        items = checklist if checklist is not None else ["exists", "not_probe", "imported_by_prod", "tests_ran"]
        failed = []
        if "exists" in items:
            failed += _check_exists(artifacts, since)
        if "not_probe" in items:
            failed += _check_not_probe(artifacts)
        if "imported_by_prod" in items:
            failed += _check_imported_by_prod(artifacts)
        if "tests_ran" in items:
            failed += _check_tests_ran(artifacts)
        if not failed:
            next_prompt = ""
        else:
            names = list(dict.fromkeys(f["item"] for f in failed))
            next_prompt = ("上一次产出未通过自检：%s。请针对上述失败项修正后重新产出完整闭合的代码块，"
                           "确保产物文件真实落盘且被生产代码引用。" % "、".join(names))
        return {"passed": not failed, "failed": failed, "next_prompt": next_prompt,
                "evidence": {"artifacts": len(artifacts), "checked": list(items),
                             "mtime_floor": since}}
    except Exception as e:
        return {"passed": False, "failed": [{"item": "internal", "detail": str(e)}],
                "next_prompt": "", "evidence": {"artifacts": len(artifacts or [])}}


def selfcheck(artifacts, spec=None):
    """单次自检（不生成重投提示）。"""
    try:
        artifacts = list(artifacts or [])
        items = []
        for a in artifacts:
            try:
                ok = os.path.isfile(a) and os.path.getsize(a) > 0
            except Exception:
                ok = False
            items.append({"name": "exists:%s" % a, "ok": ok, "detail": "" if ok else "不存在或为空"})
        for a in artifacts:
            if not a.endswith(_CODE_EXT):
                continue
            text = _read(a)
            if text is None:
                continue
            ok = not _is_probe(text)
            items.append({"name": "not_probe:%s" % a, "ok": ok, "detail": "" if ok else "疑似探测/调试代码"})
        return {"passed": all(i["ok"] for i in items) if items else True, "items": items}
    except Exception as e:
        return {"passed": False, "items": [{"name": "internal", "ok": False, "detail": str(e)}]}


def _norm_key(file_path):
    if file_path is None:
        return "__any__"
    p = os.path.normpath(file_path)
    return p[0].lower() + p[1:] if len(p) >= 2 and p[1] == ":" else p


def loop_guard(session_key, file_path=None, bump=False, threshold=3, store_dir=None):
    """按 session 记文件编辑次数，超阈值判定"原地打转"。"""
    try:
        if store_dir is None:
            store_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_loopguard")
        os.makedirs(store_dir, exist_ok=True)
        store_file = os.path.join(store_dir, "%s.json" % session_key)
        data = {}
        if os.path.isfile(store_file):
            try:
                data = json.loads(_read(store_file) or "{}")
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        key = _norm_key(file_path)
        count = int(data.get(key, 0) or 0)
        if bump:
            count += 1
            data[key] = count
            with open(store_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        stalled = count >= threshold
        hint = "同一文件已改 %d 次仍未收敛，考虑换方案或先复现问题。" % count if stalled else ""
        return {"stalled": stalled, "count": count, "hint": hint}
    except Exception:
        return {"stalled": False, "count": 0, "hint": ""}
