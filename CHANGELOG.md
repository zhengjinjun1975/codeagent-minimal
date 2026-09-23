# Changelog

所有显著变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [0.6.1] - 2026-09-23

### 与母体对齐到同一口径 + 边界修复
- **口径对齐**：补齐 3 个核心原子（`event-log` / `secret-vault` / `session`），本库现为 **39 核心原子 / 161 能力**（Lab 加载 41 = 39 核心 + 2 扩展），与母体完全一致；前端与后端接线文件逐字节相同（单一真相源在母体，本库单向同步）。
- **仓库只保留 Lab 一个前端**（`web/` 已在前一版移除）：它同时是「用预设·点一下就跑」与「自己组装·拖拽搭流程」的入口。
- **16 套预设编排（不再有"单原子卡"）**：全量代码审查 / 快速体检 / 修 bug / 写新功能 / 测试补强 / 影响面分析 / 重构精简 / 质量闸门 / 交付验收 / 按任务自动选链 / 深挖 Bug 根因 / 自我进化 / 外部工具·模型分工 / 前端冒烟 / 长任务续跑 / 上手新项目。每张卡 = 一条完整多阶段编排（并行分支 + 按需循环/门控 + 交付），覆盖 39 原子 / 161 能力零遗漏。
- **画布**：按依赖自动布局（主干从左到右横排、并行原子竖着叠、宽扇拆子列）；绘图区自适应内容 + 更大的节点与字号；循环以橙色虚线反馈弧画出（回环看得见）。画布标题栏「编排图」下拉可载入任一套编排或空白自搭，运行名跟随所选编排。
- **边界修复（真机端到端抓出）**：`deliver.report` 缺 `chain`/`outputs` 时不再把整条编排判红——容器型入参缺省即为空（如实表示"没有上游输入"，不猜测拼装）；交付原子的能力签名同步给出默认值。
- **不静默失败**：载入某套编排失败会在画布上显示真实原因（画布保持原样），选择器里单张卡构图失败也不影响其它卡。

### 验证
- 真机端到端（Lab 8098，单进程干净实例）：代码审查 ✅ → 安全扫描 ✅ → 死代码 ✅ → 门控放行 ✅ → 交付报告 ✅，`ok=1`；且**故意不提供** `chain`/`outputs`，边界容错同时验证。
- `pytest -q` 全量；门 `tests/test_capability_consistency.py` 与 `tests/test_security_boundary.py` 一致通过；按 CI 同法做干净检出对等验证。

## [0.6.0] - 2026-09-23

### 单一前端：删掉第二套前端，Lab 成为唯一入口（点一点用预设 / 拖一拖自己搭）
- **删除 `web/`（最小运行前端 + 其 REST 服务）**：原有 9 个动作（status/review/test/chain/guard/evolve/project/git/evals）不再有独立前端，全部并入 Lab，能力一个不丢（`lab/web_viz.py` 的动作口 + 门内白名单校验）。
- **Lab 新增「预设模式」（默认首页）**：11 张预设卡（全量审查 / 快速体检 / 修 bug / 写新功能 / guard 链 / 自动选链 / 黑箱调试 / 交付验收 / 题集评测 / Git 巡检 / 空白画布自己搭），点一下即跑；卡片显示真实「上次结果」，运行时右侧逐步 ✔/⏳/○ 变亮，结果统一落一处（含失败原因）。
- **Lab 组装模式更顺手**：左侧原子「点一下就入编排」并按顺序自动串线（也可拖拽/连线/改参）；预设与组装不割裂——卡片点「✎ 改图」把整张图铺到画布再改。
- **API**：新增 `POST /api/action`（单步动作口，与 git 只读同源）。未知动作 / git 写动作 / chain 缺任务均明确拒绝，不静默。
- **接线同步**：`examples/codeagent_toolkit.http_tool` 与 `examples/web_api_client.py` 改连 Lab（`127.0.0.1:8087`，`/api/action`）；README / INTEGRATION_GUIDE 改为 Lab 单前端说明。
- **门改造（只加不松）**：门由「扫 `web/server.py` 命令面」改为「扫 `lab/web_viz.py` 动作口」（需与白名单一一相等 + `run_action` 执行体存在）；安全边界测试改为离线断言 `lab_config.resolve_target` 的越界拒绝 + 动作口白名单（**新增**未知动作 / git 写动作 / chain 缺任务三项拒绝断言）。

### 验证
- 门：`scripts/verify_capability_consistency.py` / `tests/test_capability_consistency.py` 全绿；`tests/test_security_boundary.py` 8 项全过。
- 真机：Lab（8087）预设卡真跑（全量审查 15 节点图 / 快速体检 2 步 / 题集 / git 等），逐步进度与结果区均真实更新；组装模式点原子自动入图 + 自动串线实测通过；零 JS 报错。

## [0.5.0] - 2026-09-23

