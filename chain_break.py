#!/usr/bin/env python3
"""chain_break.py — 跨仓库断链检查（P1-6，纯 stdlib 零依赖，数据不出厂）。

在多仓库生态（factory-ontology / sme-decision-ontology / solo-agent-kit /
codeagent-minimal）间做"联动断链"审查：跨仓库 import / 文件路径引用 / 配置路径，
检查被引用的模块或文件是否真实存在，找出"能通但不生效/404/断链"的静默失效点。

能力：
- multi_repo_break_check(repos) → {checks, broken, summary}  多仓库联动断链一键审查
- _scan_import_breaks(repos)  — 跨仓库 import 断链（import 的模块在任仓库都不存在）
- _scan_path_breaks(repos)    — 文件路径引用断链（open/config/加载的路径不存在）

用法：
    import chain_break as cb
    cb.multi_repo_break_check(["/path/repo1", "/path/repo2"])
"""
import os
import re
import ast
import json

# 常见根模块名（忽略 stdlib/第三方，聚焦本项目内的跨仓库引用）
_STDLIB = {"os", "sys", "re", "json", "ast", "io", "csv", "datetime", "time",
           "subprocess", "pathlib", "argparse", "urllib", "hashlib", "base64",
           "typing", "collections", "itertools", "functools", "logging", "math",
           "random", "socket", "ssl", "tempfile", "shutil", "traceback",
           "importlib", "inspect", "queue", "threading", "asyncio", "unittest",
           "pytest", "signal", "textwrap", "string", "copy", "enum", "dataclasses",
           "warnings", "struct", "binascii", "zlib", "gzip", "getpass", "glob",
           "multiprocessing", "concurrent", "http", "email", "sqlite3", "xml",
           "html", "platform", "contextlib", "abc", "operator", "pprint", "types",
           "stat", "sysconfig", "decimal", "fractions", "uuid", "pickle", "marshal",
           "builtins", "codecs", "unicodedata", "array", "calendar", "difflib",
           "errno", "fnmatch", "gettext", "grp", "io", "keyword", "linecache",
           "locale", "mailbox", "mmap", "netrc", "numbers", "optparse", "os.path",
           "pdb", "posixpath", "pwd", "resource", "select", "shelve", "site",
           "smtplib", "statistics", "stringprep", "symbol", "tarfile", "token",
           "tokenize", "tty", "unittest.mock", "urllib.parse", "urllib.request",
           "weakref", "zipfile", "gettext", "runpy", "__future__", "hmac", "shlex",
           "secrets", "ctypes", "threading.local", "xml.etree", "contextvars",
           "dis", "graphlib", "importlib.metadata", "pickletools", "msvcrt", "winreg"}

# 忽略的第三方/知名库（不视为断链）
_THIRD_PARTY = {"numpy", "pandas", "requests", "fastapi", "uvicorn", "flask",
                "django", "pydantic", "yaml", "jieba", "sklearn", "pytest",
                "coverage", "bandit", "ruff", "openai", "starlette", "jinja2",
                "redis", "sqlalchemy", "bs4", "lxml", "dotenv", "torch",
                "plotly", "matplotlib", "flask_cors", "markdown", "celery",
                "psutil", "tqdm", "click", "gunicorn", "motor", "beanie",
                "openpyxl", "pymysql", "psycopg2", "pymongo", "websocket",
                "websockets", "crewai", "langchain", "langchain_core",
                "langchain_community", "langgraph", "httpx", "aiohttp", "scipy",
                "statsmodels", "sympy", "nltk", "spacy", "transformers", "tiktoken",
                "chromadb", "faiss", "pgvector", "llama_index", "qdrant", "sentence_transformers",
                "sqlmodel", "sse_starlette", "python_multipart", "python_jose", "passlib",
                "bcrypt", "jwt", "certifi", "setuptools", "werkzeug", "boto3", "azure",
                "opencc", "zhconv", "pypinyin", "dateutil", "pytz", "PIL", "cv2", "nltk",
                "edge_tts", "sounddevice", "faster_whisper", "rapidocr_onnxruntime",
                "winsdk", "fitz", "pdfplumber", "docx", "paho", "asyncua",
                "stdlib_list", "pyflakes"}


