#!/usr/bin/env python3
"""极简代码原子（来自 Obsidian 代码原子库）。复用优先·极简落地方法论。

孤立森林无监督故障检测（预测性维护）。
需 sklearn（可选，装 sklearn 才能运行演示）。
"""
import numpy as np


def isolation_forest_demo():
    """孤立森林无监督故障检测：不需要故障标注，自动标出偏离正常的样本。"""
    from sklearn.ensemble import IsolationForest
    # X: (n_samples, n_features) 传感器矩阵，例如 [温度, 振动, 压力]
    rng = np.random.RandomState(42)
    X = np.vstack([rng.randn(200, 3), rng.randn(10, 3) * 10])  # 200正常+10异常
    clf = IsolationForest(contamination=0.05, random_state=42)
    pred = clf.fit_predict(X)          # 1=正常, -1=异常
    anom_idx = np.where(pred == -1)[0] # 故障样本下标
    return anom_idx


if __name__ == "__main__":
    try:
        idx = isolation_forest_demo()
        print(f"孤立森林检出 {len(idx)} 个异常样本（应≈10）")
    except ImportError as e:
        print(f"[参考代码] 需 sklearn 才能运行演示: {e}")
        print("[参考代码] 文件本身可作为极简实现参考")
