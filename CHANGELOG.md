# Changelog

所有显著变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [0.3.0] - 2026-08-16

### 重组合整体收尾（16 原子 + 统一运行时/入口/组装链 + 审查测试深化）

- **registry.json 重建落盘 16 原子**：用 `agent_loader.build_registry()` 重建并写入 registry.json（此前代码重建但未落盘，仅 5 原子）。16 原子 = 14 核心 + **dep-scan（SCA 依赖漏洞 + 污点分析）** + **code-fuzz（覆盖率驱动属性模糊）**，零冲突、零降级。
- **新原子升级 v0.2.0**：`dep-scan`、`code-fuzz` 由 0.1.0 升级至 **0.2.0**（与 code-test/code-review/code-dispatch/llm-router 对齐），manifest 落盘一致。
- **统一运行时 agent_runtime.py 16 原子协同**：`dep_scan`/`fuzz`/`review_with_guard` 统一调用 dep-scan/code-fuzz 原子，SCA/污点 + 属性模糊 → code-review 安全·质量协同，数据不出厂默认。
- **统一入口 codeagent.py**：新增 `dep-scan` / `fuzz` / `reg-guard` / `guard` 子命令（guard = review+dep-scan+fuzz 组装链）。
- **修 4 失败测试（14→16 原子断言）**：`test_runtime_16_atoms_ready_no_degraded`、`test_unified_entry_api_atoms`、`test_sixteen_atoms_load_ready`、`test_assembler_dag_and_conflicts`。
- **审查测试深化**：`test_each_atom_runs_real_data` 新增 dep-scan/fuzz 真实数据执行；新增 `test_guard_chain_coop_16_atoms`（guard 组装链真实数据协同）。
- **回归全绿**：`pytest tests/ test_review.py` **64 passed**（原 59 + 修 4 + 新增 guard 测试）；闭源 `verify_chain.py` 组装链全绿（think→gen→review→test→evolve 独立可运行验证通过）。

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

### 吸收 OpenCode 能力升维收尾（LSP 诊断修复 + 六能力回归验证）

- **LSP 诊断修复（code-review v0.2.0）**：`lsp_mock_server.py` 未定义名检测从粗糙正则改为 **AST 作用域感知**（模块/函数/类/lambda/推导式 + 参数/赋值/导入/循环变量/异常变量），修复把 `def`/`return`/函数参数误报为未定义名的 bug。`main.py` 增加**后台 reader 线程 + 队列**收 LSP 帧，规避 Windows 管道 `read(1)` 阻塞。端到端验证：语法错误(severity 1→critical)、未定义名(severity 2→major)、行长(severity 3→minor) 三类诊断并入评分**真实生效**（lsp_test_target 100→77）。新增测试目标 `lsp_test_target.py`（未定义名+行长）与 `lsp_syntax_error.py`（语法错误）。
- **六能力回归验证（新增 `tests/test_codeagent_opencode_capabilities.py` 15 用例）**：MCP（mcp-client 真实工具 echo/upper/add + 真实调用转大写）、多模型（llm-router v0.2.0 注册表 cloud+local + 默认 local_only 封锁云端）、SKILL（code-skill 技能清单 + sediment 产出标准 SKILL.md 资产）、CodeMode 编排（闭源 codemode.py 单次 execute 编排依赖/并发/聚合真实生效 + confined 白名单拒绝任意能力）、细粒度权限（code-dispatch v0.2.0 dispatch.permission allow/ask/deny 三级判定 + deny>allow>ask 优先级 + 通配）、LSP 诊断（语法/未定义名零误报/行长 + 并入评分）。
- **回归全绿**：registry 重建 14 原子（code-review/code-dispatch/llm-router 0.2.0）零冲突；`pytest tests/` **55 passed**（原 40 + 新 15）；闭源 `verify_chain.py` 组装链全绿（think→gen→review→test→evolve，独立可运行已验证）。

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
