#!/usr/bin/env python3
"""审计扫描层：目录 → 源码文件清单 → 内容 → 覆盖度。

为什么单开这一层
----------------
改动前 review 与 project 各有一套扫描逻辑，行为不一致：

  · review 接受任意单文件（.js 也收），但传目录直接被当文件 open，
    抛 PermissionError，再被执行层包成 ATOM_FAIL
  · project 只发 `*.py`，遇到 JS 项目返回 0 文件却报 ok=true

同一个仓库、两个入口、两种覆盖范围 —— 用户没法判断哪份结论可信。
合并成一层之后，两边结论必然一致，覆盖度也只需算一次。

铁律：清单为空一律不当成「没问题」。空结果由调用方判为非 ok，
`empty_verdict` 就是给这件事准备的统一说法。
"""

import fnmatch
import os

import lang_profile

# 单次最多收集多少文件：防止误传根目录时把整台机器扫一遍
MAX_FILES = 2000


def _wanted(full, name, pattern):
    """这个文件要不要收。给了 pattern 就按 pattern，没给就看语言画像。"""
    if pattern:
        return fnmatch.fnmatch(name, pattern) or name == pattern
    return lang_profile.supported(full)


def collect(root, pattern=None):
    """收集源码文件。

    单文件入参原样返回（不判断它能不能识别，由审查层去说「看不懂」）。
    目录入参按扩展名筛选：给了 pattern 走 pattern，没给就走语言画像。
    """
    root = str(root)
    if os.path.isfile(root):
        return [root]
    out = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in lang_profile.SKIP_DIRS]
        for name in names:
            full = os.path.join(base, name)
            if _wanted(full, name, pattern):
                out.append(full)
    return sorted(out)[:MAX_FILES]


def read_all(files):
    """读成 {路径: 内容}。读不了的单列 errors，不静默当成空文件。"""
    contents, errors = {}, []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                contents[f] = fh.read()
        except OSError as e:
            errors.append({"file": f, "error": "%s: %s" % (type(e).__name__, e)})
    return contents, errors


def coverage(files):
    """覆盖度汇总（语言分组 + 三档支持 + 空标记）。"""
    return lang_profile.coverage(files)


def empty_verdict(root, cov):
    """清单为空时的统一结论：明确说「没审」，绝不说「没问题」。

    返回的是完整信封，能力层可以直接 return，交给 atomic_base 透传。
    """
    return {
        "ok": False,
        "data": {"root": str(root), "coverage": cov, "files": []},
        "error": "没有可审的源码文件（未做任何检查，不代表没有问题）：%s" % root,
        "degraded": False,
        "code": "NO_FILES",
    }


def describe(cov):
    """一句话写清这次到底看了多少、看懂了什么。"""
    if cov.get("empty"):
        return "未审任何文件"
    parts = ["共 %d 个文件" % cov["total"]]
    if cov["native"]:
        parts.append("Python %d（结构级）" % cov["native"])
    if cov["lexical"]:
        langs = "、".join(k for k in cov["by_language"] if k != "python")
        parts.append("文本级 %d（%s）" % (cov["lexical"], langs))
    if cov["unknown"]:
        parts.append("不认识 %d（%s）" % (cov["unknown"], "、".join(cov["unparsed"])))
    return "，".join(parts)
