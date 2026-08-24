#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch-fs — 通用工具原子(省重复 P1-3): 批量文件操作(包装 pycommon + scripts/fs)
提供: fs.list / fs.hash / fs.dedup / fs.rename / fs.organize

来源: E:/scripts/fs/20260824-batch-fs.py (高频文件操作脚本)
加壳不改核心: ACTIONS + run() 保留原接口; 新增 AtomicAgent 子类使 loader 可加载复用。
"""
import sys, os, shutil

sys.path.insert(0, r"E:/scripts/lib")
from pycommon import list_files, file_hash, ensure_dir, safe_filename

try:
    from atomic_base import AtomicAgent
except Exception:
    AtomicAgent = None  # 独立运行(非 loader)时无基类, 仅 run() 可用

ACTIONS = {}


def _dedup(d, dry=True):
    seen, dups = {}, []
    for f in list_files(d, recursive=True, full_path=True):
        h = file_hash(f)
        if h in seen:
            dups.append((seen[h], f))
        else:
            seen[h] = f
    return {"duplicates": len(dups), "pairs": dups[:20]}


def _rename(d, prefix="", suffix="", dry=True):
    """批量重命名: 加前缀/后缀(不改扩展名以外结构)。dry=True 只预览。"""
    changed = 0
    plan = []
    for f in list_files(d, recursive=False, full_path=True):
        base = os.path.basename(f)
        new = prefix + safe_filename(base) + suffix
        if new == base:
            continue
        dest = os.path.join(d, new)
        plan.append((f, dest))
        if not dry:
            os.replace(f, dest)
        changed += 1
    return {"changed": changed, "plan": [(os.path.basename(a), os.path.basename(b)) for a, b in plan[:50]]}


def _organize(d, dry=True):
    """按扩展名把文件归入子目录(如 .md → d/md/)。dry=True 只预览。"""
    moved = 0
    plan = []
    for f in list_files(d, recursive=False, full_path=True):
        if os.path.isdir(f):
            continue
        ext = os.path.splitext(f)[1].lstrip(".") or "noext"
        sub = os.path.join(d, ext)
        dest = os.path.join(sub, os.path.basename(f))
        plan.append((f, dest))
        if not dry:
            ensure_dir(sub)
            shutil.move(f, dest)
        moved += 1
    return {"moved": moved, "plan": [(os.path.basename(a), os.path.basename(b)) for a, b in plan[:50]]}


ACTIONS["fs.list"] = lambda d, suffix=None, recursive=True, **k: list_files(d, suffix=suffix, recursive=recursive)
ACTIONS["fs.hash"] = lambda p, **k: file_hash(p)
ACTIONS["fs.dedup"] = lambda d, **k: _dedup(d, dry=k.get("dry", True))
ACTIONS["fs.rename"] = lambda d, prefix="", suffix="", **k: _rename(d, prefix=prefix, suffix=suffix, dry=k.get("dry", True))
ACTIONS["fs.organize"] = lambda d, **k: _organize(d, dry=k.get("dry", True))


def run(action, **kwargs):
    fn = ACTIONS.get(action)
    if not fn:
        return {"error": f"unknown action: {action}", "available": sorted(ACTIONS)}
    try:
        return {"action": action, "result": fn(**kwargs)}
    except Exception as e:
        return {"action": action, "error": str(e)}


# ---------- AtomicAgent 壳(loader 可加载复用) ----------
if AtomicAgent is not None:
    class BatchFsAgent(AtomicAgent):
        name = "batch-fs"
        version = "0.1.0"
        domain = "tools"
        description = ("通用工具原子(省重复 P1-3): 批量文件操作(列文件/哈希/去重/重命名/按扩展名整理), "
                       "包装 pycommon + E:/scripts/fs。纯 stdlib 数据不出厂。")
        provides = ["fs.list", "fs.hash", "fs.dedup", "fs.rename", "fs.organize"]
        depends_on = []
        inputs = ["action", "d", "p", "prefix", "suffix", "dry", "recursive"]
        outputs = ["result", "error", "duplicates", "pairs", "changed", "moved"]

        def _exec(self, cap, **kw):
            r = run(action=cap, **kw)
            if "error" in r:
                return {"ok": False, "data": {}, "error": r["error"], "degraded": True}
            return {"ok": True, "data": r}

        def _register_defaults(self):
            for cap in self.provides:
                self.register(cap, (lambda c=cap: (lambda **kw: self._exec(c, **kw)))())


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(run(action="fs.list", d=r"C:/Windows/System32/drivers/etc"), default=str))
