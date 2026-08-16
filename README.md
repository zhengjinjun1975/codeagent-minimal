# CodeAgent Minimal — 原子化可组装代码质量治理 Agent

> **v0.3.0 · 原子化重构** — 把代码审查 / 测试 / 依赖漏洞 / 变异 / 模糊 / 回归 / 自进化等能力拆成 **16 个可独立运行、可任意组装的原子智能体**，由统一运行时编排、统一入口 `codeagent` 驱动。
> **开源内核 + 闭源编排**：本仓库开源 16 原子 + 统一运行时/入口（Apache-2.0）；重型闭源编排（assembler / orchestrator / CodeMode）位于独立工作区，不随本仓库分发。

[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.3.0-blue.svg)](CHANGELOG.md)

---

## 定位

**原子化、可组装的代码质量治理 Agent 开源内核。**

- **原子化**：每个能力是一个「原子智能体」（`AtomicAgent` 基类），具备统一接口 `call / run / describe`、统一生命周期 `discovered → loaded → ready`、统一 `{ok, data}` 结果信封、失败自动降级 `{ok:false, degraded:true}`。
- **可组装**：原子之间通过能力声明（`provides` / `depends_on`）由加载器做拓扑排序 + 冲突检测，按需拼成任意组装链（如 `think→gen→review→test→evolve`、`guard = review+dep-scan+fuzz`）。
- **零依赖**：纯 Python 标准库运行，开箱即用，不装任何包。
- **数据不出厂**：默认本地处理，云端 LLM / 远端 OSV 需显式开启。

---

## 架构

### 统一运行时 + 统一入口 + 组装链

```
                ┌──────────────────────────────────────────────┐
   codeagent    │            agent_runtime.py                  │
  (统一入口 CLI) │    统一运行时：加载原子 / 能力路由 / 组装链     │
 ─────────────► │              / 权限 / 降级                    │
                └───────────────┬──────────────────────────────┘
                                │ 注册 & 调度
        ┌───────────────────────▼────────────────────────┐
        │  agent_loader.py                                │
        │  manifest 校验 / 依赖解析(Kahn拓扑) / 冲突检测   │
        │  / 失败降级                                    │
        └───────────────────────┬────────────────────────┘
                                │ 16 原子
   ┌───────┬───────┬───────┬────┴────┬───────┬───────┬───────┐
   │review │  test │ dep-  │  fuzz   │impact │ llm-  │ mcp-  │ ... 16 atoms
   │       │       │ scan  │         │       │ router│ client│
   └───────┴───────┴───────┴─────────┴───────┴───────┴───────┘
        │ 组装链示例：
        │  chain  think→gen→review→test→evolve
        │  guard  review + dep-scan + fuzz 协同（安全·质量）
```

### 16 原子清单

| 原子 | 版本 | 能力 (provides) | 复用内核 |
|------|------|-----------------|----------|
| `code-review` | 0.2.0 | `codereview.review/design/layout/content/lsp` | `review.py` 静态审查 + LSP 诊断 + `dep_audit` 依赖图 |
| `code-test` | 0.2.0 | `test.gen/run/tdd` | `test_harness.py` 冒烟/单元/边界/变异/稳定性 |
| `dep-scan` | 0.2.0 | `depscan.scan/sca/taint/osv` | `dep_scan.py` 依赖漏洞 SCA + 污点分析 |
| `code-fuzz` | 0.2.0 | `fuzz.*` | `fuzz_engine.py` 覆盖率驱动属性模糊 |
| `code-dispatch` | 0.2.0 | `dispatch.*` | 派单 + 自适应预算 + allow/ask/deny 细粒度权限 |
| `llm-router` | 0.2.0 | `llm.generate/review` | 多模型注册表（云端 GLM / 本地 ornith），`local-only` 数据不出厂 |
| `dep-impact` | 0.1.0 | `impact.analyze/circular/coupling` | `dep_audit.py` 依赖图影响分析 |
| `code-evolve` | 0.1.0 | `evolve.*` | `self_evolve.py` 自进化 |
| `code-plan` | 0.1.0 | `plan.*` | 方案设计 |
| `code-reuse` | 0.1.0 | `reuse.*` | 代码原子复用检索 |
| `code-memory` | 0.1.0 | `memory.*` | 记忆沉淀 |
| `code-skill` | 0.1.0 | `skill.*` | SKILL 技能资产沉淀 |
| `mcp-client` | 0.1.0 | `mcp.tools/call/list` | MCP 工具接入 |
| `code-project` | 0.1.0 | `project.*` | 项目分析 |
| `task-state` | 0.1.0 | `taskstate.*` | 跨会话任务状态跟踪 |
| `code-deliver` | 0.1.0 | `deliver.report/package` | 交付验收报告（数据不出厂） |

