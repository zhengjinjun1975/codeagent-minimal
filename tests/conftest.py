#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/conftest.py — 保证 code-memory 原子的本地记忆桥以 canonical 名被加载。

背景：仓库内含 code_agent_engine 与 code-memory 原子两处会 `import optmem_mem`
（可选外部共享记忆桥，env-gated、默认关闭）。为避免"谁先导入谁污染 sys.modules"
的顺序耦合（谁先导入就缓存哪份实现），本 conftest 在 pytest 收集任何测试模块之前，
先把本仓库 code-memory 原子自带的本地桥 `agents/memory/code-memory/optmem_mem.py`
按 canonical 名 `optmem_mem` 载入 sys.modules。此后 code_agent_engine 与 code-memory
原子 import 到的都是这份本地桥（其 save/recall 与引擎用法兼容），让测试对模块加载
顺序鲁棒、不会因共享记忆桥未配置而中断。桥未配置时相关调用均静默降级到本地记忆。
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
# code-memory 原子本地桥 = 本仓库自带（含 available()）的实现
_LOCAL_OM = os.path.join(_REPO, "agents", "memory", "code-memory", "optmem_mem.py")

if os.path.isfile(_LOCAL_OM):
    _spec = importlib.util.spec_from_file_location("optmem_mem", _LOCAL_OM)
    if _spec is not None:
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules["optmem_mem"] = _mod
        try:
            _spec.loader.exec_module(_mod)
        except Exception:  # pragma: no cover - 兜底: 载入失败不阻断收集
            sys.modules.pop("optmem_mem", None)
