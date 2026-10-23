# 原子能力图谱（生成物）

> 本文件由 `scripts/verify_atoms_graph.py --emit` 从代码生成，**请勿手写修改**。
> CI 用 `--check-drift` 比对：生成物与代码不同步即红。改图请改代码再重新生成。


完整性的环图快照见 `docs/atoms_graph_snapshot_<日期>.html`（当时生成的静态图，非本门产物）；本文件是**组合追踪**视图，两者同源不同视角。

## 一、概览

| 指标 | 值 |
|---|---|
| 原子 / 能力 | **41** / **166** |
| 能力挂载 | **166/166**（声明=挂载的原子 41/41） |
| 有调用边的原子 | **21/41** |
| 能力覆盖率 | **20.5%**（34/166） |
| 调用点 | **45** 处（看得见 33 / 看不见 12） |
| 组合 | **7** 条 / **18** 步（可达：cli-reachable 7） |
| 四问判定 | Q1 GREEN ｜ Q2 GREEN ｜ Q3 GREEN ｜ Q4 YELLOW |
| 进入组合的能力 | **12/166** |
| 原子↔原子调用边 | 0（拓扑 star） |

## 二、组合清单（每一步都能落到真实能力）

组合 = 有序能力序列。下表每条组合的每一步都给出：能力名 → 所在原子 → 静态入参 → 定义位置。
`未解析` 的行就是会断的地方；`可达` 一列说明这条组合从哪个入口进得来。

### 改缺陷/修回归（模板）

- 组合 ID：`tpl:agents/dispatch/code-dispatch/chain_templates.json#改缺陷/修回归`
- 定义位置：`agents/dispatch/code-dispatch/chain_templates.json`
- 可达：**cli-reachable**（入口 cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain）
- 命中关键词：修 bug、修复、报错、异常、崩溃、不生效、回归、还原不了、跑不起来、import 失败、测试红了

| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |
|---|---|---|---|---|---|
| 1 | plan.think | `plan.think` | code-plan | `language=python task=$task` | ✅ |
| 2 | code.implement | `code.implement` | code-implement | `task=$task` | ✅ |
| 3 | test.run | `test.run` | code-test | `—` | ✅ |
| 4 | codereview.review | `codereview.review` | code-review | `code=$upstream_files reuse_atoms=True use_llm=False` | ✅ |

### 写码/新增功能（模板）

- 组合 ID：`tpl:agents/dispatch/code-dispatch/chain_templates.json#写码/新增功能`
- 定义位置：`agents/dispatch/code-dispatch/chain_templates.json`
- 可达：**cli-reachable**（入口 cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain）
- 命中关键词：实现、新增、写一个、写个、生成、开发、加个、接口、函数、脚本

| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |
|---|---|---|---|---|---|
| 1 | code.implement | `code.implement` | code-implement | `task=$task` | ✅ |
| 2 | codereview.review | `codereview.review` | code-review | `code=$upstream_files reuse_atoms=True use_llm=False` | ✅ |

### 代码审查/质量（模板）

- 组合 ID：`tpl:agents/dispatch/code-dispatch/chain_templates.json#代码审查/质量`
- 定义位置：`agents/dispatch/code-dispatch/chain_templates.json`
- 可达：**cli-reachable**（入口 cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain）
- 命中关键词：审查、评审、代码质量、规范、review、重构建议、问题诊断、哪里写得不好

| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |
|---|---|---|---|---|---|
| 1 | security.project | `security.project` | security-scan | `path=.` | ✅ |
| 2 | deadcode.scan | `deadcode.scan` | deadcode | `path=.` | ✅ |

### 依赖/影响面（模板）

- 组合 ID：`tpl:agents/dispatch/code-dispatch/chain_templates.json#依赖/影响面`
- 定义位置：`agents/dispatch/code-dispatch/chain_templates.json`
- 可达：**cli-reachable**（入口 cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain）
- 命中关键词：依赖、升级、断链、影响面、影响分析、引用、耦合、版本冲突

| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |
|---|---|---|---|---|---|
| 1 | depscan.scan | `depscan.scan` | dep-scan | `target=.` | ✅ |
| 2 | impact.analyze | `impact.analyze` | dep-impact | `path=. transitive=True` | ✅ |

