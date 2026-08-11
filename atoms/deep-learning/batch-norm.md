---
title: 批归一化前向传播（BatchNorm, numpy）
tags:
  - 深度学习
  - numpy
  - 特征归一化
  - 代码原子
type: code-atom
domain: 深度学习
source: Ioffe & Szegedy, "Batch Normalization" (2015)
source_license: 论文实现 / 自研
status: draft
created: 2026-08-11
---

## 解决的问题

深层网络内部协变量偏移导致训练慢、对初始化和学习率敏感。批归一化在每一层**对每个特征维度做标准化**：减批内均值、除批内标准差，再乘可学习 γ、加 β。输入落在 0 均值 1 方差附近，训练更稳更快，可用更大学习率。

## 极简代码（训练态）

```python
import numpy as np

def batch_norm_forward(X, gamma, beta, eps=1e-5):
    mu = X.mean(axis=0, keepdims=True)          # 每个特征维度的批均值
    var = X.var(axis=0, keepdims=True)          # 批方差
    Xn = (X - mu) / np.sqrt(var + eps)          # 标准化 (+eps 防除0)
    return gamma * Xn + beta, Xn, mu, var       # 缩放平移后输出, 及mu/var
```

## 使用要点

- **训练态**：用批内均值/方差，返回 `(输出, Xn, mu, var)` —— 反向传播算梯度时需要 Xn、mu、var。
- **推理态**：必须改用训练时滑动平均的全局均值/方差，**不可**用当前批统计（批大小=1 时方差为 0）。原子未含推理分支，可补：`gamma*(X - running_mu)/sqrt(running_var+eps) + beta`。
- `eps` 默认 `1e-5`（PyTorch 默认）；数值上先加再开方。
- 逐特征维度归一化：`axis=0` 是 batch 维，输出形状与 X 相同。

## 来源

- Ioffe & Szegedy, *Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift*, 2015
- PyTorch `nn.BatchNorm1d` 文档

## 何时用 / 别用

- **用**：深/宽网络隐藏层输入预处理、理解 BatchNorm 原理、无框架手写实现。
- **别用**：推理场景 → 需全局统计分支；序列/图数据常用 LayerNorm/GroupNorm 而非 BatchNorm；显式做实时批内归一化又要求逐样本独立时，用 LayerNorm。
