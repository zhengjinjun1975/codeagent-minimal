# code-evolve 原子（open_source:true）

**复用（零改动核心）**：`self_evolve.refine / self_prompt / tdd_loop / _sediment_skill`。

**能力**：
- `evolve.refine` — 四步闭环（观察→归因→精炼→校验·快照回滚）+ 自动沉淀技能
- `evolve.skill` — 沉淀可复用精炼动作进 skills.json（去重）
- `evolve.self_prompt` — 跨会话召回经验（lessons/refinements/skills 命中）
- `evolve.tdd` — 红→绿→回归 TDD 反馈闭环

**数据不出厂**：经验/技能落 `experience/*.json`（本地闭源侧数据目录）。