### 测试/用例（模板）

- 组合 ID：`tpl:agents/dispatch/code-dispatch/chain_templates.json#测试/用例`
- 定义位置：`agents/dispatch/code-dispatch/chain_templates.json`
- 可达：**cli-reachable**（入口 cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain）
- 命中关键词：测试、用例、覆盖率、单元测试、冒烟、smoke、pytest

| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |
|---|---|---|---|---|---|
| 1 | test.select | `test.select` | code-test | `project_root=. run=True` | ✅ |

### 排查/定根因（模板）

- 组合 ID：`tpl:agents/dispatch/code-dispatch/chain_templates.json#排查/定根因`
- 定义位置：`agents/dispatch/code-dispatch/chain_templates.json`
- 可达：**cli-reachable**（入口 cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain）
- 命中关键词：复现、排查、定位、根因、为什么、查不到

| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |
|---|---|---|---|---|---|
| 1 | plan.think | `plan.think` | code-plan | `language=python task=$task` | ✅ |
| 2 | archreview.layers | `archreview.layers` | arch-review | `path=.` | ✅ |

### 全流程/综合任务（模板·默认）

- 组合 ID：`tpl:agents/dispatch/code-dispatch/chain_templates.json#全流程/综合任务`
- 定义位置：`agents/dispatch/code-dispatch/chain_templates.json`
- 可达：**cli-reachable**（入口 cli:chain、cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain）

| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |
|---|---|---|---|---|---|
| 1 | think | `plan.think` | code-plan | `language=$seed.language task=$task` | ✅ |
| 2 | gen | `plan.gen` | code-plan | `code=$seed.code language=$seed.language spec=$seed.spec task=$task` | ✅ |
| 3 | review | `codereview.review` | code-review | `code=$upstream_files reuse_atoms=True use_llm=False` | ✅ |
| 4 | test | `test.run` | code-test | `target_dir=.` | ✅ |
| 5 | evolve | `evolve.refine` | code-evolve | `outcome={'score': '$step.review.score', 'issues': '$step.review.issues', 'task': '$task'} task=$task` | ✅ |

### codeagent.py.chain（代码内）

- 组合 ID：`code:codeagent.py#chain`
- 定义位置：`codeagent.py`:159
- **静态抽不出步骤**：组合由运行时给出（参数透传），不是代码里的组合定义

## 三、能力 → 组合 反查（改动影响面）

改某条能力之前，先在这里查它被哪些组合用到、哪些调用点依赖它。
`调用点 0 / 组合 0` 的就是未接线能力。

