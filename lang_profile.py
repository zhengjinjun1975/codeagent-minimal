#!/usr/bin/env python3
"""语言画像：判断一个文件是什么语言、本工具对它能看懂多少。

为什么需要这个模块
------------------
改动前 review / project 两层默认「所有文件都是 Python」：扫目录用 `*.py`，
分析内容用 `ast.parse`。2026-10-23 实测撞出两个后果：

  1. 审一个 15 个 .js 的小程序项目，返回 ok=true / count=0 —— 一个文件都没审，
     却报告「一切正常」。工厂拿着这份报告放行，等于工具帮着一起瞒。
  2. 单独审 .js 文件，`ast.parse` 报 `invalid character '：'`（正文是一行合法
     JS 注释），还被标成 P0「安全/断链/高影响」—— 把「我没有这个语言的解析器」
     说成「你的代码有错」。

三档支持度，与契约总账里的「判不了」同一口径：看不懂就明说，不冒充通过。

  native   —— 有真解析器，能做结构级检查（Python）
  lexical  —— 无解析器，只做语言无关的文本级检查（安全/网络/通用），
              并且必须在结论里声明「未做结构分析」
  unknown  —— 扩展名不认识，不检查，显式计入「没看」
"""

import os

SUPPORT_NATIVE = "native"
SUPPORT_LEXICAL = "lexical"
SUPPORT_UNKNOWN = "unknown"

# 有真解析器的语言：只有 Python（本工具的 AST 检查全部基于 ast 模块）
NATIVE_EXT = {".py", ".pyw"}

# 认识但无解析器：只做文本级检查
LEXICAL_LANG = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".vue": "vue", ".svelte": "svelte",
    ".java": "java", ".kt": "kotlin", ".scala": "scala", ".groovy": "groovy",
    ".go": "go", ".rs": "rust", ".swift": "swift", ".dart": "dart",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".php": "php", ".rb": "ruby", ".lua": "lua", ".pl": "perl",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ps1": "powershell",
    ".sql": "sql",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss", ".less": "less",
    ".wxml": "wxml", ".wxss": "wxss", ".axml": "axml", ".acss": "acss",
    ".xml": "xml", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".conf": "conf",
}

# 目录名级排除：这些不是「没看」，是「不该看」，不参与覆盖度统计
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist",
             "build", ".next", ".nuxt", "coverage", ".pytest_cache", ".idea",
             ".vscode", "vendor", "target", ".mypy_cache"}


def extension(path):
    """取小写扩展名。无扩展名返回空串（不返回 None，调用方不必再判空）。"""
    return os.path.splitext(str(path))[1].lower()


def classify(path):
    """返回 {path, ext, language, support}。只看扩展名，不读文件内容。"""
    ext = extension(path)
    if ext in NATIVE_EXT:
        return {"path": str(path), "ext": ext, "language": "python",
                "support": SUPPORT_NATIVE}
    lang = LEXICAL_LANG.get(ext)
    if lang:
        return {"path": str(path), "ext": ext, "language": lang,
                "support": SUPPORT_LEXICAL}
    return {"path": str(path), "ext": ext or "(无扩展名)", "language": None,
            "support": SUPPORT_UNKNOWN}


def supported(path):
    """扩展名是否可识别。目录永远返回 False —— 它要由调用方展开。"""
    if os.path.isdir(str(path)):
        return False
    return classify(path)["support"] != SUPPORT_UNKNOWN


def is_native(path):
    """能不能做结构级（AST）分析。"""
    return classify(path)["support"] == SUPPORT_NATIVE


def split(paths):
    """把文件清单分成 native / lexical / unknown 三组。"""
    out = {SUPPORT_NATIVE: [], SUPPORT_LEXICAL: [], SUPPORT_UNKNOWN: []}
    for p in paths:
        out[classify(p)["support"]].append(p)
    return out


def coverage(paths):
    """汇总覆盖度：总数、三档各几个、按语言分组、未知扩展名样例。

    `empty=True` 是本模块存在的主要理由之一：调用方必须据此把结果判为
    「没审出东西」而不是「没问题」。
    """
    paths = list(paths)
    groups = split(paths)
    by_lang = {}
    for p in paths:
        info = classify(p)
        key = info["language"] or info["ext"]
        by_lang[key] = by_lang.get(key, 0) + 1
    return {
        "total": len(paths),
        "native": len(groups[SUPPORT_NATIVE]),
        "lexical": len(groups[SUPPORT_LEXICAL]),
        "unknown": len(groups[SUPPORT_UNKNOWN]),
        "by_language": by_lang,
        "empty": not paths,
        "unparsed": [os.path.basename(p) for p in groups[SUPPORT_UNKNOWN]][:5],
    }


def note_for(support):
    """给结论用的说明句：把「你看懂了多少」写进结果，不让调用方自己猜。"""
    if support == SUPPORT_NATIVE:
        return "native：有 Python 解析器，含结构级检查"
    if support == SUPPORT_LEXICAL:
        return "lexical：无该语言解析器，只做文本级检查（安全/网络/通用），未做结构与复杂度分析"
    return "unknown：扩展名不认识，未做任何检查"
