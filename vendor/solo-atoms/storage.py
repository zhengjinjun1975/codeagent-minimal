# -*- coding: utf-8 -*-
"""storage.py — 存储原子: 原子写 / 带默认JSON读 / 并发锁。

极简原则: 标准库, 零依赖, 每个函数单职责。
用途: 任何需要安全持久化 JSON 的模块复用(记忆/技能/任务/配置)。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading


def atomic_write(path: str, data) -> None:
    """原子写 JSON: 写临时文件 + os.replace, 崩溃不损坏原文件。"""
    dirname = os.path.dirname(path) or "."
    os.makedirs(dirname, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirname, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def json_load(path: str, default=None):
    """读 JSON, 文件不存在或损坏返回 default(不抛错)。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# 全局锁注册表: 按路径加锁, 保证同一文件并发写安全
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def lock_for(path: str) -> threading.Lock:
    """为路径获取(共享)锁。"""
    with _LOCKS_GUARD:
        if path not in _LOCKS:
            _LOCKS[path] = threading.Lock()
        return _LOCKS[path]
