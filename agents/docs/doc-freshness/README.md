# doc-freshness（文档新鲜度审计 + P0-1 建议性增量重写草案）

**域**：`docs` · **提供**：`doc.anchor` / `doc.stale` / `doc.draft` · **版本**：0.2.0 · **开源**：是

## 定位

把「文档过期」变成可被程序确定性检测的事实（借鉴 OpenWiki 证据版本化 + preflight）。
扫 Markdown/文本里对源码的代码锚点，逐一校验：文件在不在、行区间内容哈希变没变、符号还在不在。
在只读审计之上，P0-1 新增「建议性增量重写」：对 stale 条目产**补丁草案**（默认 off，
独立能力，绝不直接改文档），并按 managed/手写两区纪律只针对机器可重建区提。

## 能力

| 能力 | 入参 | 返回 |
|---|---|---|
| `doc.anchor` | `path`(md 或 docs 目录) `root`(仓库根) | 逐锚点审计结果（ok/stale/unresolved/skipped + 当前哈希） |
| `doc.stale` | `path` `root` | P0 清单：`stale`(证据变更) + `unresolved`(证据消失) |
| `doc.draft` | `path` `root` [`out`(可选目录)] | 补丁草案（只读）：managed 区 stale → `drafts`；手写区/无 marker → `guarded`(保护) |

## managed/手写两区纪律（对齐 OpenWiki AGENTS.md marker 协议）

- **managed 区（机器可重建）**：HTML 注释包起的 `<!-- <LABEL>:START -->…<!-- <LABEL>:END -->` 区域
  （如 `<!-- DOC:START -->…<!-- DOC:END -->`、OpenWiki 的 `<!-- OPENWIKI:START -->…`）。
- **手写区（保留）**：marker 块之外的部分，`doc.draft` **绝不建议覆盖**。
- `doc.draft` 产出的草案只针对 managed 区内的 stale 条目；stale 落在手写区或无任何 managed
  marker 时一律进 `guarded`（保护），只报不改。草案是待 CodeAgent/人 review 的 JSON，
  源文档始终只读；仅当显式传 `out` 目录时才把草案 JSON 落盘到该目录。

## 支持的锚点语法

- `repo://path/to/file.py#L10-20` —— 行区间锚点（可带 `@sha256:xxxx` 期望哈希）
- `repo://path/to/file.py#L10` / `` `path.py:10-20` `` —— 行锚点
- `` `module.func` `` / `` `module.Class.method` `` —— 符号锚点（校验符号仍定义）
- `` `path/to/file.py` `` —— 文件存在锚点

## 实现

复用根模块 `doc_freshness.py`（纯 stdlib：正则锚点解析 + sha256 内容哈希 + AST 符号校验）。
只读审计内核（`audit_file`/`audit_dir`/`extract_anchors`/`audit_anchor`）零改动；P0-1 新增
只读辅助函数 `managed_zones`/`zone_of`/`suggest_drafts` 供 `doc.draft` 调用。

## 自测

```bash
python agents/docs/doc-freshness/main.py docs --root .
python -m pytest tests/test_optimization_p0_atoms.py -k doc
```
