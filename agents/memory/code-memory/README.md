# code-memory 原子（open_source:true）

**复用（零改动核心）**：`self_evolve.remember / _load / _save / self_prompt` + 语义记忆检索思路。

**能力**：
- `memory.save` — 经验沉淀进 `lessons.json`（跨会话复用）
- `memory.recall` — 跨会话召回命中经验（lessons/refinements/skills）
- `memory.sediment` — 技能沉淀进 `skills.json`（去重）

**数据不出厂**：经验落本地 `experience/*.json`（闭源侧数据目录）。
