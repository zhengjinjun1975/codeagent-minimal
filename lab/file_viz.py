#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""file_viz.py — 代码浏览/编辑（FR1/FR2，纯 stdlib，加壳不改核心）。

- 文件树：白名单式 walk（跳过隐藏/缓存/数据目录），大目录按需分层
- 文件查看：只读视图 + AST 方法级清单（函数/类 + 行号）；大文件分块读
- 保存：白名单 + py_compile 语法校验（真实编译）+ 原子写（tmp + os.replace）
- 编辑审计全部入 edit_log；保存后发事件 file.saved（前端联动影响分析）
- 死代码/文档新鲜度徽标：调根层算法（子进程），失败降级"?"不吞错
"""
import ast
import os
import re
import stat
import subprocess
import sys
import time

_SKIP_DIRS = {".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv", "venv",
              "lab_data", ".idea", ".vscode", ".pytest_cache", ".mypy_cache"}
_TEXT_EXT = (".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini",
             ".cfg", ".csv", ".html", ".css", ".js", ".xml", ".sh")
_SRC_EXT = (".py",)          # 编辑白名单：源码文件（可扩展）
_BIG_FILE = 500 * 1024       # >500KB 大文件分块


# ── 树 ───────────────────────────────────────
def tree(root, rel_dir="", recursive=False, max_files=2000, badges=False, codeagent_root=None):
    """返回 {dirs:[{path,name}], files:[{path,name,size,mtime}], truncated}。"""
    base = os.path.realpath(root)
    folder = os.path.realpath(os.path.join(base, rel_dir)) if rel_dir else base
    if not os.path.isdir(folder) or os.path.commonpath([folder, base]) != base:
        return {"dirs": [], "files": [], "error": "目录越界或不存在",
                "path": rel_dir}
    dirs, files = [], []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError as e:
        return {"dirs": [], "files": [], "error": str(e), "path": rel_dir}
    for e in entries:
        rel = os.path.relpath(e.path, base).replace(os.sep, "/")
        if e.is_dir():
            if e.name in _SKIP_DIRS or e.name.startswith("."):
                continue
            dirs.append({"path": rel, "name": e.name})
        elif e.is_file():
            ext = os.path.splitext(e.name)[1].lower()
            if ext not in _TEXT_EXT:
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            files.append({"path": rel, "name": e.name, "size": st.st_size,
                          "mtime": st.st_mtime})
    files.sort(key=lambda f: f["name"].lower())
    truncated = len(files) > max_files
    if truncated:
        files = files[:max_files]
    if badges:
        _attach_badges(files, base, codeagent_root)
    # P2-9: 返回绝对根路径，前端树根标签显示绝对目标仓库而非相对路径
    return {"dirs": dirs, "files": files, "truncated": truncated, "path": rel_dir,
            "root": base}


def _attach_badges(files, base, codeagent_root):
    """文档新鲜度/可编辑徽标：只对 .py 做低成本真实信号。

    P1-7 契约修复：徽标统一为对象 {kind, count, label}（原返回字符串数组导致前端
    filter(kind) 恒空）。信号：
      - syntax   : py_compile 是否可编译
      - ok       : 可编译且无 TODO
      - todo     : 含 TODO/FIXME/HACK
      - doc      : 无模块级 docstring（文档新鲜度低成本信号）
    """
    for f in files:
        if not f["name"].endswith(".py"):
            continue
        absf = os.path.join(base, f["path"].replace("/", os.sep))
        badges = []
        head = ""
        try:
            with open(absf, encoding="utf-8", errors="ignore") as fh:
                head = fh.read(200_000)
            if re.search(r"TODO|FIXME|HACK", head):
                badges.append({"kind": "todo", "count": 1, "label": "含TODO"})
            # 模块级 docstring 缺失 → 文档新鲜度低
            if not re.match(r"\s*(?:'''|\"\"\")", head):
                badges.append({"kind": "doc", "count": 1, "label": "缺模块文档"})
        except OSError:
            pass
        if _quick_compile(absf):
            if not any(b["kind"] == "todo" for b in badges):
                badges.append({"kind": "ok", "count": 1, "label": "可编译"})
        else:
            badges.append({"kind": "syntax", "count": 1, "label": "语法错误"})
        f["badges"] = badges


def _quick_compile(absf, timeout=20):
    try:
        r = subprocess.run([sys.executable, "-m", "py_compile", absf],
                           capture_output=True, timeout=timeout)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return None


# ── 文件读取 ─────────────────────────────────
def read_file(root, rel, view="code", codeagent_root=None, timeout=120):
    """返回 {path, rel, size, content, lines, methods[], dead[], big}。

    view: code|methods|deadcode —— methods/deadcode 需要 .py + AST/根层算法。
    """
    base = os.path.realpath(root)
    real = os.path.realpath(os.path.join(base, rel))
    if os.path.commonpath([real, base]) != base or not os.path.isfile(real):
        return {"ok": False, "error": "路径越界或不存在"}
    size = os.path.getsize(real)
    big = size > _BIG_FILE
    content = ""
    if not big:
        try:
            with open(real, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as e:
            return {"ok": False, "error": str(e)}
    else:
        # 大文件：读头部 + 尾部（按 seek）
        try:
            with open(real, encoding="utf-8", errors="replace") as fh:
                content = fh.read(256 * 1024)
                fh.seek(max(0, size - 256 * 1024))
                content += "\n...<<文件过大，以下为尾部>>...\n" + fh.read()
        except OSError as e:
            return {"ok": False, "error": str(e)}
    out = {"ok": True, "path": real, "rel": rel.replace(os.sep, "/"),
           "size": size, "big": big, "content": content,
           "lines": content.split("\n"), "methods": [], "dead": []}
    if rel.endswith(".py"):
        out["methods"] = ast_methods(content)
        if view == "deadcode" and codeagent_root:
            out["dead"] = deadcode(real, codeagent_root) or []
        elif view == "deadcode":
            out["dead"] = []
    return out


def ast_methods(content):
    """AST 方法级视图：函数/类清单（name/line/args/doc）。"""
    methods = []
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return methods  # 语法错误文件无法 AST（前端会显示编译报错徽标）
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            doc = ast.get_docstring(node) or ""
            methods.append({"name": node.name, "line": node.lineno, "kind": "function",
                            "args": args, "doc": doc.split("\n")[0] if doc else ""})
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            methods.append({"name": node.name, "line": node.lineno, "kind": "class",
                            "args": [], "doc": doc.split("\n")[0] if doc else ""})
    methods.sort(key=lambda m: m["line"])
    return methods


def deadcode(absf, codeagent_root, timeout=120):
    """死代码检测（复用根层 deadcode.py，子进程，失败降级）。"""
    dl = os.path.join(codeagent_root, "deadcode.py")
    if not os.path.isfile(dl):
        return []
    try:
        r = subprocess.run([sys.executable, dl, os.path.dirname(absf)],
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        if not out:
            return []
        import json as _json
        try:
            data = _json.loads(out)
        except Exception:  # noqa: BLE001
            return [{"raw": out[:2000], "source": "deadcode.py"}]
        return _norm_dead(data)
    except Exception as e:  # noqa: BLE001
        return [{"error": f"死代码分析失败: {e}"}]


def _norm_dead(data):
    """把 deadcode.py 输出归一为 [{'name','line','reason'}]（容忍多种形态）。"""
    out = []
    if isinstance(data, dict):
        items = (data.get("dead") or data.get("unused") or data.get("symbols")
                 or data.get("items") or [])
    elif isinstance(data, list):
        items = data
    else:
        items = []
    for it in items:
        if isinstance(it, dict):
            out.append({"name": it.get("name") or it.get("symbol") or it.get("func"),
                        "line": it.get("line") or it.get("lineno") or "",
                        "reason": it.get("reason") or it.get("why") or ""})
        elif isinstance(it, str):
            out.append({"name": it, "line": "", "reason": ""})
    return out[:200]


def _first_error(compile_out):
    """从 py_compile 输出提取首个错误行（File ... line N / IndentationError...）。"""
    m = re.search(r"line (\d+)", compile_out or "")
    line = int(m.group(1)) if m else 0
    msgs = []
    for ln in (compile_out or "").splitlines():
        if "Error" in ln or "error" in ln:
            msgs.append(ln.strip())
    return {"line": line, "detail": (msgs[0] if msgs else (compile_out or "").strip()[:500])}


# ── 保存（白名单 + py_compile + 原子写）───────
def save_file(root, rel, content, db=None, events=None):
    """保存源码文件。\n\n    校验：路径白名单（config.resolve）+ 仅 .py + 语法编译（py_compile 真实编译）。\n    写：tmp 文件 + os.replace（原子写）。审计入 edit_log + 事件 file.saved。\n    """
    if not rel.endswith(_SRC_EXT):
        return {"ok": False, "error": f"仅允许编辑源码文件({','.join(_SRC_EXT)})"}
    base = os.path.realpath(root)
    real = os.path.realpath(os.path.join(base, rel))
    if os.path.commonpath([real, base]) != base or not os.path.isfile(real):
        return {"ok": False, "error": "路径越界或目标不存在"}
    if not isinstance(content, str):
        return {"ok": False, "error": "content 必须为字符串"}
    # P1-5 大文件安全护栏：>500KB 后端只返头尾，前端内容是截断占位，整体保存会写坏文件。
    # 因此对大文件强制只读——后端在保存层再兜底拒绝，杜绝数据破坏。
    if os.path.getsize(real) > _BIG_FILE:
        return {"ok": False, "saved": False, "compile_ok": True,
                "error": "大文件(>500KB)为只读：为防截断内容覆盖写坏，请在外部编辑器修改"}
    # 1) 语法编译校验（真实）
    import tempfile
    fd, tmpf = tempfile.mkstemp(suffix=".py", prefix=".lab_compile_",
                                dir=os.path.dirname(real))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        r = subprocess.run([sys.executable, "-m", "py_compile", tmpf],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
    finally:
        try:
            os.remove(tmpf)
        except OSError:
            pass
    if r.returncode != 0:
        err = _first_error(r.stderr or r.stdout)
        ok = False
        detail = f"语法校验失败: line {err['line']}: {err['detail'][:300]}"
        if db:
            db.add_edit_log(rel, "save", len(content.encode("utf-8")), False, detail)
        if events:
            events.emit("file.save_failed", {"path": rel, "error": detail})
        return {"ok": False, "saved": False, "compile_ok": False,
                "first_error": err, "error": detail}
    # 2) 原子写：tmp + os.replace
    tmp = real + f".lab_tmp.{time.time_ns()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, real)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        ok = False
        detail = f"写入失败: {e}"
        if db:
            db.add_edit_log(rel, "save", 0, False, detail)
        if events:
            events.emit("file.save_failed", {"path": rel, "error": detail})
        return {"ok": False, "saved": False, "compile_ok": True, "error": detail}
    ok = True
    if db:
        db.add_edit_log(rel, "save", len(content.encode("utf-8")), True, "py_compile 通过")
    if events:
        events.emit("file.saved", {"path": rel, "size": len(content.encode("utf-8"))})
    return {"ok": True, "saved": True, "compile_ok": True, "path": rel,
            "size": len(content.encode("utf-8"))}


def file_stats(absf):
    try:
        st = os.stat(absf)
        return {"size": st.st_size, "mtime": st.st_mtime}
    except OSError:
        return {}