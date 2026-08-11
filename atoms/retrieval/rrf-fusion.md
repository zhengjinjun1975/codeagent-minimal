---
title: "代码原子：RRF 结果融合"
tags: "[code-atom, 数据检索, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 数据检索
source: 混合检索通用实践（RRF 标准算法）
source_license: CC-BY / 算法公有
status: draft
---

# 代码原子：RRF 结果融合

> 从混合检索通用实践提炼的最小可复用代码片段。遵循极简代码原则（Ponytail 阶梯）：标准库优先、一行能搞定就一行、只写最少必要代码。

## 解决的问题
把多路检索结果（如 BM25 稀疏 + 向量稠密）按排名而非分数融合成单一有序列表——分数尺度不同无法直接相加，RRF 用排名倒数规避。

## 极简代码（核心）

```python
def rrf(lists, k=60):
    """lists: [ [docA, docB, ...], ... ] 多路已排序结果；返回融合后的 doc 排序。"""
    scores = {}
    for rl in lists:                       # 每路结果
        for rank, doc in enumerate(rl):
            scores[doc] = scores.get(doc, 0) + 1.0 / (k + rank + 1)  # 排名越高贡献越大
    return sorted(scores, key=scores.get, reverse=True)

# 例：稀疏(BM25) + 稠密(向量) 两路检索结果融合
sparse = ['A', 'B', 'C', 'D']
dense  = ['C', 'A', 'E']
print(rrf([sparse, dense]))   # ['A','C','B','E','D']：A、C 因两路都靠前而排最前
```

## 使用要点
- 常数 `k=60` 是 RRF 论文建议值，控制「排序位置」的权重衰减速度。
- 输入必须是**已按各自分数排序**的文档列表，RRF 只认顺序、忽略绝对分数，天然免疫不同尺度。
- 可加权：给每路乘系数，如 `1.0/(k+rank+1) * weight`，凸显更可信的检索源。

## 来源
- 项目：混合检索通用实践（RRF，Reciprocal Rank Fusion，Cormack et al.）
- 许可：算法为公有领域知识，实现原创
- 提炼自：RRF 融合公式 `score = Σ 1/(k + rank)`。

## 何时用 / 何时别用
- ✅ 用：合并稀疏+稠密等多路异构检索结果，做混合检索时无需统一分数尺度。
- ❌ 别用：只有单路结果（无融合必要）；各结果分数本身可比较且已归一化（直接加权求和即可，RRF 会丢分数信息）。
