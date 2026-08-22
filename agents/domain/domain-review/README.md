# domain-review 原子

领域代码审查原子（`open_source:true`，P2）：跨仓库 import 依赖拓扑断链（复用 chain_break）+ 工业阀门领域规则审查。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `domain.imports` | 跨仓库 import 依赖拓扑断链审查 |
| `domain.valve` | 工业阀门领域规则审查（记录级规则，按 P0/P1/P2 定级） |

## 入参

- `repos`：仓库列表（imports 用）
- `target`：目标文件或目录
- `data`：待审查记录（valve 用）
- `rules`：领域规则集

## 独立自测

```bash
python agents/domain/domain-review/main.py --capability domain.imports --repos '["."]'
python agents/domain/domain-review/main.py --capability domain.valve --data '[{"id":1}]'
```

## 依赖

- `chain_break.py`（仓库内）
- 无第三方依赖
