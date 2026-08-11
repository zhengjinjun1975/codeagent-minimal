---
title: "代码原子：X-bar 质量控制图"
tags: "[code-atom, 智能制造, 质量控制, 统计过程控制, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 智能制造
source: sktime（https://github.com/sktime/sktime）
source_license: BSD-3-Clause
status: draft
---

# 代码原子：X-bar 质量控制图

> 从开源项目 `sktime` 的统计阈值异常检测（`sktime/detection/stat_threshold.py` 的 `StatThresholdAnomaliser`，面向均值/标准差的阈值判定）提炼的基于 numpy 的最小可复用片段。遵循极简代码原则：用矩阵运算替代循环。

## 解决的问题
产品关键参数（如尺寸、重量）随批次漂移出规格。用 X-bar 控制图判断**整体均值是否失稳**——根据子组均值与 ±3σ 控制限比较，超限即判为失控，替代凭经验的抽检。

## 极简代码（核心）

```python
import numpy as np

# sample: (n_subgroups, n_per_subgroup) 每行是同一批抽的 n 个读数
rng = np.random.RandomState(42)
sample = rng.normal(50, 2, (20, 5))            # 20批 × 每批5个

means = sample.mean(axis=1)                     # 每批均值
grand = means.mean()                            # 总体均值
sigma = sample.std(axis=1).mean() / np.sqrt(5)  # 组内标准误
UCL, LCL = grand + 3*sigma, grand - 3*sigma     # ±3σ 控制限
out = np.flatnonzero((means > UCL) | (means < LCL))  # 失控批次
```

## 使用要点
- `sigma` 用**组内标准差均值 / √n**（标准误）而非总体 σ，才能反映抽样误差；这是控制图区别于普通均值比较的关键。
- ±3σ 意味着约 0.27% 误报率；`out` 为空即流程受控。极简在于用 `axis=1` 一次算整批统计、用向量化比较替代 `for` 循环。
- 适用：抽检计量型参数（尺寸、重量、浓度）的过程稳定性监控。

## 来源
- 项目：sktime（https://github.com/sktime/sktime），BSD-3-Clause
- 提炼自：sktime 统计阈值异常检测思路（`sktime/detection/stat_threshold.py` 的 `StatThresholdAnomaliser`），此处用 numpy 实现极简等价物

## 何时用 / 何时别用
- ✅ 用：制造过程过程能力监控、批次抽检判异、需要可解释的固定控制限（±3σ）的场景。
- ❌ 别用：参数是单值非批次（子组概念不成立，改单值 EWMA 图）；需要识别故障根因或关联多参数（改监督分类/多变量模型）；数据非正态分布（3σ 假设失真）。
