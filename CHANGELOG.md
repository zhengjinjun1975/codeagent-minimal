# Changelog

所有显著变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

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
