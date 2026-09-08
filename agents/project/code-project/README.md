# code-project 原子（open_source:true）

**复用（零改动核心）**：`load_project / scan_issues / analyze_project` 思路 + `review.py._static_analyze`。

**能力**：
- `project.load` — 项目快照（文件 + 类/函数/imports）
- `project.scan` — 扫描问题清单（每文件静态分析）
- `project.analyze` — 综合分析（快照 + 问题数 + 概况）

**数据不出厂**：纯本地 AST，零 LLM。
