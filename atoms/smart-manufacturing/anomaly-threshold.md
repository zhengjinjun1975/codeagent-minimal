---
title: "代码原子：SPC 统计过程控制阈值检测（均值±3σ / EWMA）"
tags: "[code-atom, 智能制造, 质量控制, SPC, 统计过程控制, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 智能制造
source: scipy（https://github.com/scipy/scipy）
source_license: BSD-3-Clause
status: draft
---

# 代码原子：SPC 统计过程控制阈值检测（均值±3σ / EWMA）

> 从统计过程控制（SPC）经典控制图思想（Shewhart 均值±3σ、EWMA）提炼的最小可复用片段：对过程量（瓶装量、厚度、直径等）检测是否"失控出界"。遵循极简原则：控制图阈值就是一列 `mean ± 3*std` 的算术，一个 `np.where` 判出界。

## 解决的问题
在线质量检测：判断某过程量是否偏离统计稳态。Shewhart 控制图用 ±3σ 抓点状离群，EWMA 对缓慢漂移更敏感——两者组合能覆盖绝大多数 SPC 场景，无需引入重量级统计库。

## 极简代码（核心）

```python
import numpy as np

# SPC 均值±3σ 控制图：对过程量检测出界即报警
rng = np.random.RandomState(42)
x = rng.normal(100, 2, 200)
x[150] = 108  # 人为一个离群点

mu, sd = np.mean(x), np.std(x)
ucl, lcl = mu + 3 * sd, mu - 3 * sd
alarm = np.where((x > ucl) | (x < lcl))[0]

# EWMA：对缓慢漂移更敏感，加权历史
ewma = np.empty_like(x, dtype=float)
ewma[0] = x[0]
for i in range(1, len(x)):
    ewma[i] = 0.2 * x[i] + 0.8 * ewma[i - 1]
```

## 使用要点
- ±3σ 对应约 99.73% 置信区间，**单次出界**即可报警；EWMA 的 `λ=0.2` 是常用的历史权重（越大越跟当前值），连续多点持续越界对漂移更可靠。
- 关键前提：数据应来自**受控稳态**（均值、标准差稳定）。若过程本身在漂移，先做差分/去趋势再建控制限，否则误报率高。这是极简里最容易忽略的坑。
- 计算只用 numpy 原生函数，无任何第三方统计库依赖——极简所在。

## 来源
- 项目：scipy 控制图参考 / SPC 标准方法（Shewhart、EWMA）
- 许可：BSD-3-Clause（scipy）
- 提炼自：Shewhart X̄ 控制图与 EWMA 控制图标准公式的极简 numpy 复现

## 何时用 / 何时别用
- ✅ 用：批量/连续产线的过程量质量监控（灌装量、厚度、直径、温度）；需要"一个函数快速上线的出界报警"。
- ❌ 别用：需要**在线滚动**控制限时（每次新样本重算均值，见 `rolling-window-preprocess`）；非正态/重尾数据（用分位数而非 ±3σ）；没有稳态基线时（先用 `isolation-forest-anomaly` 扫异常）。
