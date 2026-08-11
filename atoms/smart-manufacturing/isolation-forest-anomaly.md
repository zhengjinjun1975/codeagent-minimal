---
title: "代码原子：孤立森林无监督故障检测"
tags: "[code-atom, 智能制造, 预测性维护, 故障检测, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 智能制造
source: sktime（https://github.com/sktime/sktime）
source_license: BSD-3-Clause
status: draft
---

# 代码原子：孤立森林无监督故障检测

> 从开源项目 `sktime` 的异常检测模块（`sktime/detection/lof.py` 等，面向时序异常）提炼的基于 sklearn 的最小可复用片段。遵循极简代码原则：只写最少必要代码。

## 解决的问题
对多传感器读数的无标签数据做异常检测——不需要任何故障标注，自动把偏离正常的样本（疑似故障/早期失效）标出来，是预测性维护的第一步。

## 极简代码（核心）

```python
import numpy as np
from sklearn.ensemble import IsolationForest

# X: (n_samples, n_features) 传感器矩阵，例如 [温度, 振动, 压力]
rng = np.random.RandomState(42)
X = np.vstack([rng.randn(200, 3), rng.randn(10, 3) * 10])  # 200正常+10异常

clf = IsolationForest(contamination=0.05, random_state=42)
pred = clf.fit_predict(X)          # 1=正常, -1=异常
anom_idx = np.where(pred == -1)[0] # 故障样本下标
```

## 使用要点
- `contamination` 是**预期异常占比**：自动维护场景常取 0.01~0.05；数据量少时可先用 `score_samples(X)` 看分布再定阈值，而非硬编码。
- 一次 `fit_predict` 即可，无需标签；`random_state` 固定保证可复现。这是极简所在——把多步检测逻辑压缩成一个 fit+阈值判断。
- 适用：振动/温度/压力多通道同时偏离常态的设备早期失效。

## 来源
- 项目：sktime（https://github.com/sktime/sktime），BSD-3-Clause
- 提炼自：sktime 异常检测思路（`sktime/detection/lof.py` 的 `SubLOF`、`moving_window.py` 等），此处用 sklearn `IsolationForest` 实现极简等价物

## 何时用 / 何时别用
- ✅ 用：设备早期故障预警、无标注的传感器异常扫描、需要 1 个函数快速上线监控。
- ❌ 别用：已有故障标签时（改用监督分类更准）；需要定位"从哪一刻开始异常"的时序变点检测（改 sktime 的 `MovingWindow`）；异常是缓慢漂移（孤立森林对点状离群灵敏，对渐进漂移失效）。
