---
title: "代码原子：滑窗传感器预处理"
tags: "[code-atom, 智能制造, 传感器预处理, 特征工程, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 智能制造
source: sktime（https://github.com/sktime/sktime）
source_license: BSD-3-Clause
status: draft
---

# 代码原子：滑窗传感器预处理

> 从开源项目 `sktime` 的滑窗/变点检测机制（`sktime/detection/moving_window.py` 的滑动窗口思路）提炼的基于 pandas 的最小可复用片段。遵循极简代码原则：pandas 一行搞定就一行。

## 解决的问题
原始传感器时间序列噪声大、单点不可信。用滑窗统计量（均值、标准差）把"连续 N 个读数"压成稳定特征，既平滑噪声又保留局部趋势，是给故障检测/预测模型喂数据的标准预处理。

## 极简代码（核心）

```python
import numpy as np, pandas as pd

# df: 传感器时序，列为 sensor 读数，按时间排序
rng = np.random.RandomState(42)
df = pd.DataFrame({"sensor": np.sin(np.linspace(0, 6, 50)) + rng.normal(0, .1, 50)})

df["mean3"] = df["sensor"].rolling(3, min_periods=1).mean()  # 3点滑动均值
df["std3"]  = df["sensor"].rolling(3, min_periods=1).std()   # 3点滑动标准差
```

## 使用要点
- `min_periods=1` 避免窗口开头产生 NaN，让前 N-1 行也能参与计算；否则头几行要 `dropna()`。
- 窗口大小 3 表示"看当前点+前两个点"；抖动大就加大窗口（如 5/10）。这是极简体现——一个 `rolling` 方法同时搞定滑动窗口与统计，无需手动切片循环。
- 多传感器可 `df[cols].rolling(3).mean()` 一次性对多列批量滑窗。

## 来源
- 项目：sktime（https://github.com/sktime/sktime），BSD-3-Clause
- 提炼自：sktime 滑窗机制（`sktime/detection/moving_window.py` 的移动窗口变换），此处用 pandas `rolling` 实现极简等价物

## 何时用 / 何时别用
- ✅ 用：振动/温度等高频传感器降噪与特征化、给机器学习模型准备滑窗输入特征、变点检测前的平滑。
- ❌ 别用：需要保留时序因果性且要求严格无前视泄漏时（务必不用中心窗口、确认窗口只含历史）；样本是独立非时序数据（滑窗无意义）；需要在线流式实时计算（`rolling` 是批处理语义）。
