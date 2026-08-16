# code-review 原子（open_source:true）

**复用（零改动核心）**：`review.py._static_analyze / review_file / _dep_enrich`、`dep_audit.build_graph`。

**能力**：
- `codereview.review` — 静态审查 + 依赖图感知 + 复用建议
- `codereview.design / layout / content` — 前端审美/布局/内容轻量规则集

**数据不出厂**：纯本地静态分析，`use_llm=False` 默认离线。