| 能力 | 原子 | 调用点 | 被哪些组合用到 |
|---|---|---|---|
| `codereview.review` | code-review | 3 | 改缺陷/修回归 第4步、写码/新增功能 第2步、全流程/综合任务 第3步 |
| `evolve.refine` | code-evolve | 2 | 全流程/综合任务 第5步 |
| `memory.save` | code-memory | 2 | — |
| `reuse.local` | code-reuse | 2 | — |
| `plan.think` | code-plan | 1 | 改缺陷/修回归 第1步、排查/定根因 第1步、全流程/综合任务 第1步 |
| `test.run` | code-test | 1 | 改缺陷/修回归 第3步、全流程/综合任务 第4步 |
| `depscan.scan` | dep-scan | 1 | 依赖/影响面 第1步 |
| `impact.analyze` | dep-impact | 1 | 依赖/影响面 第2步 |
| `deliver.report` | code-deliver | 1 | — |
| `dispatch.budget` | code-dispatch | 1 | — |
| `dispatch.chain_default` | code-dispatch | 1 | — |
| `dispatch.chain_select` | code-dispatch | 1 | — |
| `dispatch.template` | code-dispatch | 1 | — |
| `evolve.self_prompt` | code-evolve | 1 | — |
| `fuzz.gen` | code-fuzz | 1 | — |
| `fuzz.run` | code-fuzz | 1 | — |
| `llm.generate` | llm-router | 1 | — |
| `llm.list_models` | llm-router | 1 | — |
| `llm.review` | llm-router | 1 | — |
| `mcp.call` | mcp-client | 1 | — |
| `mcp.tools` | mcp-client | 1 | — |
| `model.cache_lookup` | model-fallback | 1 | — |
| `model.cache_put` | model-fallback | 1 | — |
| `project.load` | code-project | 1 | — |
| `skill.export` | code-skill | 1 | — |
| `skill.list` | code-skill | 1 | — |
| `skill.sediment` | code-skill | 1 | — |
| `taskstate.track` | task-state | 1 | — |
| `code.implement` | code-implement | 0 | 改缺陷/修回归 第2步、写码/新增功能 第1步 |
| `archreview.layers` | arch-review | 0 | 排查/定根因 第2步 |
| `deadcode.scan` | deadcode | 0 | 代码审查/质量 第2步 |
| `plan.gen` | code-plan | 0 | 全流程/综合任务 第2步 |
| `security.project` | security-scan | 0 | 代码审查/质量 第1步 |
| `test.select` | code-test | 0 | 测试/用例 第1步 |
| `approval.check` | command-approvals | 0 | — |
| `approval.classify` | command-approvals | 0 | — |
| `approval.orchestrate` | command-approvals | 0 | — |
| `approval.policy` | command-approvals | 0 | — |
| `approval.resolve` | command-approvals | 0 | — |
| `archreview.boundary` | arch-review | 0 | — |
| `archreview.intent` | arch-review | 0 | — |
| `archreview.surface` | arch-review | 0 | — |
| `atomicity.breaks` | atomicity-audit | 0 | — |
| `atomicity.manifest` | atomicity-audit | 0 | — |
| `atomicity.registry` | atomicity-audit | 0 | — |
| `browsersmoke.run` | browser-smoke | 0 | — |
| `bugdeep.adv` | bug-deep | 0 | — |
| `bugdeep.model` | bug-deep | 0 | — |
| `bugdeep.poc` | bug-deep | 0 | — |
| `bugdeep.rule` | bug-deep | 0 | — |
| `code.patch` | code-runloop | 0 | — |
| `code.runloop` | code-runloop | 0 | — |
| `code.write` | code-implement | 0 | — |
| `codereview.content` | code-review | 0 | — |
| `codereview.deep` | code-review | 0 | — |
| `codereview.design` | code-review | 0 | — |
| `codereview.layout` | code-review | 0 | — |
| `codereview.light` | code-review | 0 | — |
| `codereview.lsp` | code-review | 0 | — |
| `codereview.self_eval` | code-review | 0 | — |
| `codereview.semantic` | code-review | 0 | — |
| `context.budget` | context-compact | 0 | — |
| `context.compact` | context-compact | 0 | — |
| `context.compaction_cost` | context-compact | 0 | — |
| `context.disclose` | context-compact | 0 | — |
| `context.edit` | context-compact | 0 | — |
| `context.estimate` | context-compact | 0 | — |
| `context.offload` | context-compact | 0 | — |
| `context.reveal` | context-compact | 0 | — |
| `context.store` | context-compact | 0 | — |
| `deadcode.stats` | deadcode | 0 | — |
| `deliver.package` | code-deliver | 0 | — |
| `depscan.chainbreak` | dep-scan | 0 | — |
| `depscan.osv` | dep-scan | 0 | — |
| `depscan.sca` | dep-scan | 0 | — |
| `depscan.taint` | dep-scan | 0 | — |
| `dispatch.assert_exit` | code-dispatch | 0 | — |
| `dispatch.conflict` | code-dispatch | 0 | — |
| `dispatch.permission` | code-dispatch | 0 | — |
| `dispatch.verify` | code-dispatch | 0 | — |
| `dispatch.verify_artifact` | code-dispatch | 0 | — |
| `doc.anchor` | doc-freshness | 0 | — |
| `doc.draft` | doc-freshness | 0 | — |
| `doc.stale` | doc-freshness | 0 | — |
| `domain.imports` | domain-review | 0 | — |
| `domain.valve` | domain-review | 0 | — |
| `event.append` | event-log | 0 | — |
| `event.checkpoint` | event-log | 0 | — |
| `event.replay` | event-log | 0 | — |
| `event.timeline` | event-log | 0 | — |
| `evolve.skill` | code-evolve | 0 | — |
| `evolve.tdd` | code-evolve | 0 | — |
| `fuzz.project` | code-fuzz | 0 | — |
| `fuzz.property` | code-fuzz | 0 | — |
| `git.branch` | git-ops | 0 | — |
| `git.commit` | git-ops | 0 | — |
| `git.diff` | git-ops | 0 | — |
| `git.log` | git-ops | 0 | — |
| `git.status` | git-ops | 0 | — |
| `guard.check` | guard | 0 | — |
| `guard.exit_intercept` | guard | 0 | — |
| `guard.loop_guard` | guard | 0 | — |
| `guard.pipeline` | guard | 0 | — |
| `guard.post` | guard | 0 | — |
| `guard.pre` | guard | 0 | — |
| `guard.selfcheck` | guard | 0 | — |
| `impact.circular` | dep-impact | 0 | — |
| `impact.coupling` | dep-impact | 0 | — |
| `impact.kind` | method-impact | 0 | — |
| `impact.method` | method-impact | 0 | — |
| `llm.registry` | llm-router | 0 | — |
| `local.audit` | localized | 0 | — |
| `local.chain` | localized | 0 | — |
| `local.route` | localized | 0 | — |
| `mcp.connect` | mcp-client | 0 | — |
| `mcp.guard` | mcp-client | 0 | — |
| `mcp.list` | mcp-client | 0 | — |
| `memory.recall` | code-memory | 0 | — |
| `memory.sediment` | code-memory | 0 | — |
| `minimal.deps` | minimalist-style | 0 | — |
| `minimal.independent` | minimalist-style | 0 | — |
| `minimal.style` | minimalist-style | 0 | — |
| `model.assess_complexity` | complexity-gate | 0 | — |
| `model.candidates` | model-fallback | 0 | — |
| `model.cascade` | model-fallback | 0 | — |
| `model.chain` | model-fallback | 0 | — |
| `model.route` | model-fallback | 0 | — |
| `model.rule_match` | model-fallback | 0 | — |
| `ontology.chain` | ontology-review | 0 | — |
| `ontology.quality` | ontology-review | 0 | — |
| `project.analyze` | code-project | 0 | — |
| `project.scan` | code-project | 0 | — |
| `reuse.atom` | code-reuse | 0 | — |
| `reuse.remote` | code-reuse | 0 | — |
| `sandbox.exec` | process-sandbox | 0 | — |
| `sandbox.guard` | process-sandbox | 0 | — |
| `sandbox.interpreter` | process-sandbox | 0 | — |
| `sandbox.poc` | process-sandbox | 0 | — |
| `sandbox.validate` | process-sandbox | 0 | — |
| `secrets.decrypt` | secret-vault | 0 | — |
| `secrets.encrypt` | secret-vault | 0 | — |
| `secrets.load` | secret-vault | 0 | — |
| `secrets.mask` | secret-vault | 0 | — |
| `secrets.store` | secret-vault | 0 | — |
| `secrets.vault` | secret-vault | 0 | — |
| `security.dim` | security-scan | 0 | — |
| `security.govern` | security-scan | 0 | — |
| `security.scan` | security-scan | 0 | — |
| `security.secret` | security-scan | 0 | — |
| `session.get` | session | 0 | — |
| `session.list` | session | 0 | — |
| `session.resume` | session | 0 | — |
| `session.start` | session | 0 | — |
| `session.status` | session | 0 | — |
| `session.step` | session | 0 | — |
| `skill.load` | code-skill | 0 | — |
| `subagent.dispatch` | cli-providers | 0 | — |
| `subagent.probe` | cli-providers | 0 | — |
| `subagent.providers` | cli-providers | 0 | — |
| `test.affected` | code-test | 0 | — |
| `test.coverage_analysis` | code-test | 0 | — |
| `test.gen` | code-test | 0 | — |
| `test.project` | code-test | 0 | — |
| `test.snapshot` | code-test | 0 | — |
| `test.tdd` | code-test | 0 | — |
| `ui.smoke` | browser-smoke | 0 | — |

