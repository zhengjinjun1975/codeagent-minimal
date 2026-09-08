# CodeAgent Minimal — 一体化开源的本地代码智能体（原子 + Lab 编排 + 前端）

> ⭐ **觉得有用就给我们一个 Star** —— 你的 Star 让这个项目被更多人看见，支持我们持续迭代。
> [![GitHub stars](https://img.shields.io/github/stars/zhengjinjun1975/codeagent-minimal?style=social)](https://github.com/zhengjinjun1975/codeagent-minimal)

> **v0.4.0 · 一体化完整版** — 把代码审查 / 测试 / 依赖漏洞 / 变异 / 模糊 / 回归 / 自进化 / 写码等能力实现为 **34 个可独立运行、可任意组装的原子智能体**，由统一运行时编排、统一入口 `codeagent` 驱动，并随仓库开源 **Lab 编排平台（`lab/`）+ 浏览器前端（`web/`）**，`git clone` 即用。
> **一体化开源**：本仓库 = 34 原子 + 统一运行时/入口 + Lab 编排（`lab/`，含前端 `lab/frontend`）+ 最小运行前端（`web/`），全部 Apache-2.0，零第三方依赖。

[![License](https://img.shields.io/badge/License-Apache-2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.4.0-blue.svg)](CHANGELOG.md)
[![GitHub stars](https://img.shields.io/github/stars/zhengjinjun1975/codeagent-minimal)](https://github.com/zhengjinjun1975/codeagent-minimal)
[![GitHub forks](https://img.shields.io/github/forks/zhengjinjun1975/codeagent-minimal)](https://github.com/zhengjinjun1975/codeagent-minimal)

**它能帮你**：一键代码审查、自动化测试、依赖漏洞扫描、变异测试、模糊测试、回归验证、代码实现——34 个原子智能体任意组装，配合本地可视化 Lab 编排与浏览器前端，零第三方依赖，数据不出厂。

---

## 文档导航

| 文档 | 说明 |
|------|------|
| **[docs/PROMOTION.md](docs/PROMOTION.md)** | 开源宣传文案：场景痛点 / 方法论 / 诚实边界（为什么是「极简 + 本地 + 原子化」） |
| **[docs/CAPABILITY.md](docs/CAPABILITY.md)** | 能力域与优势：8 大能力域 + 5 大优势 + 定位一句话 |
| **[docs/ATOMS_GUIDE.md](docs/ATOMS_GUIDE.md)** | 原子逐个指南：能力 / 入参 / 返回信封 `{ok,data}` / 示例代码（真实信封核实），含独立运行 + 统一入口两种调用 |
| **[docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md)** | 对接传统框架（LangChain / CrewAI / AutoGen / OpenAI Agents SDK / Claude Code）：作为 Tool / 子代理节点 / 图编排 / REST / CLI 集成，边界为本地原子 + 数据不出厂 + 一体化开源 |
| **[docs/SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md)** | 安全加固与边界防护：沙箱 / 子进程 / 路径 / 输入校验 / 数据不出厂 |
| **examples/** | 已跑通对接示例：`codeagent_toolkit.py`（统一接入层）、`langchain_integration.py`、`crewai_integration.py`、`graph_orchestration.py`（子代理节点+图编排）、`web_api_client.py`（REST） |
| **架构总览** | ![系统架构](docs/architecture.svg) |

> 用户可**独立使用**（读 ATOMS_GUIDE 直接用 34 原子）、**独立集成**（读 INTEGRATION_GUIDE + examples 接入任意框架），或**开箱用完整版**（一条命令起 Lab 前端可视化编排）。

---

## 定位

**给需要审代码但不写代码的人：本地代码审查 / 安全 / 测试智能体，纯标准库零依赖，数据不出厂，原子化可组装，中小企业工厂代码工具。**

- **原子化**：每个能力是一个「原子智能体」（`AtomicAgent` 基类），具备统一接口 `call / run / describe`、统一生命周期 `discovered → loaded → ready`、统一 `{ok, data}` 结果信封、失败自动降级 `{ok:false, degraded:true}`。
- **可组装**：原子之间通过能力声明（`provides` / `depends_on`）由加载器做拓扑排序 + 冲突检测，按需拼成任意组装链（如 `think→gen→review→test→evolve`、`guard = review+dep-scan+fuzz`）。
- **一体化开源**：原子内核、统一运行时、Lab 编排平台（含浏览器前端）、最小运行前端全部随仓库开源，`git clone` 后本地即用。
- **零依赖**：纯 Python 标准库运行，开箱即用，不装任何包。
- **数据不出厂**：默认本地处理，云端 LLM / 远端 OSV 需显式开启。

### 8 大能力域

| 能力域 | 说明 |
|--------|------|
| **代码审查**（核心） | 静态分析（语法/复杂度/结构）+ 可选 LLM 审查，0–100 分；缺陷根因库 + 规则反哺（越用越准） |
| **安全扫描** | 10 维度漏洞（SQL 注入/反序列化/命令注入/SSRF/弱哈希/硬编码密钥/IDOR 等），危险函数 + 密钥模式 + 严重度分级 |
| **测试** | 测试选择（精确函数名）+ 测试覆盖分析 |
| **审批与权限** | 高危命令审批（细粒度权限）+ 命令执行策略 |
| **沙箱安全执行** | 进程降权沙箱 + 路径守卫（跨平台防穿越/盘符逃逸） |
| **会话与上下文** | 上下文压缩（预算裁剪）；任务状态跨会话跟踪 |
| **模型降级链** | 本地 ↔ 云端切换 + 本地优先（数据不出厂） |
| **领域审查与实现** | 原子化断链（跨仓库 import/依赖拓扑）+ 本体数据质量 + 工业阀门规则 + 极简风格 + 代码实现（code-implement） |

### 5 大优势

- **纯标准库零依赖**：无第三方依赖，可独立部署（无 venv / 无 pip install），契合怕大部署/依赖的中小企业工厂；
- **本地独立 + 数据不出厂**：全本地运行，代码/数据不出厂，契合工厂/甲方安全诉求（核心卖点）；
- **原子化可组装 + 一体化**：34 原子按需取用、可组装，配套 Lab 编排与前端可视化，极简可用 + 灵活扩展；
- **代码审查/安全/测试深度**：深度审查/安全扫描/测试，专注代码质量（差异化优势）；
- **极简不炫技**：手搓、极简、可独立部署，门槛低，中小企业工厂可落地。

> **诚实边界**：这是轻量本地代码智能体，不是大而全的编码全家桶——它不追求覆盖大型分布式系统的全套深度验证；代码实现（code-implement）通过本仓库自包含的写码引擎完成，默认本地执行。完整宣传与边界见 [docs/PROMOTION.md](docs/PROMOTION.md)。

### 两种用法(先分清层级)

**一、当作可复用原子库(附属能力层, 嵌入你已有的 agent)。** 34 个能力原子是自包含的子智能体，可独立加载、按能力名调用(`codereview.review` / `security.scan` / `test.run` …)。它们能挂到任何你已有的智能体或框架(LangChain / CrewAI / Claude Code / 自建 agent)下当**附属能力层**，只补单一能力(审查 / 测试 / 安全 / 记忆 / 依赖扫描)，不必为单个能力起整套 Lab。接入见 [docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md) 与 `examples/`。

**二、当作开箱即用的完整代码智能体。** `git clone` 后按本文"用法"区起的命令启动 Lab 编排平台，得到带浏览器前端的可视化本地代码智能体：写码、审查、测试、记忆、自进化一体调度。这是"真正能独立跑起来的体系"，适合不想自己搭 agent 编排、就要一个能用的本地代码智能体的场景。

**诚实说清楚**：原子是能力层，单拎一个只能做单点事(审一个文件 / 扫一次漏洞)；能对话式多步把事办完的，是 Lab 完整体系(原子 + 运行时 + 编排 + 前端)。两者共用同一份 34 原子与零第三方依赖运行时，数据均不出厂，按需取其一即可。

---

## 架构

### 统一运行时 + 统一入口 + Lab 编排 + 浏览器前端

```
                 ┌──────────────────────────────────────────────┐
    codeagent    │            agent_runtime.py                  │
   (统一入口 CLI) │    统一运行时：加载原子 / 能力路由 / 组装链    │
  ─────────────► │              / 权限 / 降级                    │
                 └───────────────┬──────────────────────────────┘
                                 │ 注册 & 调度
         ┌───────────────────────▼────────────────────────┐
         │  agent_loader.py                                │
         │  manifest 校验 / 依赖解析(Kahn拓扑) / 冲突检测   │
         │  / 失败降级                                    │
         └───────────────────────┬────────────────────────┘
                                 │ 34 原子
    ┌──────┬──────┬──────┬──────┬┴─────┬──────┬──────┬──────┐
    │review│ test │ dep- │ fuzz │impact│ code-│ mcp- │ ... │ 34 atoms
    │      │      │ scan │      │      │implem│ client│     │
    └──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘
         │ 组装链示例：
         │  chain  think→gen→review→test→evolve
         │  guard  review + dep-scan + fuzz 协同（安全·质量）

    ┌────────────────────────── 前端 / 编排 ──────────────────────────┐
    │  Lab 编排 (lab/)   python lab/lab_app.py --port 8087           │
    │    http://127.0.0.1:8087   编排画布/代码浏览/黑箱调试/报告/对话   │
    │  最小运行前端 (web/) python web/server.py --port 8080           │
    │    http://127.0.0.1:8080   状态/审查/测试/guard REST 界面        │
    └──────────────────────────────────────────────────────────────────┘
```

### 原子清单（34 原子，与 `registry.json` / 运行时一致）

| 原子 | 版本 | 能力 (provides) |
|------|------|-----------------|
| `command-approvals` | 0.1.0 | `approval.check, approval.classify, approval.resolve, approval.policy` |
| `arch-review` | 0.1.0 | `archreview.layers, archreview.boundary, archreview.surface, archreview.intent` |
| `atomicity-audit` | 0.1.0 | `atomicity.manifest, atomicity.registry, atomicity.breaks` |
| `bug-deep` | 0.1.0 | `bugdeep.model, bugdeep.adv, bugdeep.poc, bugdeep.rule` |
| `code-implement` | 0.2.0 | `code.implement, code.write` |
| `minimalist-style` | 0.1.0 | `minimal.style, minimal.deps, minimal.independent` |
| `context-compact` | 0.1.0 | `context.estimate, context.compact, context.budget` |
| `code-deliver` | 0.1.0 | `deliver.report, deliver.package` |
| `dep-scan` | 0.2.0 | `depscan.scan, depscan.sca, depscan.taint, depscan.osv, depscan.chainbreak` |
| `code-dispatch` | 0.2.0 | `dispatch.template, dispatch.budget, dispatch.verify, dispatch.conflict, dispatch.permission` |
| `doc-freshness` | 0.1.0 | `doc.anchor, doc.stale` |
| `domain-review` | 0.1.0 | `domain.imports, domain.valve` |
| `code-fuzz` | 0.2.0 | `fuzz.gen, fuzz.run, fuzz.property, fuzz.project` |
| `guard` | 0.1.0 | `guard.pre, guard.post, guard.pipeline, guard.check` |
| `deadcode` | 0.1.0 | `deadcode.scan, deadcode.stats` |
| `dep-impact` | 0.1.0 | `impact.analyze, impact.circular, impact.coupling` |
| `method-impact` | 0.1.0 | `impact.method, impact.kind` |
| `llm-router` | 0.2.0 | `llm.generate, llm.review, llm.list_models, llm.registry` |
| `localized` | 0.1.0 | `local.audit, local.chain, local.route` |
| `mcp-client` | 0.1.0 | `mcp.list, mcp.connect, mcp.tools, mcp.call` |
| `code-memory` | 0.1.1 | `memory.save, memory.recall, memory.sediment` |
| `ontology-review` | 0.1.0 | `ontology.chain, ontology.quality` |
| `code-project` | 0.1.0 | `project.load, project.scan, project.analyze` |
| `code-reuse` | 0.1.0 | `reuse.local, reuse.atom, reuse.remote` |
| `process-sandbox` | 0.1.0 | `sandbox.poc, sandbox.exec, sandbox.validate, sandbox.interpreter, sandbox.guard` |
| `security-scan` | 0.1.0 | `security.scan, security.secret, security.govern, security.dim, security.project` |
| `task-state` | 0.1.0 | `taskstate.track` |
| `code-test` | 0.2.1 | `test.gen, test.run, test.tdd, test.snapshot, test.affected, test.select, test.coverage_analysis` |
| `browser-smoke` | 0.1.0 | `browsersmoke.run, ui.smoke` |
| `code-review` | 0.2.1 | `codereview.review, codereview.design, codereview.layout, codereview.content, codereview.lsp, codereview.semantic, codereview.self_eval, codereview.light, codereview.deep` |
| `model-fallback` | 0.1.0 | `model.chain, model.route, model.candidates` |
| `code-evolve` | 0.1.0 | `evolve.refine, evolve.skill, evolve.self_prompt, evolve.tdd` |
| `code-skill` | 0.1.0 | `skill.list, skill.load, skill.export, skill.sediment` |
| `code-plan` | 0.1.0 | `plan.think, plan.gen` |

> 全部 `open_source: true`。注册索引见 `registry.json`（`agent_loader.build_registry()` 可自动重建）。
> **原子状态（与运行时一致）**：`python codeagent.py status --json` 显示注册 **34 原子**，`degraded=[]`、`conflicts=[]`（118 能力全绿，实测见下方回归）。

---

## 带前端（Lab 编排）—— git clone 即用

仓库自包含，克隆后无需任何安装即可起本地可视化前端：

```bash
# Lab 编排前端（编排画布 / 代码浏览 / 黑箱调试 / 报告 / 模型配置 / 对话）
python lab/lab_app.py --port 8087
# 浏览器打开 http://127.0.0.1:8087

# 最小运行前端（状态 / review / test / guard / chain / evolve 一键界面 + REST）
python web/server.py --port 8080
# 浏览器打开 http://127.0.0.1:8080
```

- 两者都是纯标准库 `http.server`，默认只绑定本机 `127.0.0.1`，数据全在本机（SQLite + JSON），无外发流量。
- Lab 前端静态页在 `lab/frontend/`，由 `lab/lab_app.py`（经 `web_viz.serve`）直接托管；最小运行前端静态页在 `web/`。
- 可选 `--token <口令>` 开启访问令牌，未配置时默认封锁跨站请求（详见 `docs/SECURITY_HARDENING.md`）。
- Windows 也可双击根目录 `start_lab.bat` 一键起 Lab（脚本已按脚本所在目录定位，可整体移动）。

> 说明：Lab 的「原子」计数为 **34 个核心原子 + 可插拔扩展原子**（内置示例扩展 `lab-harness` / `lab-loop` 在 `lab/extensions/`）。核心 34 原子以 `codeagent.py status --json` 为准。

---

## 能力

| 能力 | 原子/命令 | 说明 |
|------|-----------|------|
| **代码审查（语义）** | `review` | 语法 / BUG / 安全 / 架构 / 复用，0–100 分，code/design/layout/content 四模 |
| **语义/LSP 诊断** | `review --lsp` | 本地 LSP server 拉 diagnostics（语法/未定义名/行长）并入评分 |
| **依赖漏洞 SCA** | `dep-scan` | 依赖漏洞扫描 + 污点分析，`--osv` 远端 OSV，默认数据不出厂 |
| **变异测试** | `test` | 冒烟 / 单元 / 边界 / 变异 / 稳定性，红绿回归 |
| **属性模糊** | `fuzz` | 覆盖率驱动属性模糊测试 |
| **回归快照护栏** | `reg-guard` | 快照 / 受影响增量测试选择 |
| **影响分析** | `impact` | 依赖图 / 环 / 耦合 / 方法级影响 |
| **代码实现** | `implement` | 自包含写码引擎（code-implement），本地生成代码 |
| **自进化** | `evolve` / `evolve-loop` | refine / 自进化 |
| **记忆** | `memory` | 经验沉淀 |
| **MCP** | `mcp` | 工具列表 / 调用 |
| **SKILL** | `skill` | 技能清单 / 加载 / 导出 / sediment |
| **多模型** | `llm` / `models` | 注册表 / 审查 |
| **派单** | `dispatch` | 派单 + 预算 + allow/ask/deny 权限 |
| **安全·质量组装链** | `guard` | review + dep-scan + fuzz 协同 |

---

## 用法

统一入口：`python codeagent.py <子命令>`。34 原子皆可通过子命令独立运行，也可经组装链协同。

### 子命令一览

```bash
# 原子独立运行
python codeagent.py review  path/to/file.py
python codeagent.py test    path/to/file.py
python codeagent.py dep-scan path/to/dir
python codeagent.py fuzz    path/to/file.py --iterations 40
python codeagent.py impact  path/to/module.py
python codeagent.py project path/to/project
python codeagent.py dispatch --task "重构模块A"

# 组装链
python codeagent.py chain --task "修复登录校验漏洞" --code '<code>' --language python
python codeagent.py guard  target/                # review + dep-scan + fuzz 协同

# 能力类
python codeagent.py llm    --action list_models
python codeagent.py mcp    --action tools
python codeagent.py skill  --action list
python codeagent.py memory --findings '{"...": "..."}'
python codeagent.py evolve --task "..." --outcome '{"ok":true}'
python codeagent.py reg-guard --action snapshot
python codeagent.py deliver --chain "think,gen,review,test,evolve"
```

兼容旧入口：`python codeagent.py <target> --review/--test/--dep/--refine/--reuse ...`

通用开关：`--json` 输出机器可读 JSON；`--mode code|design|layout|content` 切换审查维度；`--remote`/`--osv`/`--llm` 显式开启远端能力（默认数据不出厂）。

### 原子使用指南

每个原子的 **能力 / 入参 / 返回 `{ok, data}` 信封 / 示例代码**（基于真实信封核实，含独立运行 + 统一入口双调用）见 **[docs/ATOMS_GUIDE.md](docs/ATOMS_GUIDE.md)**：

```python
# 进程内能力路由（脚本/框架集成）
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)                 # 数据不出厂
res = rt.run_capability("codereview.review", path="sample_target.py", mode="code")
print(res["ok"], res["data"]["score"])             # True 100
```

```bash
# 统一入口（CLI，等价输出）
python codeagent.py review sample_target.py --json
```

### 对接传统框架

把 34 原子作为 **Tool / 子代理节点 / 图编排 / REST / CLI** 接入 **LangChain · CrewAI · AutoGen · OpenAI Agents SDK · Claude Code** 的完整示例与边界见 **[docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md)**，可运行代码在 **examples/**：

```bash
python examples/langchain_integration.py     # LangChain Tool（需 pip install langchain-core）
python examples/crewai_integration.py        # CrewAI Tool（需 pip install crewai）
python examples/graph_orchestration.py       # 子代理节点 + 图编排（零依赖）
python web/server.py --port 8080 &           # 先起 REST 服务
python examples/web_api_client.py            # REST 集成
```

> **边界**：默认**本地原子 / 数据不出厂**（`local_only=True`，剥离云端密钥）；本仓库即**一体化开源完整版**（原子 + Lab 编排 + 前端），克隆即用。

---

## 安装

零依赖，无需安装任何第三方包。要求 Python 3.8+（纯标准库 `ast / re / json / subprocess / importlib / http.server`）。

```bash
git clone git@github.com:zhengjinjun1975/codeagent-minimal.git
cd codeagent-minimal
```

可选（开发便利，非必需，缺失自动降级）：`pytest`、`bandit`、`pyflakes`、`coverage`。

## 看看它跑起来的样子（真实输出）

零第三方依赖，一条命令审查代码质量。干净文件给分，有问题的文件直接揪出漏洞：

```bash
# 1. 审查一个干净文件 → 给出质量评分
$ python codeagent.py review sample_target.py --json
{ "ok": true, "score": 100, "issues": [], "summary": "code 审查 1 文件, 平均分 100" }

# 2. 审查一个有问题文件 → 直接揪出漏洞
$ python codeagent.py review bug_deep.py --json
{ "ok": true, "score": 0, "static_issues": [
    { "severity": "critical", "title": "SQL 注入风险",
      "suggestion": "用参数化查询，避免将变量直接拼进 SQL" },
    { "severity": "major", "title": "不安全的 eval/exec" },
    { "severity": "major", "title": "不安全的反序列化" },
    { "severity": "major", "title": "圈复杂度 14 > 10" } ] }
```

一行命令跑通代码审查、依赖扫描、变异测试、模糊测试、回归验证——34 个原子智能体任意组装，配合 Lab 前端可视化，零依赖，数据不出厂。

## 快速开始

```bash
# 1. 审查一个文件
python codeagent.py review sample_target.py

# 2. 跑测试 harness（冒烟/单元/边界/变异/稳定性）
python codeagent.py test sample_target.py

# 3. 依赖漏洞 + 污点扫描
python codeagent.py dep-scan .

# 4. 一键安全·质量组装链
python codeagent.py guard sample_target.py

# 5. 查看运行时全貌
python codeagent.py status --json

# 6.（可选）起本地可视化前端
python lab/lab_app.py --port 8087    # 打开 http://127.0.0.1:8087
```

---

## 回归

- `C:/Python312/python.exe codeagent.py status --json`：**34 原子 / 118 能力**，`degraded=[]`、`conflicts=[]`（实测）。
- `python -m pytest tests/ -q`：见下方实测结果（`tests/` 覆盖原子加载/ready、组装链 DAG、真实数据逐原子执行、guard 协同、Lab E2E、安全加固）。

---

## 许可

本项目采用 **Apache License 2.0**（见 `LICENSE`，全文 201 行，标准官方文本）。

借鉴声明见 `NOTICE`：

- **借鉴 MIT 项目（自实现，未复制）**：FSoft CodeWiki、OpenCode（MCP/SKILL/CodeMode 概念）、CodeReview/测试 harness 方法论等；Semgrep 思路（LGPL）仅借鉴 source→sink 污点分析**思路**，自实现为纯 stdlib 污点引擎。
- **MIT 兼容 Apache-2.0**：由于未复制任何 MIT 源码，不触发 MIT 通知包含或衍生作品义务。
- **零第三方运行时依赖**：纯标准库；`bandit/pyflakes/coverage/pytest` 为可选开发便利，缺失自动跳过，不构成对交付物任何许可义务。
- **边界**：本仓库 = 34 原子 + 统一运行时/入口 + Lab 编排 + 浏览器前端，全部开源（Apache-2.0）、零第三方依赖。

**合规结论：Apache-2.0 合规**（借鉴 MIT 均为自实现 + NOTICE 标注 + 零第三方运行时依赖）。

---

## 边界与兼容

- **开源边界**：本仓库 = 一体化完整版（34 原子 + agent_runtime + codeagent 统一入口 + `lab/` 编排 + `web/` 前端），全部开源。
- **数据边界**：默认数据不出厂；云端 LLM / OSV 需显式 `--remote / --osv / --llm` 开启。
- **兼容**：`legacy_cli.py` 保留旧命令兼容；统一入口子命令即新推荐用法。
- **环境**：Windows / Linux / macOS，Python 3.8+。

---

## ⭐ 支持这个项目

如果你觉得 CodeAgent Minimal 有用，请花 3 秒给我们一个 **Star**：

[![GitHub stars](https://img.shields.io/github/stars/zhengjinjun1975/codeagent-minimal?style=social)](https://github.com/zhengjinjun1975/codeagent-minimal) ⬅️ 点这里

**Star 的意义**：
- 让更多做代码质量治理的人找到这个项目
- 激励我们持续迭代（更多原子、更强组装链）
- 开源维护者最需要的正向反馈

**反馈与贡献**：有问题提 [Issue](https://github.com/zhengjinjun1975/codeagent-minimal/issues)，有想法提交 PR。