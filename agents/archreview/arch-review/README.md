# arch-review 原子

架构审查原子（`open_source:true`）：分层/依赖方向/边界/攻击面清单/设计意图比对（声明 vs 实现）。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `archreview.layers` | 分层分析：识别层 + 层内成员 + 依赖违规 |
| `archreview.boundary` | 边界审查：声明边界 vs 实际依赖方向比对 |
| `archreview.surface` | 攻击面清单：外部可触达入口盘点 |
| `archreview.intent` | 设计意图比对：声明 vs 实现差异 |

## 入参

- `path`：目标文件或目录（必填）

## 独立自测

```bash
python agents/archreview/arch-review/main.py <path> --capability archreview.layers
python agents/archreview/arch-review/main.py <path> --capability archreview.boundary
python agents/archreview/arch-review/main.py <path> --capability archreview.surface
python agents/archreview/arch-review/main.py <path> --capability archreview.intent
```

## 依赖

- `arch_review.py`（仓库内算法库）
- 无第三方依赖
