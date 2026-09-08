# code-plan 原子（open_source:true）

**复用（零改动核心）**：`dispatch_template` 派单5段模板（背景/目标/约束/红线/产出）+ 预算启发式。

**能力**：
- `plan.think` — 派单5段模板任务拆解，输出可派单方案 + files_needed + 约束链（零 LLM）
- `plan.gen` — 按方案产出代码：透传 `code`（数据不出厂）或从 `spec` 生成真实骨架

**数据不出厂**：think/gen 均纯本地启发式，不调云端。
