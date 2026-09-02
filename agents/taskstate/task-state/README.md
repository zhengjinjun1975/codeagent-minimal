# task-state 原子（open_source:true）

**复用（零改动核心）**：外置任务状态模块（吸收 LoopX 优势）+ 内建 `_ts` 任务跟踪思路。

**能力**：
- `taskstate.track` — 跟踪任务状态/证据/决策点（new/set/ev/gate），跨会话续跑不丢"做到哪/下一步/证据/决策点"。

**数据不出厂**：状态落本地 `_task_state.md`。