def _py_files(root):
    """收集目录下全部 .py 文件（排除 .venv/node_modules/__pycache__）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".venv", "venv", "node_modules", "__pycache__", ".git")]
        for f in filenames:
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return out


def _scan_import_breaks(repos):
    """跨仓库 import 断链：import 的本地/跨仓库模块在所有仓库都不存在。

    真实文件系统解析（修复误报）：
      - 候选导入根 = 每个仓库根目录 + 所有含 .py 的目录（覆盖 package/子目录兄弟引用）；
      - 模块 a.b.c 解析为 <base>/a/b/c.py 或 <base>/a/b/c/__init__.py（包）命中即存在；
      - __future__/stdlib/已知第三方 一律不算断链。
    """
    # 全仓库所有 .py 绝对路径 + 所有候选导入根目录
    all_py = set()
    base_dirs = []
    for r in repos:
        root_abs = os.path.abspath(r)
        base_dirs.append(root_abs)
        for f in _py_files(r):
            all_py.add(os.path.normpath(os.path.abspath(f)))
            base_dirs.append(os.path.dirname(os.path.abspath(f)))
    base_dirs = sorted(set(base_dirs))

    def _resolve(module_name):
        """判断 import 的模块名能否解析到任一仓库的真实文件（含包目录）。"""
        if not module_name:
            return False
        parts = module_name.split(".")
        for b in base_dirs:
            # 逐级截断父包，最长路径优先：优先精确文件，其次 __init__.py
            for i in range(len(parts), 0, -1):
                sub = parts[:i]
                tail = sub[-1]
                head = sub[:-1]
                pkg = os.path.join(b, *head, tail) if head else os.path.join(b, tail)
                if os.path.join(pkg + ".py") in all_py:
                    return True
                if os.path.join(pkg, "__init__.py") in all_py:
                    return True
        return False

    breaks = []
    checked = 0
    for r in repos:
        for f in _py_files(r):
            try:
                tree = ast.parse(open(f, encoding="utf-8", errors="ignore").read())
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        top = a.name.split(".")[0]
                        if top == "__future__" or top in _STDLIB or top in _THIRD_PARTY:
                            continue
                        if not _resolve(a.name):
                            checked += 1
                            breaks.append({
                                "type": "import", "repo": os.path.basename(r),
                                "file": os.path.relpath(f, r),
                                "line": n.lineno, "module": a.name,
                                "issue": f"import '{a.name}' 在任一仓库都不存在（跨仓库断链）",
                                "severity": "P1",
                            })
                elif isinstance(n, ast.ImportFrom):
                    # 相对导入（from . import x / from .. import y）→ 本包内部引用，非跨仓库断链
                    if n.level and n.level > 0:
                        continue
                    mod = n.module or ""
                    top = mod.split(".")[0]
                    if top == "__future__" or top in _STDLIB or top in _THIRD_PARTY \
                            or top in ("agents", "atoms", "web"):
                        continue
                    if not _resolve(mod):
                        checked += 1
                        breaks.append({
                            "type": "import", "repo": os.path.basename(r),
                            "file": os.path.relpath(f, r),
                            "line": n.lineno, "module": mod,
                            "issue": f"from '{mod}' import 的模块在任一仓库都不存在（跨仓库断链）",
                            "severity": "P1",
                        })
    return breaks, checked


def _scan_path_breaks(repos):
    """文件路径引用断链：open()/config 加载的文件路径在所有仓库不存在。"""
    all_files = set()
    for r in repos:
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames
                           if d not in (".venv", "venv", "node_modules", "__pycache__", ".git")]
            for f in filenames:
                all_files.add(os.path.normpath(os.path.join(dirpath, f)))
    breaks = []
    path_re = re.compile(r"(?:open|load|read_text|read\()\s*\(\s*['\"]([^'\"]+)['\"]")
    for r in repos:
        for f in _py_files(r):
            try:
                content = open(f, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            base = os.path.dirname(f)
            for m in path_re.finditer(content):
                cand = m.group(1)
                # 只查相对路径 / 常见资源后缀，忽略绝对系统路径与 URL
                if cand.startswith(("/", "http", "C:", "file:")):
                    continue
                if cand.endswith((".json", ".csv", ".md", ".txt", ".yaml", ".yml",
                                  ".py", ".html", ".js", ".css", ".tsv", ".db", ".sqlite")):
                    # 尝试多种基准：文件所在目录 / 仓库根 / 直接
                    resolved = None
                    for b in (base, r):
                        p = os.path.normpath(os.path.join(b, cand))
                        if p in all_files:
                            resolved = p
                            break
                    if resolved is None:
                        breaks.append({
                            "type": "path", "repo": os.path.basename(r),
                            "file": os.path.relpath(f, r),
                            "line": content[:content.find(cand)].count("\n") + 1,
                            "path": cand,
                            "issue": f"引用的文件 '{cand}' 在所有仓库都不存在（路径断链）",
                            "severity": "P2",
                        })
    return breaks


def multi_repo_break_check(repos, report=True) -> dict:
    """多仓库联动断链一键审查（P1-6）。

    repos: [目录绝对路径]。返回 {checks, broken, summary}：
      checks:  各检查项结果（import 断链数 / 路径断链数）
      broken:  全部断链明细（type=import/path, repo, file, line, module/path, issue, severity）
      summary: 人类可读汇总
    report=True 时同步打印。
    """
    if not repos:
        return {"ok": True, "checks": [], "broken": [], "summary": "未提供仓库目录"}
    # 统一规范化：目录存在才纳入
    valid = [os.path.abspath(r) for r in repos if os.path.isdir(r)]
    if not valid:
        return {"ok": True, "checks": [], "broken": [],
                "summary": "提供的仓库目录均不存在: " + ",".join(str(r) for r in repos)}
    import_breaks, import_checked = _scan_import_breaks(valid)
    path_breaks = _scan_path_breaks(valid)
    broken = import_breaks + path_breaks
    # 去重（同文件同行同 issue）
    seen, dedup = set(), []
    for b in broken:
        key = (b["repo"], b["file"], b.get("module") or b.get("path"), b["issue"])
        if key not in seen:
            seen.add(key)
            dedup.append(b)
    broken = dedup
    checks = [
        {"check": "跨仓库 import 断链", "found": len(import_breaks),
         "desc": "import/from-import 引用的模块在所有仓库都不存在"},
        {"check": "文件路径引用断链", "found": len(path_breaks),
         "desc": "open/load 引用的文件路径在所有仓库都不存在"},
    ]
    by_tier = {"P0": 0, "P1": 0, "P2": 0}
    for b in broken:
        by_tier[b.get("severity", "P2")] = by_tier.get(b.get("severity", "P2"), 0) + 1
    summary = (f"检查 {len(valid)} 个仓库: {sum(c['found'] for c in checks)} 处断链 "
               f"(P0={by_tier['P0']}/P1={by_tier['P1']}/P2={by_tier['P2']})")
    if report:
        print("══ 跨仓库断链检查 ══")
        for c in checks:
            print(f"  {'✅' if c['found']==0 else '⚠️'} {c['check']}: {c['found']} 处 — {c['desc']}")
        for b in broken:
            loc = b.get("module") or b.get("path")
            print(f"  🔗 {b['severity']} [{b['type']}] {b['repo']}/{b['file']}:{b['line']} "
                  f"{loc} — {b['issue']}")
        print("  " + summary)
    return {"ok": len(broken) == 0, "repos": [os.path.basename(r) for r in valid],
            "checks": checks, "broken": broken, "by_tier": by_tier, "summary": summary}


if __name__ == "__main__":
    import sys
    repos = sys.argv[1:]
    if not repos:
        print("用法: python chain_break.py <repo1> [repo2 ...]")
        sys.exit(0)
    r = multi_repo_break_check(repos)
    sys.exit(0 if r["ok"] else 2)
