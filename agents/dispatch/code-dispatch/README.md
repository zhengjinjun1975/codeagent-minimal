# code-dispatch 原子（open_source:true）

**复用（零改动核心）**：`dispatch_template`（5段派单）+ `estimate_budget`（自适应预算）+ `back_to_back_check`（背靠背验证）+ `check_workspace_conflicts`（并行冲突防护）。

**能力**：
- `dispatch.template` — 派单5段模板（背景/目标/约束/红线/产出）
- `dispatch.budget` — 自适应预算启发式（不调模型）
- `dispatch.verify` — 背靠背验证（子代理自报不可信，需独立复跑证据）
- `dispatch.conflict` — 并行冲突防护（同文件写冲突检测）

**数据不出厂**：全部纯规则/启发式，零 LLM。
