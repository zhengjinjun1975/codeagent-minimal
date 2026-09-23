#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""capability_scope.py — 任务级能力作用域（P0-1）。

依据：arXiv 2609.08371《Authority Is Not a String: A Capability-Scoped Harness for
Prompt-Injection-Resistant Coding Agents》。核心主张：**拒绝用工具名/命令串来授权**
（那叫 ambient authority——同一个字符串在任何任务里判定都一样）；正确做法是在**读取仓库
内容与工具输出之前**，先把这次任务允许做什么（capability 集合）定下来，之后按 capability
约束调用。

本模块只做**确定性推导**（纯标准库、不调模型、可复现）：任务描述 + 预设画像 + 工作区根
→ 一个作用域对象。作用域与原有字符串规则**同时通过**才允许；作用域拒绝**不可**被字符串
规则放行（fail-closed）；两侧取更严的一侧。

使用顺序（要点，顺序本身就是安全属性）：
    scope = derive_scope(task="审查一下这个仓库")      # ① 先定权限
    guard = scope.check("read", "/repo/x.py")          # ② 再碰内容
    # 绝不许先读文件、再根据读到的东西决定权限（那就又被注入牵着走了）

接进审批链：`approval_policy.check_command(..., scope=scope, scope_action="exec")`。
"""

import os
import fnmatch

FACETS = ("read", "write", "exec", "net")

# 预设画像：识别不了任务类型时用最保守的 readonly（fail-closed，不猜"应该是只读吧"）
PROFILES = {
    "readonly":  {"read": True,  "write": False, "exec": False, "net": False},
    "implement": {"read": True,  "write": True,  "exec": True,  "net": False},
    "ops":       {"read": True,  "write": True,  "exec": True,  "net": True},
}

# 任务文本 → 画像（确定性关键词，先匹配更宽的权限档；识别不了→readonly）
_TASK_HINTS = (
    ("ops", ("部署", "发布", "上线", "运维", "回滚", "deploy", "release", "rollback")),
    ("implement", ("实现", "修复", "改写", "重写", "重构", "新增", "补",
                   "implement", "fix", "refactor", "patch", "add ", "build")),
    ("readonly", ("审查", "分析", "评估", "检索", "调研", "排查", "诊断", "统计",
                  "review", "audit", "analy", "scan", "survey", "inspect", "report")),
)

# 永不外放的敏感路径（即使在 workspace 内）——凭据/密钥这类东西一个读取口都不该开
_SENSITIVE = (".env", "*.pem", "*.key", "id_rsa*", "*.p12", "credentials*", "*.secret",
              ".git/config", "*token*.json")


def profile_for_task(task: str) -> str:
    """按任务文本推画像。识别不了 → readonly（宁可多要一次人工，也不要默默放权）。"""
    t = (task or "").lower()
    for label, kws in _TASK_HINTS:
        if any(k in t for k in kws):
            return label
    return "readonly"


def _norm(p) -> str:
    """归一化路径用于包含判断（Windows 大小写不敏感；统一分隔符）。"""
    try:
        return os.path.normcase(os.path.realpath(str(p))).replace("\\", "/").rstrip("/")
    except Exception:
        return str(p).lower().replace("\\", "/").rstrip("/")


def _match_any(globs, path) -> bool:
    p = (path or "").replace("\\", "/").lower()
    base = os.path.basename(p)
    return any(fnmatch.fnmatch(p, str(g).lower()) or fnmatch.fnmatch(base, str(g).lower())
               for g in globs or ())


class CapabilityScope:
    """一次任务的能力作用域：允许的 facet + 路径边界 + 敏感路径黑名单。"""

    def __init__(self, facets=None, workspace=None, label="readonly",
                 allow_paths=(), deny_paths=(), net_hosts=()):
        base = dict(PROFILES.get(label, PROFILES["readonly"]))
        for k, v in (facets or {}).items():
            if k in base:
                base[k] = bool(v)
        self.facets = base
        self.label = label
        self.workspace = _norm(workspace or os.getcwd())
        self.allow_paths = tuple(allow_paths or ())
        self.deny_paths = tuple(deny_paths or ()) + _SENSITIVE
        self.net_hosts = tuple(net_hosts or ())

    # ── 查询 ────────────────────────────────────────────────────────────
    def allows(self, action) -> bool:
        return bool(self.facets.get(action))

    def in_workspace(self, path) -> bool:
        p = _norm(path)
        return p == self.workspace or p.startswith(self.workspace + "/")

    def check(self, action: str, resource=None) -> dict:
        """判一次动作。返回 {effect: allow|deny, reason, action, resource, scope}。

        - facet 不许 → deny（这是"任务根本没这个权限"，不是"这个命令危险"）
        - 路径类动作：必需落在 workspace 内，且不命中敏感黑名单
        - 网络类动作：给了 net_hosts 就按白名单
        """
        action = str(action or "").lower()
        if action not in FACETS:
            return self._verdict("deny", "未知能力面 %r（fail-closed）" % action, action, resource)
        if not self.allows(action):
            return self._verdict("deny", "任务作用域不含 %s 权限（画像 %s）" % (action, self.label),
                                 action, resource)
        if action in ("read", "write") and resource:
            if _match_any(self.deny_paths, resource):
                return self._verdict("deny", "命中敏感路径黑名单", action, resource)
            if not self.in_workspace(resource) and not _match_any(self.allow_paths, resource):
                return self._verdict("deny", "路径越出工作区边界", action, resource)
        if action == "net" and self.net_hosts and resource:
            if not _match_any(self.net_hosts, resource):
                return self._verdict("deny", "目标主机不在网络白名单", action, resource)
        return self._verdict("allow", "作用域内许可", action, resource)

    def _verdict(self, effect, reason, action, resource):
        return {"effect": effect, "reason": reason, "action": action,
                "resource": str(resource) if resource is not None else None,
                "scope": self.to_dict()}

    def to_dict(self) -> dict:
        return {"label": self.label, "facets": dict(self.facets),
                "workspace": self.workspace,
                "allow_paths": list(self.allow_paths),
                "deny_paths": list(self.deny_paths),
                "net_hosts": list(self.net_hosts)}

    def __repr__(self):
        on = [k for k, v in self.facets.items() if v]
        return "<CapabilityScope %s: %s, ws=%s>" % (self.label, "+".join(on) or "none", self.workspace)


def derive_scope(task="", profile=None, workspace=None, allow_paths=(), deny_paths=(),
                 net_hosts=(), facets=None) -> CapabilityScope:
    """由任务描述/画像推导作用域。**在读取任何内容之前调用**（顺序即安全属性）。"""
    return CapabilityScope(facets=facets, workspace=workspace,
                           label=profile or profile_for_task(task),
                           allow_paths=allow_paths, deny_paths=deny_paths, net_hosts=net_hosts)


# ── 与字符串规则合判：谁更严听谁的，作用域拒绝不可被放行 ──────────────────
_SEVERITY = {"allow": 0, "ask": 1, "deny": 2}


def strictest(a: str, b: str) -> str:
    """取更严的一侧（deny > ask > allow）。"""
    return a if _SEVERITY.get(a, 1) >= _SEVERITY.get(b, 1) else b


def combine_with_policy(scope_verdict: dict, policy_decision: str) -> dict:
    """作用域判定 + 既有字符串规则判定 → 合判。作用域 deny 是硬拒（不可被 allow 规则翻盘）。"""
    sev = scope_verdict.get("effect", "ask")
    final = strictest(sev, policy_decision)
    reason = scope_verdict.get("reason", "")
    if final != policy_decision:
        reason = "作用域判定（%s）：%s；字符串规则给出 %s" % (sev, reason, policy_decision)
    return {"decision": final, "scope_effect": sev, "policy_decision": policy_decision,
            "reason": reason}