## 四、入口与可达性（Q2 通路）

组合被声明了，不等于有人走得到。这一节回答「从哪个入口进得来」——
`cli-reachable` 敲命令能走；`api-only` 只有库调用能走（允许，须承认）；`declared-only` 谁都不通（G14 红）。

| 入口 | 类型 | 指向 | 证据（文件#符号） |
|---|---|---|---|
| `cli:chain` | cli | tpl:agents/dispatch/code-dispatch/chain_templates.json#全流程/综合任务 | `codeagent.py#chain`:default_chain |
| `cli:chain.auto` | cli | tpl:* | `agent_runtime.py#_chain_select`:select_chain |
| `cli:chain.template` | cli | tpl:* | `codeagent.py#chain`:default_chain |
| `api:run_chain.auto` | api | tpl:* | `agent_runtime.py#_chain_select`:select_chain |
| `api:default_chain` | api | tpl:* | `agent_runtime.py#default_chain`:dispatch.chain_default |
| `cli:graph` | cli | — | `codeagent.py#_graph_query`:build_graph |

| 组合 | 判定 | 入口 |
|---|---|---|
| 改缺陷/修回归 | **cli-reachable** | cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain |
| 写码/新增功能 | **cli-reachable** | cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain |
| 代码审查/质量 | **cli-reachable** | cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain |
| 依赖/影响面 | **cli-reachable** | cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain |
| 测试/用例 | **cli-reachable** | cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain |
| 排查/定根因 | **cli-reachable** | cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain |
| 全流程/综合任务 | **cli-reachable** | cli:chain、cli:chain.auto、cli:chain.template、api:run_chain.auto、api:default_chain |

