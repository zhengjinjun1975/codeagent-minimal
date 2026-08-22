# command-approvals 原子

高危命令/工具审批原子（`open_source:true`，借鉴 Codex approvals.rs / execpolicy）。命令/工具/文件/网络 `allow/ask/deny` 细粒度判定 + 高危命令表 + 人工确认。复用 code-dispatch 权限模型，算法收敛进开源 `approval_policy.py`。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `approval.check` | 对资源做整体审批判定，返回 allow/ask/deny + 匹配规则 |
| `approval.classify` | 按资源类型/高危命令表判定层级 |
| `approval.resolve` | 结合用户选择（allow/deny）裁决最终决策 |
| `approval.policy` | 获取/合并默认审批策略（default + rules） |

## 入参

- `resource`：待审批的命令/工具/文件/网络资源（必填）
- `resource_type`：资源类型（command/tool/file/network）
- `policy`：自定义审批策略（合并默认策略）
- `decision`：当前决策（resolve 用）
- `user_choice`：用户选择（resolve 用）

## 独立自测

```bash
python agents/approval/command-approvals/main.py --capability approval.check --resource "rm -rf /"
```

## 依赖

- `approval_policy.py`（仓库内算法库）
- 无第三方依赖
