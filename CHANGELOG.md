# Changelog

所有显著变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [0.2.0] - 2026-08-16

### 新增（原子化重构 P0：agent_loader + AtomicAgent 基类 + 首批 3 原子）

- **`atomic_base.py`**：原子智能体基类——统一接口 `call/run/describe`，生命周期 `discovered→loaded→ready`，能力注册 `register/capabilities`，统一 `{ok,data}` 信封，异常捕获失败降级 `{ok:false, error, degraded:true}`。零第三方依赖。
- **`agent_loader.py`**：原子加载器——manifest schema 校验（name==目录名/version/entry 存在/依赖声明）、依赖解析（Kahn 拓扑排序）、冲突检测（重复能力/自依赖/未知依赖/依赖环/依赖方向铁律）、失败降级（任何错误不抛给上层）。含 `scan`/`build_registry`/`load_agents` 及 CLI。
- **首批 3 原子**（放 `agents/`，规避 `atoms/` 复用库同名冲突；均为 `open_source:true`，核心零改动，加壳包 `{ok,data}`）：
  - `dep-impact`：复用 `dep_audit.build_graph/dep_report`，`impact.analyze/circular/coupling`，零 LLM
  - `code-test`：复用 `test_harness.run_all`，`test.gen/run/tdd`，红绿回归
  - `llm-router`：复用 `model_config.json`，`llm.generate`（云端 GLM）/`llm.review`（本地 ornith），`local-only` 开关数据不出厂
- **`registry.json`**：3 原子注册索引（scan 自动重建）。
- **`tests/test_codeagent_atomic_agents.py`**：19 用例（loader 校验/拓扑/冲突/降级 + 3 原子真实 load/run）。双绿回归：与现有 `test_review.py` 一起 `27 passed`。
- **架构依据**：`E:/code_agent/docs/CODEGENT_ATOMIC_ARCHITECTURE.md`（原子化重构设计 P0）。

## [0.1.1] - 2026-08-11

### 新增（代码原子库随仓库分发 + 方法论落地）

- **内置代码原子库**：`atoms/` 目录随仓库分发——19 个极简代码原子（.md，智能制造/地球物理/本体建模/数据检索/深度学习 5 领域）+ 3 个可直接运行的 .py（bm25_search / softmax / isolation_forest）
- **复用建议完善**：`--reuse-atoms` 本地 Obsidian 原子 → GitHub 远端开源 → 大模型兜底，三层递进全程静默不报错
- **方法论文档**：README 内置「复用优先·极简落地」决策流；完整文档见 Obsidian `knowledge/code/patterns/reuse-first-minimalism.md`

## [0.1.0] - 2026-08-11

### 变更（版本降维 + 方法论融合）

- **版本降维**：v2.3 → **v0.1.0**，从 0.1 重新开始（诚实定位：融入复用优先·极简落地方法论后，作为新起点的首个版本）
- **方法论融合**：内置「复用优先·极简落地（Reuse-First Minimalism）」——能复用就不写，必须写就极简
- **新增 `_static_check_reuse` 审查维度**：静态检查冗余抽象 / 转发函数 / 重复字符串 / 仅 `__init__` 的类，提醒该复用/该极简
- **新增 `--reuse-atoms` 应用接口**：审查时检索本地 Obsidian 代码原子库（5 领域）→ 未命中自动降级 GitHub 远端开源代码搜索 → 给复用建议；全程静默不报错（无 Obsidian/断网/限流自动跳过）

### 方法论：复用优先 · 极简落地

```
找的阶梯（Reuse）：① 本地 Obsidian 代码原子 → ② GitHub 远端开源 → ③ 大模型兜底
写的阶梯（极简）：标准库 → 已装依赖 → 一行 → 最少代码
```

完整方法论文档见仓库 `docs/`。

## 旧版（v2.x，已降维，历史不在此记录）

> 降维前为 v2.3（专业化代码审查 + 测试 harness，含 --external 可选对接 bandit/ruff、网络安全隐患扫描、CI 门禁等）。历史版本见 git。
