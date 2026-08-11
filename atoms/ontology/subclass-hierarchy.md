---
title: 子类层级提取 (subClassOf)
tags: [本体建模, 知识图谱, rdflib, 层级, subclass]
type: code-atom
domain: 本体建模
source: rdflib + networkx
source_license: BSD-3-Clause
status: draft
---

# 子类层级提取 (subClassOf)

## 解决的问题
从本体里抽出 `rdfs:subClassOf` 关系，建"类→父类"层级，做祖先查询、父子分类、类型推断。

## 极简代码
```python
from rdflib import Graph, URIRef, RDFS

g = Graph()
g.parse("ontology.nt", format="nt")

parent = {}                                   # 类 -> 直接父类集合
for s, o in g.subject_objects(RDFS.subClassOf):
    parent.setdefault(s, set()).add(o)

def ancestors(cls):                           # 递归取全部祖先
    return {cls} | {a for p in parent.get(cls, ())
                    for a in ancestors(p)}

def is_subclass(c, sup):                      # 类型归属判断
    return sup in ancestors(c)

# 示例：全部直接子类
print({str(c).split("#")[-1]: {str(p).split("#")[-1] for p in ps}
       for c, ps in parent.items()})
```

## 使用要点
- `RDFS.subClassOf` 是 `rdflib` 预定义谓词，等价于显式 `URIRef(RDFS + "subClassOf")`。
- 层级可能有环/多继承，`ancestors` 递归前应加 `visited` 集合防死循环。
- 用 networkx 建 `DiGraph` 后 `nx.ancestors(G, cls)` 可直接拿全部祖先(见 `networkx-graph-traverse` 原子)。
- 本体若同时有 `rdf:type`(实例归属)，可据此把"类层级"和"实例分类"分开处理。

## 来源
本地 `E:/domain-libs/ontology/rdflib`、`E:/domain-libs/ontology/networkx`；类型索引思路源自 `E:/open-source/factory-ontology-kit/codes/graph_rag.py`(value_index 分类)。

## 何时用 / 别用
- **用**：本体含 TBox(类定义)，要回答"某类属于哪个大类/父子关系"。
- **别用**：只有实例数据无类定义(无 subClassOf)；或要完整 OWL 推理——交给推理器。
