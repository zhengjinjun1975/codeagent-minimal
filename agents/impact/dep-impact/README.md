# dep-impact 原子

依赖图影响分析原子（`open_source:true`）。复用 `dep_audit.py`（核心零改动），零 LLM，数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `impact.analyze` | 影响分析：改符号/模块波及谁 + 图概况（files/entities/edges/coupling/circular_imports） |
| `impact.circular` | 循环依赖检测（模块 import 环，Tarjan SCC） |
| `impact.coupling` | 模块耦合指标（fan_in/fan_out/耦合指数/影响面） |

## 入参

- `path`：目标文件或目录（必填）
- `symbol` / `impact`：函数/类/模块名（可选，影响分析用）
- `transitive`：影响分析是否含传递闭包

## 独立自测

```bash
python agents/impact/dep-impact/main.py <path> [--impact <symbol>] [--transitive]
```

## 依赖

- 无（零 LLM，纯静态依赖图分析）
