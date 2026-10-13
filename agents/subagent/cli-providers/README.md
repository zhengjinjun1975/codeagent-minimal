# cli-providers —— 外部 CLI 子代理 provider 链：探测可用性 + 降级链 + 统一派活信封

域：`subagent`｜能力：`subagent.providers` / `subagent.dispatch` / `subagent.probe`

## 调用

```bash
# 单原子自测入口
python agents/subagent/cli-providers/main.py --capability subagent.providers --text hello

# 统一入口（进程内能力路由）
from agent_runtime import get_runtime
get_runtime().run_capability("subagent.providers", text="hello")
```

## 契约

| 项 | 值 |
|---|---|
| 能力 | `subagent.providers` / `subagent.dispatch` / `subagent.probe` |
| 入参 | `text` |
| 返回 | `{"echo": ...}` 或 `{ok:false, degraded:true, error}` |
| 依赖 | 无（`depends_on: []`） |
| 状态 | 骨架（确定性实现），待填真算法 |

新建后跑：`python atom_spec.py check agents/subagent/cli-providers` 与 `python scripts/ci.py`。
