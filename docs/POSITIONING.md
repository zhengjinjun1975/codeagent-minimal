# CodeAgent Minimal 能力对照说明（Positioning）

> **数据口径**：同类项目星标 / 许可 = **2026-09-23 经 GitHub API 实测**（`gh api repos/<owner>/<repo>`）；本仓数字取自 `registry.json`（35 原子 / 140 能力）与全库扫描（138 个 `.py` / 30,234 行 / **无 requirements.txt**）。
> 本文件只做**定位与边界**说明：优势逐条列出，短板同样逐条列出，不拔高、不贬低。

---

## 一句话定位

本仓**不是「替你写代码的代理」**（Aider / Cline / Codex CLI 那一类），而是**「本地零依赖的代码质量治理内核」**：
把大厂需要装一堆引擎 + 服务才能做的检查（审查 / 安全 / 依赖 / 测试 / 影响 / 合规 / 审批），
用**纯标准库**在一台机器上跑完，且**可任意组装成链、数据默认不出厂**。

一句话卖点：**能塞进隔离内网、离线复算、不被云服务存续绑架的代码质量底座**。

## 坐标（一张逻辑图）

```
                  云端重依赖（需账号 / API Key / Docker / 引擎 / 数据库）
                                  │
   ● Codex CLI (126.0k)           │           ● Cline (69.1k)   ● Continue (36.0k)
   ● Gemini CLI (107.1k)          │
   ● Aider (49.1k)                │
                                  │
   生成代码 ────────────────────────┼────────────────────────── 治理代码
                                  │
   ● OpenHands (88.9k)            │   ● Semgrep (16.7k)   ● CodeQL (10.1k)
   ● SWE-agent (20.4k)            │   ● Trivy (38.0k)     ● SonarQube (11.0k)
   ● Goose (54.6k)                │   ● Ruff (49.7k) ● Bandit (8.3k) ● pylint (5.7k)
                                  │   ● pytest (14.5k) ● mutmut (1.4k) ● Hypothesis (9.0k)
                                  │
                                  │   ★ CodeAgent Minimal —— 本地零依赖 + 治理闭环 + 可组装
                  本地零依赖（不装任何第三方包也能跑）
```

一句话读图：**上面一圈各强在「深度 / 生成」，而「本地零依赖 + 多面合一 + 可组装成链」这一格，本仓是少数站得住的。**

---

## 表 A：与「编码代理类」不是同一物种

它们的主职是**替你写代码**，代价是默认把代码 / 上下文交给云端模型；本仓不生成代码，但把「代码合不合格」这一层做完整。

| 项目 | 星标 | 许可 | 主职 | 运行依赖 | 默认数据去向 | 治理闭环（审查/安全/测试/影响） |
|------|------|------|------|----------|--------------|----------------------------------|
| `openai/codex` | 126,019 | Apache-2.0 | 终端编码代理 | 需账号 + 云模型 | 云端 | ✗ |
| `google-gemini/gemini-cli` | 107,132 | Apache-2.0 | 终端编码代理 | 需云模型 | 云端 | ✗ |
| `All-Hands-AI/OpenHands` | 88,903 | MIT | 全自动编码代理 | Docker 沙箱 + 模型 | 云端/自备 | ✗ |
| `cline/cline` | 69,094 | Apache-2.0 | IDE 编码代理 | 需模型 API | 云端 | ✗ |
| `block/goose` | 54,573 | Apache-2.0 | 本地编码代理 | 需模型 | 云端/本地模型 | ✗ |
| `aider-AI/aider` | 49,122 | Apache-2.0 | 终端结对编程 | 需模型 API | 云端 | ✗ |
| `continuedev/continue` | 35,997 | Apache-2.0 | IDE 助手 | 需模型 | 云端 | ✗ |
| `SWE-agent/SWE-agent` | 20,386 | MIT | 研究型修 issue 代理 | 需模型 API | 云端 | ✗ |
| **CodeAgent Minimal（本仓）** | — | Apache-2.0 | **质量治理内核** | **无（可选增强缺失即降级）** | **默认不出厂** | **✓（35 原子 / 140 能力）** |

