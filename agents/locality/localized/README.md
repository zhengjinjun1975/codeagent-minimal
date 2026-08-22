# localized 原子

本地化原子（`open_source:true`，P1）：数据不出厂审计（网络/云端调用信号）+ 模型降级链本地主（本地可用绝不外发）+ 本地路由审查。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `local.audit` | 数据不出厂审计：检测网络/云端调用信号 + LLM 配置本地优先性 |
| `local.chain` | 模型降级链：本地可用绝不外发 |
| `local.route` | 本地路由审查 |

## 入参

- `target` / `code`：目标代码
- `local_only`：仅本地开关
- `candidates`：候选模型链
- `config_path`：LLM 配置文件路径
- `timeout`：超时

## 独立自测

```bash
python agents/locality/localized/main.py --capability local.audit --target <dir>
python agents/locality/localized/main.py --capability local.route --local_only 1
```

## 依赖

- 无（纯 stdlib）
