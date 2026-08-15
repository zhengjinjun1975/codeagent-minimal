---
title: NetworkX 建图与图遍历
tags: [本体建模, 知识图谱, networkx, 图遍历]
type: code-atom
domain: 本体建模
source: networkx
source_license: BSD-3-Clause
status: draft
---

# NetworkX 建图与图遍历

## 解决的问题
把三元组(RDF/邻接表)建成内存图，做 BFS 邻域扩展、最短路径、连通判断——图检索/推理的底座。

## 极简代码
```python
import networkx as nx

G = nx.Graph()                     # 有向关系用 nx.DiGraph()
G.add_edge("E1", "E2", rel="partOf")
G.add_edge("E2", "E3", rel="hasPart")
G.add_edges_from([("E1", "E4"), ("E2", "E4")])

# BFS 从源节点向外扩展邻域(子图提取)
for u, v in nx.bfs_edges(G, source="E1", depth_limit=2):
    print(u, "->", v)

# 最短路径 / 是否可达
print(nx.shortest_path(G, "E1", "E3"))
print(nx.has_path(G, "E1", "E4"))          # True

# 单跳邻居(取属性)
print(list(G.neighbors("E1")))
print(G["E1"]["E2"]["rel"])                # 边属性
```

## 使用要点
- 无向 `Graph()` 双向往返；有向关系(如 partOf)用 `DiGraph()`，注意方向。
- 从 RDF 建图：`for s,p,o in g: G.add_edge(s,o,rel=tail(p))`（见 `rdf-parse-nt` 原子）。
- 大图 BFS 用 `bfs_edges`+`depth_limit`，避免全图遍历；`graph_rag.py` 的手写 BFS(deque)思想一致。
- 图退化到"实体→邻居"小规模时，纯标准库 `defaultdict(list)` 更轻，不必上 networkx。

## 来源
图检索设计源自 `factory-ontology-kit`（开源）的 `codes/graph_rag.py`。

## 何时用 / 别用
- **用**：需要最短路径、连通分量、多种图算法，或图会变大/反复遍历。
- **别用**：只存邻接表且只做一层邻居查询——标准库 `defaultdict` 已足够。
