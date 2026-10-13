#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli-providers 原子 —— 外部 CLI 子代理 provider 链：探测可用性 + 降级链 + 统一派活信封

能力：
  subagent.providers — 外部 CLI 子代理 provider 链：探测可用性 + 降级链 + 统一派活信封
  subagent.dispatch — （待填）
  subagent.probe — （待填）

铁律：本文件只做壳（声明身份 + 注册能力），真算法放同目录模块里 import 调用。
返回一律走信封：成功 {"字段": 值} 或失败 self._envelope(False, degraded=True, error=...)。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo_root(start):
    """向上找含 atomic_base.py 的目录（不靠固定层级，原子挪位置/被软链也不断）。"""
    d = start
    for _ in range(8):
        if os.path.isfile(os.path.join(d, "atomic_base.py")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.dirname(os.path.dirname(os.path.dirname(start)))


REPO_ROOT = _repo_root(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

# 同目录核心模块（纯 stdlib）：外部 CLI provider 探测与派活
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import cli_providers as cp


class CliProvidersAgent(AtomicAgent):
    name = "cli-providers"
    version = "0.1.0"
    domain = "subagent"
    description = "外部 CLI 子代理 provider 链：探测可用性 + 降级链 + 统一派活信封"
    provides = ["subagent.providers", "subagent.dispatch", "subagent.probe"]
    depends_on = []
    inputs = ["text"]
    outputs = ["echo"]

    def _register_defaults(self):
        self.register("subagent.providers", self._providers)
        self.register("subagent.dispatch", self._dispatch)
        self.register("subagent.probe", self._probe)

    def _providers(self):
        """subagent.providers：列出 provider 表与各自可用性（探测带缓存）。"""
        lst = cp.list_providers()
        return self._envelope(True, degraded=(lst["usable_count"] == 0), data=lst,
                              error=None if lst["usable_count"] else "本机没有可用的外部 CLI 子代理")

    def _dispatch(self, task=None, provider=None, cwd=None, timeout=None):
        """subagent.dispatch：派活给外部 CLI 子代理（provider 缺省走降级链取第一个可用的）。"""
        if not task:
            return self._envelope(False, degraded=True, error="缺 task 入参")
        r = cp.dispatch(task, provider=provider, cwd=cwd, timeout=timeout)
        ok = bool(r.get("ok"))
        return self._envelope(ok, degraded=(not ok), data=r,
                              error=None if ok else r.get("reason"))

    def _probe(self, name=None):
        """subagent.probe：探测一个（或全部）provider 是否可用。"""
        items = cp.probe(name=name)
        if not items:
            return self._envelope(False, degraded=True, error="provider 不在表内: %s" % name)
        ok = any(i.get("ok") for i in items)
        return self._envelope(True, degraded=(not ok),
                              data={"probed": items, "any_available": ok},
                              error=None if ok else "探测到的 provider 都不可用")

agent = CliProvidersAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(CliProvidersAgent(), run_args={
        "capability": {"default": "subagent.providers", "choices": ["subagent.providers", "subagent.dispatch", "subagent.probe"]},
        "text": {"help": "示例入参"},
    }))
