#!/usr/bin/env python3
"""command-approvals 原子壳（open_source:true）——高危命令/工具审批（P0 安全边界）。

借鉴 Codex `approvals.rs`（ApprovalAction + network-approval）与 `execpolicy/`（命令策略），
复用 codeagent 既有 code-dispatch 的 allow/ask/deny 三级权限模型（OpenCode P1-2），
算法进开源 approval_policy.py（check_command/classify/resolve），本原子只加壳。

能力（纯 stdlib，数据不出厂）：
  approval.check    — 对命令/工具/文件/网络资源做 allow/ask/deny 细粒度审批判定
  approval.classify — 快速风险分层（命中高危关键词兜底提升）
  approval.resolve  — 审批判定 + 人工选择 → 最终放行/拒绝
  approval.policy   — 返回默认高危审批策略 + 合并自定义规则
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import approval_policy as ap


class CommandApprovalsAgent(AtomicAgent):
    name = "command-approvals"
    version = "0.1.0"
    domain = "approval"
    description = ("高危命令/工具审批原子（P0，借鉴Codex approvals.rs/execpolicy）: "
                   "命令/工具/文件/网络 allow/ask/deny 细粒度判定 + 高危命令表 + 人工确认。"
                   "复用code-dispatch权限模型, 算法进开源approval_policy.py。纯stdlib数据不出厂。")
    provides = ["approval.check", "approval.classify", "approval.resolve", "approval.policy"]
    depends_on = []
    inputs = ["resource", "resource_type", "policy", "decision", "user_choice"]
    outputs = ["decision", "reason", "rule", "tier", "verdict", "granted", "matched_keywords", "pending"]

    def _register_defaults(self):
        self.register("approval.check", self._check)
        self.register("approval.classify", self._classify)
        self.register("approval.resolve", self._resolve)
        self.register("approval.policy", self._policy)

    def _check(self, resource=None, resource_type="command", policy=None):
        if not resource:
            return self._envelope(False, degraded=True, error="缺 resource 入参")
        return ap.check_command(resource, resource_type=resource_type, policy=policy)

    def _classify(self, resource=None, policy=None):
        if not resource:
            return self._envelope(False, degraded=True, error="缺 resource 入参")
        return ap.classify(resource, policy=policy)

    def _resolve(self, decision=None, user_choice=None):
        if not decision:
            return self._envelope(False, degraded=True, error="缺 decision 入参")
        return ap.resolve(decision, user_choice=user_choice)

    def _policy(self, custom=None):
        merged = ap.merge_policy(custom)
        # 只返回可序列化结构
        return {"default": merged["default"],
                "rules": merged["rules"], "rule_count": len(merged["rules"])}


agent = CommandApprovalsAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(CommandApprovalsAgent(), run_args={
        "capability": {"default": "approval.check", "choices": list(CommandApprovalsAgent.provides)},
        "resource": {}, "resource_type": {}, "decision": {}, "user_choice": {},
    }))
