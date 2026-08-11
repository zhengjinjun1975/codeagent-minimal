---
title: "代码原子：obspy 带通滤波地震波形"
tags: "[code-atom, 地球物理, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 地球物理
source: obspy (https://github.com/obspy/obspy)
source_license: LGPL-3.0
status: draft
---

# 代码原子：obspy 带通滤波地震波形

> 从开源项目 `obspy` 提炼的最小可复用代码片段。遵循极简代码原则（Ponytail 阶梯）：标准库优先、一行能搞定就一行、只写最少必要代码。

## 解决的问题
对一段地震/地震勘探时间序列做 Butterworth 带通滤波，滤掉高频噪声与低频漂移，保留目标频带信号。

## 极简代码（核心）

```python
from obspy.signal.filter import bandpass
import numpy as np

data = np.load("trace.npy")                # 一维数组：原始采样
sampling_rate = 250.0                      # 采样率 Hz
freqmin, freqmax = 1.0, 60.0               # 通带低/高截止频率 Hz

filtered = bandpass(data, freqmin, freqmax,
                    sampling_rate, corners=4, zerophase=True)
```

## 使用要点
- 该 `bandpass` 是**纯 numpy 数组函数**（基于 `scipy.signal.iirfilter` 设计 + `sosfilt` 应用），不必用整套 obspy Stream/Trace 对象，可直接作用在任意一维数组上——这正是它最简之处。
- `corners=4` 为滤波阶数/角点数；`zerophase=True` 表示前向+后向各过一次（阶数翻倍）但**零相位偏移**，处理地震数据一般默认开。
- 频率上限 `freqmax` 应明显低于采样率一半（Nyquist）；如需更陡或特殊类型可改 `ftype='cheby1'/'cheby2'`，但默认 Butterworth 覆盖绝大多数场景。

## 来源
- 项目：obspy（https://github.com/obspy/obspy）
- 许可：LGPL-3.0（LICENSE.txt）
- 提炼自：`obspy/signal/filter.py` 的 `bandpass(data, freqmin, freqmax, df, corners=4, zerophase=False, ...)`（第 42 行起）。

## 何时用 / 何时别用
- ✅ 用：对任意 numpy 一维时间序列做带通/带限处理；配合 `lowpass`/`highpass`（同文件）做单边滤波。
- ❌ 别用：需要在波形上叠加去趋势、重采样、仪器响应校正等完整流程时——直接用 `Stream.filter('bandpass', ...)` 更省事；信号长度很短或阶数极高（内存/精度）时不建议 zerophase 双程滤波。
