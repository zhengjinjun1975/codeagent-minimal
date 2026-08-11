---
title: softmax 稳定版（数值稳定）
tags:
  - 深度学习
  - numpy
  - 张量操作
  - 代码原子
type: code-atom
domain: 深度学习
source: 自研 / 通用教材
source_license: 公有领域
status: draft
created: 2026-08-11
---

## 解决的问题

softmax 把任意实数向量压成和为 1 的概率分布。直接 `exp(x)/sum(exp(x))` 在 x 很大（如 logits=1000）时 exp 溢出为 inf → NaN；x 很小时也可能下溢为 0。**减去每行最大值** 不改变结果（因为分子分母同时除以 e^m），却把指数输入拉到 ≤0，彻底消除溢出。

## 极简代码

```python
import numpy as np

def softmax(x, axis=-1):
    x = np.asarray(x, dtype=np.float64)     # 提升精度防溢出
    m = np.max(x, axis=axis, keepdims=True) # 每行最大值，keepdims 保持形状
    e = np.exp(x - m)                       # 减 max → 指数输入≤0，不溢出
    return e / np.sum(e, axis=axis, keepdims=True)
```

- 输出与 `exp(x)/sum(exp(x))` 数学等价（分子分母同除 e^m）。
- 默认作用于最后一维；分类任务传 `axis=-1` 即可。

## 使用要点

- `keepdims=True` 必须加，否则广播维度丢失、形状出错。
- 返回的是 float64，可加 `x.astype(np.float32)` 再算以省内存。
- 数值稳定性来自**先减 max**，而不是换 `np.exp` 的精度。

## 来源

- Goodfellow《Deep Learning》深度学习基础
- 通用 softmax 数值稳定实现（CS231n 等）

## 何时用 / 别用

- **用**：logits 可能很大/很小的分类头、注意力权重归一化。
- **别用**：追求训练稳定可用 PyTorch `F.softmax`（同样内置减 max）；需要对数域下溢保护时改用 `log_softmax`（本原子未覆盖，可另立）。
