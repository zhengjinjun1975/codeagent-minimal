# Changelog

所有显著变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

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