## 表 B：与「质量治理工具类」同层，差在覆盖与组装

这些工具与本仓干的是同一层的事，但它们**一工具一件事、各有一套输出格式**；本仓是一个内核把这几层合起来，输出同一个信封，还能拼成链。

| 工具 | 星标 | 许可 | 干的事 | 要装什么 | 输出契约 | 可编排 | 数据不出厂 |
|------|------|------|--------|----------|----------|--------|------------|
| `astral-sh/ruff` | 49,734 | MIT | Python lint + format | pip 装 | 自有格式 | ✗ | ✓ |
| `aquasecurity/trivy` | 38,021 | Apache-2.0 | 漏洞 / 镜像 / 配置扫描 | 二进制 + 漏洞库 | 自有格式 | ✗ | ✓（需更新库） |
| `gitleaks/gitleaks` | 29,437 | MIT | 密钥泄露 | 二进制 | 自有格式 | ✗ | ✓ |
| `semgrep/semgrep` | 16,728 | LGPL-2.1 | 规则静态分析 | pip / 引擎 + 规则 | 自有格式 | ✗ | 部分（云规则） |
| `pytest-dev/pytest` | 14,521 | MIT | 跑测试 | pip | 自有格式 | ✗ | ✓ |
| `coveragepy/coveragepy` | 3,409 | Apache-2.0 | 覆盖率（行 / 分支） | pip（可选 C 扩展） | 自有格式 | ✗ | ✓ |
| `google/osv-scanner` | 11,075 | Apache-2.0 | 依赖漏洞 | 二进制 + OSV 库 | 自有格式 | ✗ | ✓（需库） |
| `sonarsource/sonarqube` | 11,011 | LGPL-3.0 | 代码质量平台 | 服务端 + DB | 平台 UI | ✗ | ✓（自建） |
| `github/codeql` | 10,117 | MIT（**仅查询库**；CLI 二进制为专有 Terms） | 深度数据流分析 | CLI + 数据库 + 查询包 | SARIF | ✗ | ✓ |
| `HypothesisWorks/hypothesis` | 9,003 | NOASSERTION（实为 MPL-2.0） | 属性测试 | pip | 自有格式 | ✗ | ✓ |
| `PyCQA/bandit` | 8,278 | Apache-2.0 | 安全规则扫描 | pip | 自有格式 | ✗ | ✓ |
| `pylint-dev/pylint` | 5,726 | GPL-2.0 | Python 静态检查 | pip | 自有格式 | ✗ | ✓ |
| `tox-dev/tox` | 3,937 | MIT | 多环境测试编排 | pip | 自有格式 | 部分 | ✓ |
| `stryker-mutator/stryker-js` | 3,143 | Apache-2.0 | 变异测试（JS） | npm | 自有格式 | ✗ | ✓ |
| `rubik/radon` | 2,030 | MIT | 复杂度度量 | pip | 自有格式 | ✗ | ✓ |
| `boxed/mutmut` | 1,449 | BSD-3-Clause | 变异测试（Py） | pip | 自有格式 | ✗ | ✓ |
| `se2p/pynguin` | 1,387 | MIT | 自动生成测试 | pip | 自有格式 | ✗ | ✓ |
| `pypa/pip-audit` | 1,366 | Apache-2.0 | 依赖漏洞 | pip | 自有格式 | ✗ | ✓ |
| `tarpas/pytest-testmon` | 1,015 | MIT | 增量测试选择 | pip | 自有格式 | ✗ | ✓ |
| `sixty-north/cosmic-ray` | 658 | MIT | 变异测试 | pip | 自有格式 | ✗ | ✓ |
| **CodeAgent Minimal（本仓）** | — | Apache-2.0 | **上表多类合一**（审查 + 安全 + 依赖 + 测试 + 突变 + 影响 + 合规） | **无** | **统一 `{ok, data}` 信封** | **✓ 组装链** | **✓ 默认** |

