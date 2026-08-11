---
title: "代码原子：BM25 稀疏检索"
tags: "[code-atom, 数据检索, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 数据检索
source: factory-ontology-kit
source_license: MIT
status: draft
---

# 代码原子：BM25 稀疏检索

> 从开源项目 `factory-ontology-kit` 提炼的最小可复用代码片段。遵循极简代码原则（Ponytail 阶梯）：标准库优先、一行能搞定就一行、只写最少必要代码。

## 解决的问题
纯标准库实现 BM25 关键词相关性打分，对一组文本按查询词相关度排序（稀疏检索的经典基线）。

## 极简代码（核心）

```python
import math, re
from collections import defaultdict, Counter

def tokenize(text):
    text = text.lower()
    han = re.findall(r'[\u4e00-\u9fff]', text)   # 中文单字
    en  = re.findall(r'[a-z0-9_]+', text)        # 英文/数字词
    toks = en + han
    for i in range(len(han) - 1):                # 中文双字 bigram
        toks.append(han[i] + han[i+1])
    return toks

class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.n = len(docs)
        self.dl = [len(tokenize(d)) for d in docs]
        self.avgdl = sum(self.dl) / max(1, self.n)
        self.df = defaultdict(int); self.tf = []
        for d in docs:
            c = Counter(tokenize(d)); self.tf.append(c)
            for t in c: self.df[t] += 1
        self.k1, self.b = k1, b
    def score(self, q):
        s = [0.0] * self.n
        for t in set(tokenize(q)):
            if t not in self.df: continue
            idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i, c in enumerate(self.tf):
                tf = c.get(t, 0)
                if tf:
                    s[i] += idf * tf * (self.k1+1) / (tf + self.k1*(1 - self.b + self.b*self.dl[i]/self.avgdl))
        return sorted(range(self.n), key=lambda i: -s[i])
```

## 使用要点
- 中文用「单字 + bigram」分词，无需 jieba，纯标准库即可覆盖中文召回。
- `k1=1.5, b=0.75` 是经典默认；`b` 越大越惩罚长文档。
- 打分只做排序：`s` 是文档绝对分数，不要跨集合比较绝对值。

## 来源
- 项目：factory-ontology-kit（工厂本体问答系统）
- 许可：MIT
- 提炼自：`codes/bm25_retrieval.py` 的 `BM25Index` 类（去掉了 graph 绑定，只留核心打分）。

## 何时用 / 何时别用
- ✅ 用：中小规模文本的模糊/自然语言召回，作为稠密向量检索前的稀疏基线。
- ❌ 别用：海量语料（需分词与索引优化/外部引擎如 whoosh、Elasticsearch）；需要语义近义匹配（请上向量检索）。
