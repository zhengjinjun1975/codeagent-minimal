# method-impact（方法级影响分析）

**域**：`impact` · **提供**：`impact.method` / `impact.kind` · **版本**：0.1.0 · **开源**：是

## 定位

从「文件级 1 跳反向 import」升级为**方法级传递反向可达**：改一个函数/方法，精确列出会波及的
间接调用者，并给出**最短传播路径**（借鉴 code-graph-rag 方法级 CALLS/INSTANTIATES/INHERITS 边）。

## 能力

| 能力 | 入参 | 返回 |
|---|---|---|
| `impact.method` | `path`(目录) `symbol`(fqn) `transitive` `max_depth` | `impact`(波及方列表) `paths`(最短传播路径) `entities/edges` |
| `impact.kind` | `path` `caller` `to` | `kind`(边类型: CALLS/INSTANTIATES/INHERITS/REFERENCES) |

## 实现

复用根模块 `method_impact.py`（纯 stdlib AST），本原子只加壳包 `{ok,data}` 信封，核心零改动。

## 自测

```bash
python agents/impact/method-impact/main.py <目录> --symbol module.func --transitive
python -m pytest tests/test_optimization_p0_atoms.py -k method
```
