---
title: "代码原子：传感器数据融合（多信号时间戳对齐）"
tags: "[code-atom, 智能制造, 传感器融合, 时间序列, pandas, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 智能制造
source: pandas（https://github.com/pandas-dev/pandas）
source_license: BSD-3-Clause
status: draft
---

# 代码原子：传感器数据融合（多信号时间戳对齐）

> 从 pandas 时间序列对齐功能（`reindex` / `interpolate` / `ffill`）提炼的最小可复用片段：把采样率、时间戳各不相同的多路传感器读数对齐到统一时间网格，是传感器融合和后续建模的前置步骤。遵循极简原则：重采样对齐就是 `reindex` + 插值两行的事。

## 解决的问题
产线多路传感器（温度、振动、压力）常来自不同采集器，采样率/时间戳不一致，无法直接拼成特征矩阵。本原子把它们对齐到统一的固定频率时间网格，一行一个时间点。

## 极简代码（核心）

```python
import pandas as pd

# 两路传感器，采样率/时间戳不一致，对齐到统一 1Hz 网格
a = pd.Series([1, 2, 3, 4], index=pd.to_datetime(['00:00:00', '00:00:01', '00:00:02', '00:00:03']), name='temp')
b = pd.Series([0.5, 1.5, 2.5], index=pd.to_datetime(['00:00:00.5', '00:00:01.5', '00:00:02.8']), name='vib')

grid = pd.date_range('00:00:00', '00:00:03', freq='1s')
df = pd.DataFrame({
    'temp': a.reindex(grid).interpolate(),          # 线性插值填齐
    'vib':  b.reindex(grid, method='ffill').ffill() # 前一时刻采样值前向填充
})
```

## 使用要点
- 关键差异：`reindex` 默认只保留**完全匹配**的时间戳（所以高频/偏移采样会丢成 NaN）；必须用 `method='ffill'`（或对连续量用 `interpolate()`）补齐目标网格。这是极简里的唯一坑。
- 连续量（温度/压力）用线性插值 `interpolate()` 合理；事件/布尔量或低频量用 `ffill()` 前向填充更稳妥，避免制造虚假趋势。
- 对齐后可配 `rolling-window-preprocess` / `predictive-maintenance-lstm` 原子直接做窗口特征或建模。

## 来源
- 项目：pandas（https://github.com/pandas-dev/pandas）
- 许可：BSD-3-Clause
- 提炼自：`DataFrame.reindex` + `Series.interpolate` / `ffill` 的时间对齐惯用法

## 何时用 / 何时别用
- ✅ 用：多路传感器采样率不同、或含漂移/缺失时间戳需要统一网格；任何"多信号融合成特征矩阵"的前处理。
- ❌ 别用：各路本来就同频率同时间戳（直接 concat 即可）；含传感器**损坏时段**（长时间缺失）时插值会造出假数据，应先用 `anomaly-threshold` 或人工打标过滤掉再对齐。
