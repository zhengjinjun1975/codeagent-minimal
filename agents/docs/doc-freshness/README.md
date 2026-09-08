# doc-freshness（文档新鲜度审计）

**域**：`docs` · **提供**：`doc.anchor` / `doc.stale` · **版本**：0.1.0 · **开源**：是

## 定位

把「文档过期」变成可被程序确定性检测的事实（借鉴 OpenWiki 证据版本化 + preflight）。
扫 Markdown/文本里对源码的代码锚点，逐一校验：文件在不在、行区间内容哈希变没变、符号还在不在。

## 能力

| 能力 | 入参 | 返回 |
|---|---|---|
| `doc.anchor` | `path`(md 或 docs 目录) `root`(仓库根) | 逐锚点审计结果（ok/stale/unresolved/skipped + 当前哈希） |
| `doc.stale` | `path` `root` | P0 清单：`stale`(证据变更) + `unresolved`(证据消失) |

## 支持的锚点语法

- `repo://path/to/file.py#L10-20` —— 行区间锚点（可带 `@sha256:xxxx` 期望哈希）
- `repo://path/to/file.py#L10` / `` `path.py:10-20` `` —— 行锚点
- `` `module.func` `` / `` `module.Class.method` `` —— 符号锚点（校验符号仍定义）
- `` `path/to/file.py` `` —— 文件存在锚点

## 实现

复用根模块 `doc_freshness.py`（纯 stdlib：正则锚点解析 + sha256 内容哈希 + AST 符号校验）。

## 自测

```bash
python agents/docs/doc-freshness/main.py docs --root .
python -m pytest tests/test_optimization_p0_atoms.py -k doc
```
