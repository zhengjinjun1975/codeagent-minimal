#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_version_consistency.py — 版本口径校验（顶层四处 + 原子三处）。

为什么单独一个文件：这个仓库的版本号是**手写同步**的，没有生成器。
- 顶层四处：`version.py` 的 `__version__`、README 正文的 `vX.Y.Z`、README 徽标、
  CHANGELOG 顶部条目。`version.py` 自称"版本单点"，但另外三处靠人记得改。
- 原子三处：每个原子的 `manifest.json`、`registry.json`、`main.py` 的 `version` 类属性。
  其中 `atomic_base.AtomicAgent.describe()` 上报给用户的正是 `main.py` 那个值。

已经实锤漂过：`guard` / `dep-scan` / `code-fuzz` / `process-sandbox` 四个原子的 `main.py`
版本比 manifest 低一档，用户看到的版本与声明版本不符。所以把口径钉成断言：任一处漂了
CI 直接红，不靠人肉巡检。

纯静态读文件：不导入原子（避免副作用与依赖），不联网、不起服务、不碰临时目录。
"""
import os
import re
import json
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_README_BODY = re.compile(r"\*\*v(\d+\.\d+\.\d+)")
_README_BADGE = re.compile(r"badge/version-(\d+\.\d+\.\d+)-")
_CHANGELOG_TOP = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]", re.M)
_ATOM_VER = re.compile(r"^\s+version\s*=\s*[\"']([^\"']+)[\"']", re.M)


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def _first(pattern, rel):
    m = pattern.search(_read(rel))
    assert m, "%s 里找不到版本号，写法可能被改了" % rel
    return m.group(1)


def top_version():
    """version.py 的 __version__，即本库发布版本的单一来源。"""
    m = re.search(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", _read("version.py"), re.M)
    assert m, "version.py 里找不到 __version__"
    return m.group(1)


def _atom_pairs():
    """返回 [(原子相对路径, manifest name, manifest 版本, main.py 版本)]，无版本记空串。"""
    out = []
    for mp in sorted(glob.glob(os.path.join(ROOT, "agents", "**", "manifest.json"), recursive=True)):
        d = os.path.dirname(mp)
        with open(mp, encoding="utf-8") as f:
            manifest = json.load(f)
        out.append((os.path.relpath(d, ROOT).replace(os.sep, "/"),
                    str(manifest.get("name", "")),
                    str(manifest.get("version", "")),
                    _main_py_version(d)))
    return out


def _main_py_version(atom_dir):
    path = os.path.join(atom_dir, "main.py")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        hit = _ATOM_VER.search(f.read())
    return hit.group(1) if hit else ""


def _registry_versions():
    with open(os.path.join(ROOT, "registry.json"), encoding="utf-8") as f:
        agents = json.load(f).get("agents") or {}
    return {name: str(info.get("version", "")) for name, info in agents.items()}


def test_top_version_is_semver():
    ver = top_version()
    assert _SEMVER.match(ver), "version.py 的版本不是 x.y.z：%r" % (ver,)


def test_readme_body_matches_top():
    want = top_version()
    got = _first(_README_BODY, "README.md")
    assert got == want, "README 正文写 v%s，version.py 是 %s" % (got, want)


def test_readme_badge_matches_top():
    want = top_version()
    got = _first(_README_BADGE, "README.md")
    assert got == want, "README 徽标写 %s，version.py 是 %s" % (got, want)


def test_changelog_top_matches_top():
    want = top_version()
    got = _first(_CHANGELOG_TOP, "CHANGELOG.md")
    assert got == want, "CHANGELOG 顶部条目是 %s，version.py 是 %s" % (got, want)


def test_atom_scan_not_empty():
    """防止 glob 失效让下面的断言空跑成"通过"。"""
    pairs = _atom_pairs()
    assert len(pairs) >= 20, "只扫到 %d 个原子，agents/**/manifest.json 路径可能失效" % len(pairs)


def test_atom_main_py_matches_manifest():
    bad = ["%s: main.py=%s / manifest=%s" % (rel, p or "缺失", m)
           for rel, _, m, p in _atom_pairs() if p != m]
    assert not bad, ("原子 main.py 的 version 与 manifest.json 不一致"
                     "（describe() 会上报错版本）：\n" + "\n".join(bad))


def test_atom_registry_matches_manifest():
    reg = _registry_versions()
    bad = ["%s: registry=%s / manifest=%s" % (name or rel, reg.get(name, "缺项"), m)
           for rel, name, m, _ in _atom_pairs()
           if reg.get(name) != m]
    assert not bad, ("registry.json 与 manifest.json 版本不一致（改完 manifest 需重新生成 registry）：\n"
                     + "\n".join(bad))
