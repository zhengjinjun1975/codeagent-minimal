#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""complexity-gate 原子 —— 编排前复杂度闸：先判任务要不要走长链，简单任务不进编排（规则层零模型调用，判不了保守判复杂）

能力：
  model.assess_complexity — 编排前复杂度闸：先判任务要不要走长链，简单任务不进编排（规则层零模型调用，判不了保守判复杂）

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

# 同目录核心模块（纯 stdlib）：规则层判复杂度，规则判不了保守判复杂
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import assess_core


class ComplexityGateAgent(AtomicAgent):
    name = "complexity-gate"
    version = "0.1.0"
    domain = "model"
    description = "编排前复杂度闸：先判任务要不要走长链，简单任务不进编排（规则层零模型调用，判不了保守判复杂）"
    provides = ["model.assess_complexity"]
    depends_on = []
    inputs = ["text"]
    outputs = ["echo"]

    def _register_defaults(self):
        self.register("model.assess_complexity", self._assess_complexity)

    def _assess_complexity(self, text=None, task=None, rules=None, hints=None):
        """model.assess_complexity：编排前复杂度闸（真算法在同目录 assess_core.py，纯 stdlib）。

        规则层零模型调用；规则判不了保守判复杂（complex=True, degraded=True），不假装判断成功。
        """
        subject = task or text or (hints or {}).get("task")
        if not subject:
            return self._envelope(False, degraded=True, error="缺 task/text 入参")
        r = assess_core.assess_complexity(subject, rules)
        return self._envelope(True, data={
            "complex": r.get("complex"), "reason": r.get("reason"),
            "est_steps": r.get("est_steps"), "layer": r.get("layer"),
            "confident": r.get("confident"), "degraded": r.get("degraded")})

agent = ComplexityGateAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(ComplexityGateAgent(), run_args={
        "capability": {"default": "model.assess_complexity", "choices": ["model.assess_complexity"]},
        "text": {"help": "示例入参"},
    }))
