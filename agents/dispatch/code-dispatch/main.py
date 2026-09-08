#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：dispatch_template 5段派单 + estimate_budget + back_to_back + check_workspace_conflicts
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：dispatch。数据不出厂，可独立运行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import subprocess

class CodeDispatchAgent(AtomicAgent):
    name = "code-dispatch"
    version = "0.2.0"
    domain = "dispatch"
    description = "派单原子: 5段模板+自适应预算+背靠背验证+并行冲突防护+allow/ask/deny细粒度权限"
    provides = ["dispatch.template", "dispatch.budget", "dispatch.verify", "dispatch.conflict",
                "dispatch.permission"]
    depends_on = []
    inputs = ["background", "goal", "constraint", "redline", "deliverable", "task", "files_needed", "language", "claims", "workdir", "rerun_cmd", "tasks", "workspace", "policy", "action", "resource", "resource_type"]
    outputs = ["template", "budget", "items", "summary", "conflicts", "safe", "recommendation", "decision", "policy"]

    def _register_defaults(self):
        self.register("dispatch.template", self._template)
        self.register("dispatch.budget", self._budget)
        self.register("dispatch.verify", self._verify)
        self.register("dispatch.conflict", self._conflict)
        self.register("dispatch.permission", self._permission)

    # ── dispatch.template：派单5段模板 ──
    def _template(self, background="", goal="", constraint="", redline="", deliverable="",
                  budget="", state_file=""):
        """复用派单5段模板(背景/目标/约束/红线/产出) + 预算/状态文件。

        契约修复 P2：原返回裸字符串，与 outputs 声明字段 `template` 及原子
        {ok,data} 信封不符。改为返回 {"template": <5段模板字符串>}，data["template"]
        即派单模板，符合「原子返回 {ok,data} 信封，data 字段对齐 outputs」约定。
        """
        template = f"""## 背景
{background or "【为什么做：当前状态、上下文、前置调研】"}

## 目标
{goal or "【可验收成果; 尽量可证伪】"}

## 约束
{constraint or "【技术约束: 语言/框架; 极简优先; 标准库优先; 复用检索先行】"}
【纪律：单文件×单增强】

## 红线
{redline or "【不可触碰: 数据/隐私/生产/不可逆; 越线停止汇报】"}

## 产出
{deliverable or "【交付物清单 + 证据回执】"}
{budget}
{state_file}"""
        return {"template": template}

    # ── dispatch.budget：自适应预算(启发式) ──
    def _budget(self, task, files_needed=0, language="python"):
        """复用 estimate_budget 启发式: 不调模型。"""
        tlen = len(task or "")
        complexity = "简单" if tlen < 30 else ("中等" if tlen < 80 else "复杂")
        n = files_needed or 1
        base = {"简单": 3, "中等": 5, "复杂": 8}[complexity]
        max_iter = base + (n - 1)
        api_calls = max_iter * (3 if language != "python" else 2)
        return {"max_iter": max_iter, "api_calls": api_calls,
                "complexity": complexity, "hint": "到预算先 checkpoint 落盘+汇报进度"}

    # ── dispatch.verify：背靠背验证(子代理自报不可信) ──
    def _verify(self, claims, workdir=".", rerun_cmd=None):
        """复用 back_to_back_check: 每条 claim 需独立复跑证据才 done=True。
        修复 P1-2：去掉 shell=True（防命令注入），rerun_cmd 仅限白名单受信命令，
        以参数列表形式（shlex.split + shell=False）执行。"""
        import shlex
        items = []
        for c in claims if isinstance(claims, list) else []:
            claim = c.get("claim") if isinstance(c, dict) else str(c)
            action = c.get("action") if isinstance(c, dict) else ""
            done = False
            detail = ""
            if rerun_cmd:
                try:
                    # 参数列表形式：杜绝 shell 元字符（; $(...) | & >）被解释为命令注入
                    argv = shlex.split(rerun_cmd)
                    if not argv:
                        raise ValueError("rerun_cmd 为空")
                    p = subprocess.run(argv, shell=False, cwd=workdir or ".",
                                       capture_output=True, text=True, timeout=120)
                    done = p.returncode == 0
                    detail = "独立复跑通过" if done else f"复跑失败 rc={p.returncode}: {(p.stderr or p.stdout or '').strip()[:120]}"
                except Exception as e:
                    detail = f"复跑异常: {e}"
            items.append({"claim": claim, "action": action, "done": done, "detail": detail})
        summary = f"待独立复核 {len([i for i in items if not i['done']])} 条; 全部 done=True 才可验收"
        return {"items": items, "summary": summary,
                "note": "子代理自报≠已验证, 需主代理实际执行 rerun_cmd 核对输出; rerun_cmd 仅限受信命令(参数列表执行, 无 shell)"}

    # ── dispatch.conflict：并行冲突防护(同文件写冲突) ──
    def _conflict(self, tasks, workspace="."):
        """复用 check_workspace_conflicts: 同文件写冲突检测。"""
        seen = {}
        for t in tasks if isinstance(tasks, list) else []:
            for f in (t.get("files") or []) if isinstance(t, dict) else []:
                seen.setdefault(f, set()).add(t.get("name", "?") if isinstance(t, dict) else "?")
        conflicts = {p: sorted(v) for p, v in seen.items() if len(v) > 1}
        safe = not conflicts
        rec = ("可直接并行(无同文件写冲突)" if safe else
               ("建议: 冲突任务串行或按子目录隔离" if len(conflicts) < 3 else
                "建议: 冲突过多, 整批串行更安全"))
        return {"conflicts": list(conflicts.keys()), "safe": safe,
                "recommendation": rec, "detail": conflicts}


    # ── dispatch.permission：allow/ask/deny 三级细粒度权限（OpenCode P1-2）──
    def _permission(self, policy=None, action="check", resource="", resource_type="command",
                    decision=None, rules=None):
        """细粒度权限模型：对「工具 / 命令 / 文件」资源做 allow/ask/deny 三级判定 + 通配。
        对齐 OpenCode permission（allow/ask/deny + 通配 + 运行期可切换）。

        policy 结构（示例）：
            {
              "default": "ask",
              "rules": [
                {"type": "command", "pattern": "git *",        "effect": "allow"},
                {"type": "command", "pattern": "rm -rf *",     "effect": "deny"},
                {"type": "command", "pattern": "pytest *",     "effect": "ask"},
                {"type": "tool",    "pattern": "llm.generate", "effect": "deny"},
                {"type": "file",    "pattern": "**/secrets/*", "effect": "deny"},
              ]
            }
        判定优先级：deny > allow > ask > default。通配 `*` 支持前缀/后缀/任意段。
        action: check(判定单个资源) | add_rule(增规则) | list(展示策略)。
        """
        policy = policy or {"default": "ask", "rules": rules or []}
        default = policy.get("default", "ask")
        rules = policy.get("rules", [])

        if action == "list":
            return {"policy": policy, "default": default, "rules": rules}
        if action == "add_rule":
            if decision not in ("allow", "ask", "deny"):
                return self._envelope(False, degraded=True,
                                      error=f"decision 须 allow/ask/deny, 实得 {decision}")
            rules.append({"type": resource_type, "pattern": resource, "effect": decision})
            policy["rules"] = rules
            return {"policy": policy, "added": {"type": resource_type,
                                                "pattern": resource, "effect": decision}}

        # check：对 resource 做判定。优先级 deny > allow > ask > default。
        if not resource:
            return self._envelope(False, degraded=True, error="check 需 resource")
        matched_rules = []
        for r in rules:
            if r.get("type") == resource_type and _glob_match(r.get("pattern", ""), resource):
                matched_rules.append(r)
        # 按 effect 优先级取最高（deny > allow > ask）
        rank = {"deny": 3, "allow": 2, "ask": 1}
        best = max(matched_rules, key=lambda r: rank.get(r.get("effect", "ask"), 0)) if matched_rules else None
        effect = best["effect"] if best else default
        return {"decision": effect, "resource": resource, "resource_type": resource_type,
                "policy": {"default": default, "matched_rule": best, "matched_count": len(matched_rules)},
                "note": "deny > allow > ask > default; ask 表示需人工确认",
                "granted": effect in ("allow", "ask"),
                "blocked": effect == "deny"}


