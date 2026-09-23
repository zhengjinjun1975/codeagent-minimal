# CodeAgent Minimal — 给工厂审外包代码的本地代码智能体

> ⭐ 觉得有用就给我们一个 **Star**，支持持续迭代。
> [![GitHub stars](https://img.shields.io/github/stars/zhengjinjun1975/codeagent-minimal?style=social)](https://github.com/zhengjinjun1975/codeagent-minimal)

> **v0.6.1 · 一体化完整版**：一个本地运行的代码智能体，`git clone` 即用，纯 Python 标准库、零第三方依赖、数据不出厂，Apache-2.0。

[![License](https://img.shields.io/badge/License-Apache-2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.1-blue.svg)](CHANGELOG.md)

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

**为什么落地容易**：纯 Python 标准库、零第三方依赖，工厂一台普通机器、IT 不强也能跑；一键出报告，审完即可对外包提要求或留档追责。

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

## 详细文档（都在这里，不放 README）

| 文档 | 说明 |
|------|------|
| [docs/ATOMS_GUIDE.md](docs/ATOMS_GUIDE.md) | 39 原子逐个指南：能力 / 入参 / 返回 / 示例 |
| [docs/POSITIONING.md](docs/POSITIONING.md) | 与同类开源的真实对照（编码代理类 8 + 质量治理工具类 20）：优势 8 条 / 短板 6 条逐条显性 |
| [docs/PROMOTION.md](docs/PROMOTION.md) | 宣传与边界：场景痛点 / 方法论 / 诚实边界 |
| [docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md) | 对接 LangChain / CrewAI / Claude Code 等框架 |
| [docs/SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md) | 沙箱 / 子进程 / 路径防护 / 数据不出厂 |
| examples/ | 已跑通的对接示例代码 |

## 安装与环境

- 零第三方依赖，要求 Python 3.8+（Windows / Linux / macOS）。
- 可选开发便利（非必需，缺失自动降级）：`pytest`、`bandit`、`pyflakes`、`coverage`。
- 默认数据不出厂；云端能力需显式开启。

## 回归与状态

- `python codeagent.py status --json`：注册 **39 原子 / 161 能力**，`degraded=[]`、`conflicts=[]`（实测）。
- `python -m pytest tests/ -q`：全量单测通过（CI 已接入 GitHub Actions）。

## 许可

Apache License 2.0（见 `LICENSE`）。借鉴声明见 `NOTICE`：借鉴 MIT 项目均为自实现未复制，零第三方运行时依赖，Apache-2.0 合规。

---

## ⭐ 支持这个项目

觉得有用就给我们一个 **Star**：[![GitHub stars](https://img.shields.io/github/stars/zhengjinjun1975/codeagent-minimal?style=social)](https://github.com/zhengjinjun1975/codeagent-minimal)

有问题提 [Issue](https://github.com/zhengjinjun1975/codeagent-minimal/issues)，有想法提交 PR。