### 与母版能力同步（11 个原子升版/入册 + 运行前端 11 预设 + 题集）
- **原子 34 → 36、能力 118 → 145**（`codeagent.py status --json` 实测：`degraded=[]`、`conflicts=[]`）。
- 升版：`code-test` 0.3.0（+`test.project` 项目级验收）、`code-runloop` 0.3.0（+`code.patch`）、`command-approvals` 0.2.0（+`approval.orchestrate`）、`context-compact` 0.5.0（+6 能力，预算**真裁到预算内**）、`code-dispatch` 0.4.0（+3 能力）、`doc-freshness` 0.2.0（+`doc.draft`）、`guard` 0.2.0（+3 能力）、`mcp-client` 0.2.0（+`mcp.guard`）、`model-fallback` 0.2.0（+4 能力）、`process-sandbox` 0.3.0、`task-state` 0.2.0。
- 同步支撑模块：`capability_scope.py`、`mcp_guard.py`、`context_harness.py`、`chain_selector.py`、`verify_gate.py`、`cascade_router.py`、`harness_guard.py`，并更新 `approval_policy.py` / `doc_freshness.py` / `security_scan.py`。
- **脱敏**：`llm.py` 的 key 兜底路径与提示不再指向内部目录（改用 `~/.codeagent/.env`）；`task-state` 注释去掉内部名。新增文件内部名复扫 = 0。
- **文档计数统一**：README / ATOMS_GUIDE（索引表 16 → **36 行**（+`git-ops`））/ INTEGRATION_GUIDE / PROMOTION / CAPABILITY 全部归 36 原子 / 145 能力；新增 `docs/POSITIONING.md`（与开源生态的真实对照：8 个编码代理类 + 20 个质量治理工具类，优势 8 条 / 短板 6 条逐条显性）。
- **新增门** `tests/test_capability_consistency.py`（7 项：registry 自洽 / README 计数 / 白名单文档计数 / 指南索引集合 / 前端中文名覆盖 / server 命令白名单 / 分辨力自检）——**上线即抓出多处真实不一致**（指南 3 处计数、CAPABILITY.md 与前端中文名表各 1 处），已修。
- **新增原子 `git-ops` 0.1.0**（`git.status/diff/log/commit/branch`；只做日常操作，**不暴露 `push`/`reset --hard`/`force`**，`commit` 为受控写）→ 原子 **36**。
- **题集 + 运行前端同步**：`evals/cases/*`（10 题，按 `pass^k` 口径）+ `scripts/run_evals.py` / `scripts/verify_evals.py`；用例路径改**相对仓库根**（跨机可移植）；`web/` 运行前端同步为 **11 个预设动作**（新增「运行 git / 运行项目验收 / 跑题集」）。

### 验证
- `python -m pytest -q`：**195 passed**；`codeagent.py status --json`：36 原子 / 145 能力 / `degraded=[]` / `conflicts=[]`。
- 题集：`python scripts/run_evals.py` → **10/10 通过（pass_k 1.00）**；运行前端（8080）真浏览器实测 11 个预设动作全通（`运行 git` 返回真实分支/改动、`运行项目验收` 返回真实快照、`跑题集` 10/10），零 JS 报错。

## [0.4.1] - 2026-09-08

### 修复与 CI

- **atomicity 豁免独立原子**：`atomicity.registry` 现认识 `standalone` 原子（独立运行、不进 fusion registry 的原子，如 code-runloop），不再将其误报为「磁盘已有但未注册」，全量 pytest 由 179/1 → **180 全绿**。`code-runloop/manifest.json` 标记 `standalone:true`。
- **补 GitHub CI**：新增 `.github/workflows/ci.yml`（ubuntu + `pytest tests/`），main 推送自动跑全量单测，验证纯标准库零依赖。

## [0.4.0] - 2026-09-08

### 一体化完整版（带前端）开源

本版本 = **code-agent-lab 带前端完整版**并入开源仓库 codeagent-minimal，替代旧「纯内核 29 原子 + 闭源编排」形态，`git clone` 即用。

- **完整原子集**：统一运行时注册 **34 原子 / 能力全绿**（`codeagent.py status --json`：`degraded=[]`、`conflicts=[]`）。在纯内核基础上补齐 `code-implement`（代码实现/写码）、`browser-smoke`（浏览器冒烟）、`doc-freshness`（文档新鲜度审计）、`deadcode`（死代码检测）、`method-impact`（方法级影响分析）等原子。
- **Lab 编排 + 浏览器前端开源**：`lab/`（原子库可视化编排系统，含 `lab/frontend`）与 `web/`（最小运行前端 + REST）全部随仓库分发，纯标准库 `http.server` 起服务：
  - `python lab/lab_app.py --port 8087` → http://127.0.0.1:8087（编排画布 / 代码浏览 / 黑箱调试 / 报告 / 对话）
  - `python web/server.py --port 8080` → http://127.0.0.1:8080（状态 / review / test / guard / chain / evolve + REST）
- **写码引擎 LLM 通道纯标准库化**：`code_agent_engine` 的 LLM 调用由 `requests` 改写为原生 `urllib`（`_http_post_json`，无代理直连、JSON 解析等价），彻底移除最后一个第三方 import。
- **代码自包含化**：去除对个人环境/外部目录的硬编码依赖（obsidian_ops / env 门控、内部盘符路径 env 化等），运行时指向自身仓库目录，克隆即用。
- **零依赖零三方**：全部纯 Python 标准库，无需 `pip install` / venv；默认数据不出厂，仅绑定本机。

> 历史追溯：0.2.x–0.3.4 为**纯内核原子化迭代**，完整历史见 git。
> 0.3.4 = 33 原子纯内核版（新增 browser-smoke）；0.3.0 = 原子化重构（16 原子 + 统一运行时/入口 + 组装链）；0.2.x = 原子化重构 P0（atomic_base + agent_loader + registry）；0.1.x = 版本降维与复用优先·极简落地；旧版 v2.x 历史不在此记录，见 git。
