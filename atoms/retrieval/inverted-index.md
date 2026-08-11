---
title: "代码原子：倒排索引"
tags: "[code-atom, 数据检索, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 数据检索
source: factory-ontology-kit
source_license: MIT
status: draft
---

# 代码原子：倒排索引

> 从开源项目 `factory-ontology-kit` 提炼的最小可复用代码片段。遵循极简代码原则（Ponytail 阶梯）：标准库优先、一行能搞定就一行、只写最少必要代码。

## 解决的问题
用「词 → 文档ID」的正向映射，把「查询含哪些文档」从全表扫描的 O(N) 降为 O(词频)——BM25 等打分模型的底层索引。

## 极简代码（核心）

```python
from collections import defaultdict
import re

def tokenize(text):
    text = text.lower()
    han = re.findall(r'[\u4e00-\u9fff]', text)
    en  = re.findall(r'[a-z0-9_]+', text)
    toks = en + han
    for i in range(len(han) - 1):
        toks.append(han[i] + han[i+1])
    return toks

class InvertedIndex:
    def __init__(self):
        self.index = defaultdict(list)   # 词 -> 文档ID列表
        self.df = defaultdict(int)       # 词 -> 含该词的文档数
    def add(self, doc_id, text):
        for t in set(tokenize(text)):    # set 去重：同文档一词计一次
            self.index[t].append(doc_id)
            self.df[t] += 1
    def postings(self, t):
        return self.index.get(t, [])
    def docs_containing_all(self, q):
        hits = [set(self.postings(t)) for t in tokenize(q)]
        return set.intersection(*hits) if hits else set()
```

## 使用要点
- `add` 用 `set(tokenize(text))` 去重，`df` 才等于「含该词的文档数」而非词频。
- `postings` 返回文档ID列表供 BM25 遍历打分；`docs_containing_all` 直接做 AND 检索。
- `defaultdict` 让不存在的词自动落到空表，避免 KeyError。

## 来源
- 项目：factory-ontology-kit（工厂本体问答系统）
- 许可：MIT
- 提炼自：`codes/bm25_retrieval.py` 的 `df`/`doc_tokens` 索引结构，抽成独立可复用原子。

## 何时用 / 何时别用
- ✅ 用：批量文本的关键词定位、AND 检索、为稀疏打分模型提供候选集。
- ❌ 别用：百万级文档（内存索引需外存/压缩，交给 Lucene/ES）；需要短语、模糊、通配查询（需额外结构）。
