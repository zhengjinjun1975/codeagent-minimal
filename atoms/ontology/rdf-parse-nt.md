---
title: RDF N-Triples 解析
tags: [本体建模, RDF, rdflib, 解析]
type: code-atom
domain: 本体建模
source: rdflib
source_license: BSD-3-Clause
status: draft
---

# RDF N-Triples 解析

## 解决的问题
把 N-Triples（`<s> <p> <o> .` 每行一条三元组）解析成内存 RDF 图，供遍历/查询/建邻接表。

## 极简代码
```python
from rdflib import Graph, URIRef, Literal

g = Graph()
g.parse("ontology.nt", format="nt")   # format="nt" 或 "n3"/"turtle"/"xml"

# 遍历全部三元组
for s, p, o in g:
    print(s, p, o)

# 查询某实体所有 (属性, 值)
s = URIRef("http://ex.com/Equipment_E1")
for p, o in g.predicate_objects(s):
    print(p.split("#")[-1], o)

# 拿到 URI 局部名；Literal 是字面值(数字/字符串)
tail = lambda uri: str(uri).split("#")[-1]
```

## 使用要点
- `g.parse()` 按需指定 `format`；`nt`/`n3`/`turtle`/`xml` 均可。
- 三元组里 `s`/`p` 是 `URIRef`，字面值 `o` 是 `Literal`；判断实体用 `isinstance(o, URIRef)`。
- `g.triples((s, p, None))` 可按任意坐标过滤，比 `predicate_objects` 更通用。
- 大文件用 `g.parse(..., format="nt")` 流式即可；纯标准库版本可用 `line.split()` 手撕（见 ontology_qa_v3.parse_nt）。

## 来源
`graph_rag.py`/`ontology_qa_v3.py` 手写解析思路见 `factory-ontology-kit`（开源）的 `codes/`。

## 何时用 / 别用
- **用**：本体是 N-Triples/Turtle，要快速转成图、跑 SPARQL 或建邻接表。
- **别用**：只想要「实体↔邻居」邻接表(纯标准库更轻)；或要做复杂推理(直接用 SPARQL/OWL)。
