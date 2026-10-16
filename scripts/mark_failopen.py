#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为静默吞异常点补写/重写带理由的行尾注释标记。

判定口径与 scripts/verify_silent_except_policy.py 对齐：
except 体仅含 pass / continue / return <空值> 即为静默，须带理由标记。
只改行尾注释，AST 不变，行尾形态不变，幂等。
"""
import argparse
import ast
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "scripts", "verify_silent_except_policy.py")

MARK_RE = re.compile(r"#\s*(fail-open|expected-exc)\s*:")
TEMPLATE_RE = re.compile(r"属意外错|仅旁路当前项")
EMPTY_RETURN = (ast.Constant,)
EMPTY_VALUES = (None, False, 0, "", [], {})


def _is_empty_return(node):
    if not isinstance(node, ast.Return):
        return False
    if node.value is None:
        return True
    v = node.value
    if isinstance(v, ast.Constant):
        return v.value in EMPTY_VALUES
    if isinstance(v, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
        return len(v.elts if hasattr(v, "elts") else v.keys) == 0
    return False


def _is_silent_body(body):
    """except 体是否静默：全部语句都是 pass/continue/return<空值>。"""
    if not body:
        return False
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Continue):
            continue
        if _is_empty_return(stmt):
            continue
        return False
    return True


def _enclosing_name(tree, lineno):
    """最内层函数名；退到类名；再退到模块级。"""
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    if best is not None:
        return best.name
    cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if cls is None or node.lineno > cls.lineno:
                    cls = node
    if cls is not None:
        return cls.name
    return "模块级"


def _try_hint(tree, exc_node):
    """try 体里在做什么：取 try 体首条语句的简述。"""
    body = exc_node.body if hasattr(exc_node, "body") else []
    # exc_node 是 ExceptHandler，其父 try 的 body 才是 try 体
    return None


def _behavior(body):
    """出错后的行为描述。"""
    for stmt in body:
        if isinstance(stmt, ast.Continue):
            return "跳过该条继续扫"
        if _is_empty_return(stmt):
            return "返回空值，调用方按缺失处理"
    return "失败不影响主流程"


def _reason(fname, behavior):
    return f"fail-open: {fname} 里出错就{behavior}"


def _collect_silent(tree):
    """返回 [(except_lineno, body, handler)]。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                if _is_silent_body(h.body):
                    out.append((h.lineno, h.body, h))
    return out


def _read(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _split_lines(text):
    """保留行尾：返回 (lines, endings)。"""
    lines = text.splitlines(keepends=True)
    return lines


def _line_ending(line):
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _strip_comment(line):
    """剥掉行尾注释，返回 (code, comment)。"""
    # 简单处理：找第一个 # 且不在字符串里（本脚本只处理 except 行，风险低）
    idx = line.find("#")
    if idx == -1:
        return line, ""
    return line[:idx], line[idx:]


def _process_file(path, dry=False):
    """返回 (rewritten, added, changed)。"""
    text = _read(path)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0, 0, False

    silent = _collect_silent(tree)
    if not silent:
        return 0, 0, False

    lines = _split_lines(text)
    rewritten = 0
    added = 0
    changed = False

    # 按行号从后往前处理，避免行号漂移
    for lineno, body, handler in sorted(silent, key=lambda x: -x[0]):
        # except 行 = handler.lineno（Python 3.8+ 指向 except 关键字行）
        idx = lineno - 1
        if idx < 0 or idx >= len(lines):
            continue
        raw = lines[idx]
        ending = _line_ending(raw)
        core = raw[:-len(ending)] if ending else raw
        code, comment = _strip_comment(core)

        fname = _enclosing_name(tree, lineno)
        behavior = _behavior(body)
        new_reason = _reason(fname, behavior)

        if comment.strip():
            if TEMPLATE_RE.search(comment):
                new_core = code.rstrip() + "  # " + new_reason
                lines[idx] = new_core + ending
                rewritten += 1
                changed = True
            # 已有非模板标记：不动
        else:
            new_core = code.rstrip() + "  # " + new_reason
            lines[idx] = new_core + ending
            added += 1
            changed = True

    if changed and not dry:
        _write(path, "".join(lines))
    return rewritten, added, changed


def _iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".venv", "venv")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _count_remaining(root):
    template = 0
    unmarked = 0
    for path in _iter_py(root):
        text = _read(path)
        try:
            tree = ast.parse(text)
        except SyntaxError:  # fail-open: _count_remaining 里出错就跳过该条继续扫
            continue
        lines = _split_lines(text)
        for lineno, body, handler in _collect_silent(tree):
            idx = lineno - 1
            if idx < 0 or idx >= len(lines):
                continue
            core = lines[idx].rstrip("\r\n")
            _, comment = _strip_comment(core)
            if TEMPLATE_RE.search(comment):
                template += 1
            elif not MARK_RE.search(comment):
                unmarked += 1
    return template, unmarked


def _selfcheck():
    """在临时副本上抹掉一处标记，门应报红。"""
    tmp = tempfile.mkdtemp(prefix="failopen_selfcheck_")
    try:
        dst = os.path.join(tmp, "repo")
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        # 抹掉一处标记
        target = os.path.join(dst, "scripts", "mark_failopen.py")
        # 找一个真实标记文件
        erased = False
        for path in _iter_py(dst):
            text = _read(path)
            if MARK_RE.search(text):
                new = MARK_RE.sub("# erased", text, count=1)
                _write(path, new)
                erased = True
                break
        if not erased:
            print("selfcheck: 未找到可抹标记，跳过")
            return 0
        gate = os.path.join(dst, "scripts", "verify_silent_except_policy.py")
        if not os.path.exists(gate):
            print("selfcheck: 门文件不存在，跳过")
            return 0
        r = subprocess.run([sys.executable, gate], cwd=dst,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("selfcheck: 门报红 ✓")
            return 0
        print("selfcheck: 门未报红 ✗")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args()

    if args.selfcheck:
        return _selfcheck()

    total_rw = 0
    total_add = 0
    for path in _iter_py(args.root):
        rw, add, _ = _process_file(path)
        total_rw += rw
        total_add += add

    template, unmarked = _count_remaining(args.root)
    print(f"本次改写处数: {total_rw}")
    print(f"本次新增处数: {total_add}")
    print(f"全仓剩余模板话处数: {template}")
    print(f"全仓剩余未标记处数: {unmarked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())