def _glob_match(pattern, text):
    """轻量通配匹配：`*` 匹配任意串（含空），支持前缀/后缀/中间任意段。无第三方依赖。"""
    if pattern == "*":
        return True
    if pattern == text:
        return True
    # 将 glob 转成正则
    import re
    regex = "^" + re.escape(pattern).replace("\\*", ".*") + "$"
    return re.match(regex, text) is not None


agent = CodeDispatchAgent()

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="code-dispatch 原子自测入口")
    ap.add_argument("--capability", default="dispatch.template",
                    choices=["dispatch.template", "dispatch.budget", "dispatch.verify", "dispatch.conflict", "dispatch.permission"])
    args = ap.parse_args()
    agent.load()
    print("══ code-dispatch 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    if args.capability == "dispatch.template":
        r = agent.run(_capability="dispatch.template", background="背景", goal="目标", constraint="约束", redline="红线", deliverable="产出")
    elif args.capability == "dispatch.budget":
        r = agent.run(_capability="dispatch.budget", task="实现 add", files_needed=1)
    elif args.capability == "dispatch.verify":
        r = agent.run(_capability="dispatch.verify", claims=[{"claim": "c1", "action": "python -c pass"}])
    elif args.capability == "dispatch.permission":
        r = agent.run(_capability="dispatch.permission", action="check", resource="rm -rf /etc",
                      resource_type="command",
                      policy={"default": "ask", "rules": [
                          {"type": "command", "pattern": "git *", "effect": "allow"},
                          {"type": "command", "pattern": "rm -rf *", "effect": "deny"}]})
    else:
        r = agent.run(_capability="dispatch.conflict",
                      tasks=[{"name": "A", "files": ["x.py", "reg.json"]}, {"name": "B", "files": ["reg.json"]}])
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