## 五、自知：门看得见多少（Q4）

看不见的调用点不会违规 —— 所以门必须把「我看不见多少」当成一等结论说出来。

| 证据等级 | 含义 | 数量 | 占比 |
|---|---|---|---|
| `verified` | 能力名+参数名都可核（全核） | 30 | 66.7% |
| `name-only` | 能力名可核，参数被 `**kwargs` 摊开（只核名字） | 3 | 6.7% |
| `sealed` | 能力名来自变量，但声明了 `dispatch-sealed` 边界（边界已知，目标不可核） | 12 | 26.7% |
| `blind` | 能力名来自变量且无声明（什么都核不了） | 0 | 0.0% |

- 看得见（verified+name-only）：**33/45**；看不见（sealed+blind）：**12/45 = 26.7%**
- 阈值：黄 20% / 红 35%（`sealed` 也计入看不见 —— 边界声明不等于能校验）

## 六、规则命中

| 组 | 规则 | 级别 | 命中 | 说明 |
|---|---|---|---|---|
| A 调用正确性 | `G1_PHANTOM_CALL` | P0 | 0 | 调用了 registry 未声明的能力（运行时必然失败） |
| D 覆盖与命名 | `G2_UNWIRED_CAPABILITY` | P2 | **132** | 未接线：已挂载可调用但全仓零调用（编排覆盖度，非原子缺陷） |
| B 依赖一致性 | `G3_DEP_NOT_EXERCISED` | P2 | **5** | depends_on 声明了，源码里找不到对应调用 |
|  | `G4_UNDECLARED_DEP` | P1 | 0 | 原子实际跨原子调用，但 depends_on 未声明 |
| A 调用正确性 | `G5_CONTRACT_GAP` | P1 | 0 | 调用点实参名/必填项与目标签名对不上（TypeError 被吞成 degraded） |
| D 覆盖与命名 | `G6_NAMING_DRIFT` | P3 | 0 | 能力前缀与 domain 不一致（白名单外） |
| B 依赖一致性 | `G7_CYCLE` | P0 | 0 | depends_on 成环 |
|  | `G8_ORDER_CONFLICT` | P3 | 0 | registry.order 与依赖先序冲突 |
| C 组合可追踪 | `G9_CHAIN_PHANTOM` | P0 | 0 | 组合引用了不存在的能力（走到那一步必断） |
|  | `G10_CHAIN_INPUT_MISMATCH` | P1 | 0 | 组合的静态入参签名接不住（TypeError 被吞成 degraded） |
|  | `G11_CHAIN_PLACEHOLDER` | P2 | 0 | 组合入参用了未登记的占位符或占位符写错形态 |
| A 调用正确性 | `G12_CAP_NOT_MOUNTED` | P0 | 0 | provides 里声明了，但 main.py 没有 register（调了必失败） |
|  | `G13_CAP_UNDECLARED` | P1 | 0 | main.py 挂了能力，但 provides 没声明（对外不可见） |
| C 组合可追踪 | `G14_UNREACHABLE_CHAIN` | P1 | 0 | 组合没有任何入口可达（配了忘接，或入口声明被删） |
|  | `G15_IMPLICIT_CHAIN` | P1 | 0 | 代码里写死了组合（capability 键的 list），没走模板表也没登记 |
| D 覆盖与自知 | `G16_BLIND_RATIO` | P1 | 0 | 看不见的调用点比例超阈值（盲区在无声长大） |
| B 依赖一致性 | `G17_SIGNATURE_BREAKING` | P1 | 0 | 能力签名发生破坏性变更（删参数/改必填/改名）且未认领 |
| D 覆盖与自知 | `G18_DISPATCH_UNSEALED` | P2 | 0 | 转发型包装器没声明 dispatch-sealed 边界（黑盒不可审计） |
| C 组合可追踪 | `G19_ENTRY_STALE` | P1 | 0 | 入口清单与代码对不上（evidence 找不到/ref 指向不存在的组合） |

