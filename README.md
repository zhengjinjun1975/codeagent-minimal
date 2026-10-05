# CodeAgent Minimal — 给工厂审外包代码的本地代码智能体

> ⭐ 觉得有用就给我们一个 **Star**，支持持续迭代。
> [![GitHub stars](https://img.shields.io/github/stars/zhengjinjun1975/codeagent-minimal?style=social)](https://github.com/zhengjinjun1975/codeagent-minimal)

> **v0.8.1 · 一体化完整版**：一个本地运行的代码智能体，`git clone` 即用，核心只用 Python 标准库、数据不出厂，Apache-2.0（依赖清单见 [requirements-optional.txt](requirements-optional.txt)）。

[![License](https://img.shields.io/badge/License-Apache-2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.8.1-blue.svg)](CHANGELOG.md)

---

## 它解决什么问题

**给中小企业 / 工厂审外包代码的本地代码智能体。**

外包、供应商、临时团队交付的代码，往往是中小企业最大的技术风险来源：质量有没有人把关、代码里有没有带雷没人知道、出了问题找不到人。可中小企业通常请不起专职代码评审，也不敢把核心代码传到第三方云端去审（怕泄密）。把整包代码丢进这个本地工具，它就能当你的「外包代码质检员」：

- **质量审查**：挑出注水、缩水与隐患，给每份代码质量评分（0–100）
- **Bug 修复**：定位问题并给出修复，不必干等外包
- **安全防护**：揪出漏洞、硬编码密钥、危险函数、依赖风险
- **测试**：自动生成并跑单元 / 边界 / 变异测试

**典型场景：**

- **外包交付接收**：项目整包交付回厂，先一键体检再验收。注水代码、隐藏雷当场现形，结果可留作验收依据
- **供应商长期合作**：对方持续迭代的代码质量下滑或夹带私货，定期体检心里有底
- **历史项目交接**：接手多年无人维护的外包系统，源码堆一堆看不懂，先扫一遍安全与质量再决定怎么动
- **安全与合规底线**：交付前扫硬编码密钥、危险函数、依赖漏洞，堵住上线后的安全与合规风险
- **怕泄密的代码**：核心代码不出厂、不上云，本地就能审完，保密要求同样能满足

**为什么落地容易**：核心只用 Python 标准库，工厂一台普通机器、IT 不强也能跑；一键出报告，审完即可对外包提要求或留档追责。

## 30 秒跑起来

只需 **Python 3.8+**，无需安装任何第三方包：

```bash
git clone https://github.com/zhengjinjun1975/codeagent-minimal.git
cd codeagent-minimal

# 方式一：起本地可视化界面 Lab（浏览器打开 http://127.0.0.1:8087）
python lab/lab_app.py --port 8087

# 方式二：命令行直接用（不开界面）
python codeagent.py review 你的代码文件.py   # 代码审查：质量评分 + 揪出隐患
python codeagent.py test   你的代码文件.py   # 自动测试
python codeagent.py dep-scan 代码目录         # 依赖漏洞扫描
python codeagent.py guard   代码目录          # 一键安全·质量检查
```

## 能做什么

| 用途 | 命令 | 说明 |
|------|------|------|
| 代码审查 | `review` | 语法 / bug / 安全 / 架构，0–100 分 |
| Bug 修复 | `implement` | 本地写码引擎定位并修复 |
| 安全扫描 | `dep-scan` / `guard` | 依赖漏洞 + 10 维安全 + 硬编码密钥 |
| 测试 | `test` / `fuzz` | 单元 / 变异 / 模糊测试 |
| 影响分析 | `impact` | 改动会影响哪些模块 |
| 一键检查 | `guard` | 审查 + 安全 + 测试 协同 |

共 **39 个可独立运行、可任意组装的能力原子**，配合本地可视化 Lab 编排与浏览器前端。每个原子的能力 / 入参 / 示例见 [docs/ATOMS_GUIDE.md](docs/ATOMS_GUIDE.md)。

## 调用方式（三种，任选）

**一、命令行**（最快上手）：

```bash
python codeagent.py status --json           # 运行时自省：39 原子 / 161 能力 / degraded / conflicts
python codeagent.py review 你的代码.py       # 代码审查（0–100 分 + 问题清单）
python codeagent.py test   你的代码.py       # 测试闭环
python codeagent.py dep-scan 代码目录        # 依赖漏洞扫描
python codeagent.py guard  代码目录          # 一键安全·质量检查
```

每个原子也能独立跑（各自带 `__main__`，参数见 [docs/ATOMS_GUIDE.md](docs/ATOMS_GUIDE.md)）：

```bash
python agents/code/code-implement/main.py --task "补一个参数校验" --path 目标.py
```

**二、进程内调用**（把能力当函数用，接进你自己的脚本或服务）：

```python
from agent_runtime import get_runtime

rt = get_runtime()                      # 加载一次，39 个原子全部注册
r = rt.run_capability("code.review", path="你的代码.py")
print(r["ok"], r["data"]["score"], r["data"]["issues"])

# 定点替换（零模型调用）：调用方给 old→new
rt.run_capability("code.patch", path="目标.py",
                  edits=[{"old": "a = 1", "new": "a = 2"}])

# 组装链：关键词选链，或直接给一串能力
rt.run_capability("dispatch.chain_select", task="修一下这个 bug 的回归")
```

`run_capability(能力名, **入参)` 是统一入口；每个能力的入参表写在它自己目录的 `manifest.json`（`provides` / `inputs` / `outputs`）。

**三、可视化界面**（原子库 + 编排 + 浏览器前端）：

```bash
python lab/lab_app.py --port 8087      # 浏览器打开 http://127.0.0.1:8087
```

## 接线方式（一个原子怎么接进来、链怎么跑起来）

**目录即原子**。每个原子一个目录 `agents/<域>/<原子>/`，至少两件东西：

- `manifest.json`：声明 `provides`（对外能力名）、`inputs` / `outputs`、`depends_on`；
- `main.py`：实现本体，能力在 `load()` 里注册（`AtomicAgent.register`）。

**加载顺序**由 `registry.json` 的 `order` 决定（39 项）。`agent_runtime.get_runtime()` 按序加载并统一注册，加载不起来的会如实出现在 `degraded`，冲突出现在 `conflicts`，不静默吞。

**组装链**：`agents/dispatch/code-dispatch/` 下备了六条链——改缺陷/修回归、写码/新增功能、代码审查/质量、依赖/影响面、测试/用例、排查/定根因；模板在 `chain_templates.json`，`chain_selector.py` 在规则层按关键词选链（不花模型的钱），命不中才落回默认写码闭环。链里前一步的产物会作为后一步的输入。

**接单线**：`docs/intake/tasks.md` 是一本任务队列，`scripts/intake_poll.py` 每 15 分钟领一次单。领单先落「进行中」再动手，跑完在任务下面回写结果；任务块里可以写一行 `能力链: [...]` 走显式能力序列。它不做 git 推送。

**三道账**：判前门（guard，动手前判定）、事件账本（event-log，可追加/回放/拉时间线）、任务状态（task-state，外部与内部读同一份状态文件）。

## 代码宪法（写码纪律怎么落地）

写代码的模型知道规矩，但不一定每轮都记得住。这里把规矩做成三件实物。

**一、一份短文档**。`docs/代码宪法.md` 里 `<!-- CODE-CONSTITUTION:BEGIN -->` 与 `<!-- CODE-CONSTITUTION:END -->` 之间是唯一正文，分三层：常驻五条（不编造、不越权、边界内做到底、只认一个现行说法、说了就做）；按数字判的十一条（函数 ≤50 行、嵌套 ≤3 层、圈复杂度 ≤10，另有死代码、静默失败、空壳函数、明文凭据四类要求为零）；评审环节才加载的五条。

**二、真的送进提示里**。三处注入：写码入口 `code_agent_engine.py`、循环写码入口 `agents/code/code-runloop/prompts.py`、审查入口 `review.py`。注入失败不静默，会在提示里留下显式标记，让人一眼看出宪法没进去。

**三、一个能跑起来的执法者**。`scripts/constitution_gate.py` 读 `config/constitution_thresholds.json`（数字唯一出处），扫全仓给出越线清单与退出码。越线处可以写一行 `# 宪法例外: <理由>` 登记例外，例外进台账、要留名字；全库存量走 `config/constitution_baseline.json` 基线台账，所以持续集成只判**本次改动**新引入的越线（`scripts/constitution_gate_changed.py`，取不到基线就明确报"不判"，不假装通过）。

**要改宪法**：编辑 `docs/代码宪法.md` 的标记区间，跑 `python scripts/verify_code_constitution.py`（29 项接线断言），再跑 `python scripts/constitution_gate.py`。

## 详细文档（都在这里，不放 README）

| 文档 | 说明 |
|------|------|
| [docs/代码宪法.md](docs/代码宪法.md) | 代码宪法正文（唯一来源）与三层结构 |
| [docs/CAPABILITY.md](docs/CAPABILITY.md) | 能力与原子清单 |
| [docs/ATOMS_GUIDE.md](docs/ATOMS_GUIDE.md) | 39 原子逐个指南：能力 / 入参 / 返回 / 示例 |
| [docs/POSITIONING.md](docs/POSITIONING.md) | 与同类开源的真实对照（编码代理类 8 + 质量治理工具类 20）：优势 8 条 / 短板 6 条逐条显性 |
| [docs/PROMOTION.md](docs/PROMOTION.md) | 宣传与边界：场景痛点 / 方法论 / 诚实边界 |
| [docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md) | 对接 LangChain / CrewAI / Claude Code 等框架 |
| [docs/SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md) | 沙箱 / 子进程 / 路径防护 / 数据不出厂 |
| examples/ | 已跑通的对接示例代码 |

## 安装与环境

- **核心零第三方依赖**，要求 Python 3.8+（Windows / Linux / macOS）。
- **可选依赖**（不装也能跑核心；缺失时对应能力降级或明确报错，不静默）：
  - 浏览器冒烟原子 `agents/web/browser-smoke`（CDP over WebSocket）需要 `websocket-client`；
  - 开发便利：`pytest`、`bandit`、`pyflakes`、`coverage`、`stdlib_list`。
  - 完整清单见 [requirements-optional.txt](requirements-optional.txt)。
- 默认数据不出厂；云端能力需显式开启。

## 回归与状态

- `python codeagent.py status --json`：注册 **39 原子 / 161 能力**，`degraded=[]`、`conflicts=[]`（实测）。
- `python -m pytest tests/ -q`：全量单测通过（CI 已接入 GitHub Actions）。

## 许可

Apache License 2.0（见 `LICENSE`）。借鉴声明见 `NOTICE`：借鉴 MIT 项目均为自实现未复制，核心零第三方运行时依赖（可选依赖见表），Apache-2.0 合规。

---

## ⭐ 支持这个项目

觉得有用就给我们一个 **Star**：[![GitHub stars](https://img.shields.io/github/stars/zhengjinjun1975/codeagent-minimal?style=social)](https://github.com/zhengjinjun1975/codeagent-minimal)

有问题提 [Issue](https://github.com/zhengjinjun1975/codeagent-minimal/issues)，有想法提交 PR。
