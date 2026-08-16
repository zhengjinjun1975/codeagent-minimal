# task-state 原子（open_source:true）

**复用（零改动核心）**：`E:/optmem/taskstate/task_state.py`（外置任务状态，吸收 LoopX 优势）+ `code_agent._ts` 思路。

**能力**：
- `taskstate.track` — 跟踪任务状态/证据/决策点（new/set/ev/gate），跨会话续跑不丢"做到哪/下一步/证据/决策点"。

**数据不出厂**：状态落本地 `_task_state.md`。