## 七、问题明细

### G2_UNWIRED_CAPABILITY（132 项，P2）

- `arch-review · archreview.boundary` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `arch-review · archreview.intent` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `arch-review · archreview.surface` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `atomicity-audit · atomicity.breaks` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `atomicity-audit · atomicity.manifest` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `atomicity-audit · atomicity.registry` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `browser-smoke · browsersmoke.run` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `browser-smoke · ui.smoke` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `bug-deep · bugdeep.adv` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `bug-deep · bugdeep.model` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `bug-deep · bugdeep.poc` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `bug-deep · bugdeep.rule` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `cli-providers · subagent.dispatch` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `cli-providers · subagent.probe` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `cli-providers · subagent.providers` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-deliver · deliver.package` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-dispatch · dispatch.assert_exit` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-dispatch · dispatch.conflict` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-dispatch · dispatch.permission` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-dispatch · dispatch.verify` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-dispatch · dispatch.verify_artifact` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-evolve · evolve.skill` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-evolve · evolve.tdd` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-fuzz · fuzz.project` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-fuzz · fuzz.property` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-implement · code.write` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-memory · memory.recall` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-memory · memory.sediment` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-project · project.analyze` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-project · project.scan` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-reuse · reuse.atom` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-reuse · reuse.remote` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-review · codereview.content` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-review · codereview.deep` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-review · codereview.design` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-review · codereview.layout` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-review · codereview.light` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-review · codereview.lsp` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-review · codereview.self_eval` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-review · codereview.semantic` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-runloop · code.patch` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-runloop · code.runloop` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-skill · skill.load` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-test · test.affected` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-test · test.coverage_analysis` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-test · test.gen` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-test · test.project` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-test · test.snapshot` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `code-test · test.tdd` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `command-approvals · approval.check` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `command-approvals · approval.classify` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `command-approvals · approval.orchestrate` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `command-approvals · approval.policy` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `command-approvals · approval.resolve` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `complexity-gate · model.assess_complexity` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `context-compact · context.budget` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `context-compact · context.compact` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `context-compact · context.compaction_cost` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `context-compact · context.disclose` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- `context-compact · context.edit` — 已挂载可调用，但既无调用点、也不在任何组合里（待接线）
- …… 另 72 项见 `--json` 输出

### G3_DEP_NOT_EXERCISED（5 项，P2）

- `code-evolve → memory.recall` — depends_on 声明了，源码中找不到对应调用
- `code-plan → taskstate.track` — depends_on 声明了，源码中找不到对应调用
- `code-review → impact.analyze` — depends_on 声明了，源码中找不到对应调用
- `code-skill → memory.recall` — depends_on 声明了，源码中找不到对应调用
- `model-fallback → llm.list_models` — depends_on 声明了，源码中找不到对应调用