> 全部 `open_source: true`。注册索引见 `registry.json`（`agent_loader.build_registry()` 可自动重建）。

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
| **影响分析** | `impact` | 依赖图 / 环 / 耦合 |
| **自进化** | `evolve` / `evolve-loop` | refine / 大自进化闭环 |
| **记忆** | `memory` | 经验沉淀 |
| **MCP** | `mcp` | 工具列表 / 调用 |
| **SKILL** | `skill` | 技能清单 / 加载 / 导出 / sediment |
| **多模型** | `llm` / `models` | 注册表 / 审查 |
| **派单** | `dispatch` | 派单 + 预算 + allow/ask/deny 权限 |
| **安全·质量组装链** | `guard` | review + dep-scan + fuzz 协同 |

---

## 用法

统一入口：`python codeagent.py <子命令>`。16 原子皆可通过子命令独立运行，也可经组装链协同。

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

---

## 安装

零依赖，无需安装任何第三方包。要求 Python 3.8+（纯标准库 `ast / re / json / subprocess / importlib`）。

```bash
git clone git@github.com:zhengjinjun1975/codeagent-minimal.git
cd codeagent-minimal
```

可选（开发便利，非必需，缺失自动降级）：`pytest`、`bandit`、`pyflakes`、`coverage`。

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
```

---

## 回归

- `pytest tests/ test_review.py`：**64 passed**（16 原子加载/ready、组装链 DAG、真实数据逐原子执行、guard 协同）。
- 闭源 `verify_chain.py` 组装链（think→gen→review→test→evolve）独立可运行验证通过。

---

## 许可

本项目采用 **Apache License 2.0**（见 `LICENSE`，全文 201 行，标准官方文本）。

借鉴声明见 `NOTICE`：

- **借鉴 MIT 项目（自实现，未复制）**：FSoft CodeWiki、OpenCode（MCP/SKILL/CodeMode 概念）、CodeReview/测试 harness 方法论等；Semgrep 思路（LGPL）仅借鉴 source→sink 污点分析**思路**，自实现为纯 stdlib 污点引擎。
- **MIT 兼容 Apache-2.0**：由于未复制任何 MIT 源码，不触发 MIT 通知包含或衍生作品义务。
- **零第三方运行时依赖**：纯标准库；`bandit/pyflakes/coverage/pytest` 为可选开发便利，缺失自动跳过，不构成对交付物任何许可义务。
- **边界**：本仓库开源 16 原子 + 统一运行时/入口；**闭源编排**（assembler / orchestrator / CodeMode）位于独立工作区 `E:/code_agent`，不随本仓库分发、不链接、非必需。

**合规结论：Apache-2.0 合规**（借鉴 MIT 均为自实现 + NOTICE 标注 + 零第三方运行时依赖）。

---

## 边界与兼容

- **开源边界**：本仓库 = 开源内核（16 原子 + agent_runtime + codeagent 统一入口）。重型闭源编排不在此。
- **数据边界**：默认数据不出厂；云端 LLM / OSV 需显式 `--remote / --osv / --llm` 开启。
- **兼容**：`legacy_cli.py` 保留旧命令兼容；统一入口子命令即新推荐用法。
- **环境**：Windows / Linux / macOS，Python 3.8+。