---

## 优势（逐条显性，不埋在表格里）

1. **零安装起点**：无 `requirements.txt`；核心判定全走标准库（可选增强如 bandit / pyflakes / coverage / pytest 装了才用、缺失即降级）。对照：Semgrep 要装引擎 + 规则、CodeQL 要建数据库、Trivy 要下漏洞库、SonarQube 要起服务 + DB。
2. **覆盖面 × 组装**：35 原子 / 140 能力，横跨代码审查、安全扫描、依赖 SCA、测试与变异、影响分析（方法级 / 依赖级）、死代码、架构审查、本体 / 领域审查、审批、沙箱、上下文压缩、模型降级；可任意组装成链（`guard` 安全质量链、`chain` think→gen→review→test→evolve）。
3. **统一契约**：每个原子同一生命周期（`discovered → loaded → ready`）、统一 `{ok, data}` 信封、失败自动降级 `{ok:false, degraded:true}`；manifest 声明 `permission`（read/write/exec/net）+ `resource` 并受门复核。
4. **数据不出厂是默认值**：云端 LLM / 远端漏洞库必须显式开启；同类编码代理默认就把代码 / 上下文交给云端模型。
5. **自证体系**：188 条 pytest + 新增 **7 项跨面能力一致性门**（含分辨力自检：注入错误计数 / 删表行 / 删映射键必须报红）+ GitHub Actions CI。
6. **可加壳扩展不碰核心**：扩展原子挂壳层（Lab 编排画布），核心原子只读复用；拆得开、拼得起。
7. **危险面收紧**：命令审批（`approval.check/classify/policy`）细粒度 allow/ask/deny + 执行期审批门；沙箱执行不可信 PoC。
8. **可解释可复算**：判定主体是规则 / 阈值，能复算、能对账；模型只做兜底与增强，不把结论托付给黑盒。

## 短板（同样显性，别误用）

1. **单点深度不如专业工具**：Semgrep 的规则库、CodeQL 的数据流 / 污点深度、Trivy 的漏洞库时效、pytest 的插件生态，本仓都比不上。本仓是**「广度 + 零依赖 + 可组装」**，不是**「最深」**。
2. **不写代码**：不做 Aider / Cline 那类多文件大改；`code.implement` / `code-runloop` 是受控小步。
3. **接入走 CLI / REST / 框架 Tool**：没有 VS Code 插件那条路（有本地 Lab 编排与浏览器前端）。
4. **无社区生态与星标体量**：单人维护、无第三方插件市场（表中的星标差就是真实差距）。
5. **规则库靠外部**：离线时只有内置规则，远端漏洞库要显式开启且需网络。
6. **无大仓公开基准**：本仓自身规模下逐原子实测，但**未做过超大仓库压测**，不能声称生产级大仓性能。

---

## 可借鉴清单（短板→路线，按优先级）

1. `testmon` 式**受影响测试选择**（已有 `test.select` 雏形，可深做）
2. `coverage.py` 式**分支覆盖**（当前是行级 / 冒烟级）
3. `mutmut` 式**变异体增量缓存**（当前突变跑全量）
4. `tox` 式**多环境矩阵**（当前单环境顺序执行）

---

## 数据来源（可复核）

- 同类项目星标 / 许可：`gh api repos/<owner>/<repo>`，**2026-09-23 实测**；许可取自 `license.spdx_id`；另经 3 路独立调研（编码代理 / 静态分析安全 / 测试变异）一手核对，数字一致。
- 本仓：`registry.json`（35 原子 / 140 能力）；全库扫描 138 个 `.py` / 30,234 行 / 无 `requirements.txt`；测试数取自 `pytest` 实测。
