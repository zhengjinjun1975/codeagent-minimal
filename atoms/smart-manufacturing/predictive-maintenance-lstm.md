---
title: "代码原子：随机森林预测性维护（故障分类）"
tags: "[code-atom, 智能制造, 预测性维护, 机器学习, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 智能制造
source: NASA C-MAPSS Turbofan（https://www.nasa.gov/intelligent-systems-division/discovery-systems-and-technology/turbofan-engine-degradation-simulation-data-set）
source_license: 公开数据集（NASA 开源数据）
status: draft
---

# 代码原子：随机森林预测性维护（故障分类）

> 从 NASA C-MAPSS 涡扇退化数据集的经典预测性维护任务提炼的最小可复用片段：用 sklearn `RandomForestClassifier` 判断设备"正常/故障"。遵循极简代码原则：预测性维护并不默认要上 LSTM，sklearn 树模型往往够用且可解释。

## 解决的问题
已有设备特征（一段滑动窗口的温度/振动/压力均值、运行时长等）和故障标签时，训练一个分类器判断设备当前是否处于故障态——预测性维护的监督学习基线，比 LSTM 更快、更可解释。

## 极简代码（核心）

```python
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# X: 每台设备一段窗口的特征 [温度均值, 振动RMS, 压力均值, 运行时长h]
rng = np.random.RandomState(7)
X = np.vstack([rng.randn(240, 4) + [20, 0.5, 100, 10],
               rng.randn(60, 4)  + [35, 2.5, 160, 10]])  # 正常240 + 故障60
y = np.array([0] * 240 + [1] * 60)
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42)

clf = RandomForestClassifier(n_estimators=100, random_state=42)
clf.fit(Xtr, ytr)
acc = clf.score(Xte, yte)             # 准确率
imp = clf.feature_importances_        # 哪些传感器特征最关键
proba = clf.predict_proba(Xte[:3])    # 故障概率，用于 RUL/优先级
```

## 使用要点
- 特征要按**固定窗口聚合**（配 `rolling-window-preprocess` 原子做滑动窗口统计），因为模型输入必须是"一行一个窗口"。这也是极简所在：把时序问题转成表格分类问题，直接用 sklearn 而非上 LSTM。
- `feature_importances_` 免费给出故障主因特征（如振动RMS），可直接用于工单诊断；`predict_proba` 输出故障概率，适合做风险排序和剩余寿命(RUL)分档。
- 数据不平衡（故障样本远少于正常）时建议用 `class_weight='balanced'`。

## 来源
- 项目：NASA C-MAPSS Turbofan（公开数据集，预测性维护经典 benchmark）
- 许可：NASA 公开数据
- 提炼自：C-MAPSS RUL 任务的标准做法（特征工程→分类器），此处用 sklearn `RandomForestClassifier` 极简复现

## 何时用 / 何时别用
- ✅ 用：已有设备故障标签、需要快速上线且要可解释（能说清"哪个传感器异常"）；数据量中等的监督预测性维护。
- ❌ 别用：只有振动原始波形、无标签时（改 `isolation-forest-anomaly` 无监督）；强时序依赖（如自然语言/波动信号趋势预测）时才考虑 LSTM/时序模型；数据量极大需在线流式预测时改用梯度提升或在线模型。
