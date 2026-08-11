#!/usr/bin/env python3
"""极简代码原子（来自 Obsidian 代码原子库）。复用优先·极简落地方法论。"""
import numpy as np

def softmax(x, axis=-1):
    x = np.asarray(x, dtype=np.float64)     # 提升精度防溢出
    m = np.max(x, axis=axis, keepdims=True) # 每行最大值，keepdims 保持形状
    e = np.exp(x - m)                       # 减 max → 指数输入≤0，不溢出
    return e / np.sum(e, axis=axis, keepdims=True)

if __name__ == '__main__':
    # 演示用法（提取自代码原子的'使用要点'）
    pass

