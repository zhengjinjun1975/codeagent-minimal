---
title: 简单 MLP 前向传播（numpy）
tags:
  - 深度学习
  - numpy
  - 模型推理
  - 代码原子
type: code-atom
domain: 深度学习
source: 自研 / 通用教材
source_license: 公有领域
status: draft
created: 2026-08-11
---

## 解决的问题

一个两隐藏层感知机（MLP）的**前向传播**：输入 X → 线性层(W1,b1) → ReLU 激活 → 线性层(W2,b2) → 输出。numpy 纯矩阵运算即可实现一次推理，无需深度学习框架。

## 极简代码

```python
import numpy as np

def relu(z):
    return np.maximum(z, 0)

def mlp_forward(X, params):
    Z1 = X @ params["W1"] + params["b1"]  # 输入 → 隐藏层线性变换
    A1 = relu(Z1)                          # 隐藏层激活
    Z2 = A1 @ params["W2"] + params["b2"]  # 隐藏层 → 输出
    return Z2, A1                          # 返回输出与中间激活(反向传播可复用)
```

## 使用要点

- `@` 是 numpy 矩阵乘法；X 形状 `(batch, in)`, W1 `(in, hidden)`, W2 `(hidden, out)`。
- 权重用小随机值初始化（如 `np.random.randn(n)*0.1`），b 初始为 0，否则对称问题导致无法学习。
- 分类任务输出层后接 softmax（见 softmax-stable 原子）。
- 中间激活 A1 返回出来，反向传播计算梯度时免重算。

## 来源

- Goodfellow《Deep Learning》第 6 章
- 手写神经网络前向/反向标准模板

## 何时用 / 别用

- **用**：理解前向计算本质、轻量推理、无框架环境的教学/原型。
- **别用**：真实训练/复杂网络（缺反向传播、优化器、正则化）→ 用 PyTorch `nn.Linear`/`nn.Sequential`；大数据量时 `@` 慢，考虑批量/向量化或框架。
