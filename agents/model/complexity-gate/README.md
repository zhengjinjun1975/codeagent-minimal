# complexity-gate —— 编排前复杂度闸：先判任务要不要走长链，简单任务不进编排（规则层零模型调用，判不了保守判复杂）

域：`model`｜能力：`model.assess_complexity`

## 调用

```bash
# 单原子自测入口
python agents/model/complexity-gate/main.py --capability model.assess_complexity --text hello

# 统一入口（进程内能力路由）
from agent_runtime import get_runtime
get_runtime().run_capability("model.assess_complexity", text="hello")
```

## 契约

| 项 | 值 |
|---|---|
| 能力 | `model.assess_complexity` |
| 入参 | `text` |
| 返回 | `{"echo": ...}` 或 `{ok:false, degraded:true, error}` |
| 依赖 | 无（`depends_on: []`） |
| 状态 | 骨架（确定性实现），待填真算法 |

新建后跑：`python atom_spec.py check agents/model/complexity-gate` 与 `python scripts/ci.py`。
