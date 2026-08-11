---
title: "代码原子：产线能耗/功率监测（滑动窗口均功率 + 峰值检测）"
tags: "[code-atom, 智能制造, 能耗监测, 功率, 峰值检测, numpy, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 智能制造
source: numpy（https://github.com/numpy/numpy）
source_license: BSD-3-Clause
status: draft
---

# 代码原子：产线能耗/功率监测（滑动窗口均功率 + 峰值检测）

> 从 numpy 卷积（`convolve`）与分位数统计提炼的最小可复用片段：产线功率/能耗数据的滑动窗口平均功率与过载峰值检测。遵循极简原则：滑动平均就是一次卷积，峰值检测就是一个布尔比较——不需要 pandas `rolling` 之外的任何重库。

## 解决的问题
产线电机/设备功率曲线监控：平滑出滑动窗口内的平均功率（判断稳态负载），并捕捉瞬时过载尖峰（设备堵转、冲击载荷、异常起停），用于能耗统计和设备过载预警。

## 极简代码（核心）

```python
import numpy as np

# 产线能耗监测：滑动窗口平均功率 + 峰值/尖峰检测
rng = np.random.RandomState(1)
p = np.abs(np.sin(np.linspace(0, 20, 500))) * 50 + rng.normal(0, 3, 500)
p[300:305] += 200  # 一个过载尖峰

w = 10
avg = np.convolve(p, np.ones(w) / w, mode='same')   # 窗口平均功率
peak = p > (avg + 3 * np.std(p))                     # 超均线3σ视为尖峰
peak_idx = np.where(peak & (p > np.percentile(p, 99)))[0]
```

## 使用要点
- `np.convolve(p, np.ones(w)/w, mode='same')` 一行实现滑动平均（w 为窗口点数），无需 pandas；`mode='same'` 保持输出长度不变。
- 峰值判定用**双条件**更稳：既要"超均线 3σ"又要"超全局 99 分位"，避免把正常波动误报为过载——这是极简里的实用技巧。
- 平均功率×时长可积分出能耗（kWh）；尖峰时刻 `peak_idx` 对应具体过载时间点，可直接关联 PLC/停机日志。

## 来源
- 项目：numpy（https://github.com/numpy/numpy）
- 许可：BSD-3-Clause
- 提炼自：`numpy.convolve` 滑动窗口 + 分位数阈值检测的标准能耗监测惯用法

## 何时用 / 何时别用
- ✅ 用：电机/产线功率曲线稳态负载评估、过载尖峰报警、能耗分时段统计；快速原型不需要 pandas 重依赖时。
- ❌ 别用：需要指数加权移动平均的平滑（用 `ewm` 或 `anomaly-threshold` 的 EWMA）；要按**时间标签**（datetime）而非样本点做窗口（用 pandas `resample`）；信号本身带强趋势时先去除趋势再判峰值。
