# 契约总账（声明 ↔ 实现）

> 本文件由 `scripts/export_contracts.py --emit` 生成，**禁手写**。
> 改完 manifest 或能力签名后重跑一次；`--check` 会在有断链时失败。

## 这份账在查什么

| 方向 | 含义 | 处理 |
|------|------|------|
| 断链 | manifest 声明了这个输入，但该原子所有能力都不接受 | **硬伤**，`--check` 时 exit 1 |
| 漏记 | 能力接受这个参数，manifest 没声明 | 清单不完整，只列出不挡门 |
| 判不了 | 能力吃 `**kwargs` 或签名静态解析不出 | 如实标注，不冒充通过 |

## 总数

| 项 | 数 |
|----|----|
| 原子 | 41 |
| 能力 | 166 |
| 声明输入 | 278 |
| 断链 | 0 |
| 漏记 | 56 |
| 可静态判定的原子 | 35 / 41 |

判不了的原子（吃 `**kwargs` 或签名不透明）：`code-review`、`deadcode`、`doc-freshness`、`git-ops`、`mcp-client`、`method-impact`

## 断链（必须为 0）

无。所有声明的输入都能被实现接住。

## 按域汇总

| 原子 | 域 | 能力数 | 声明输入 | 断链 | 漏记 |
|------|----|--------|----------|------|------|
| `arch-review` | archreview | 4 | 1 | 0 | 0 |
| `atomicity-audit` | atomic | 3 | 2 | 0 | 0 |
| `browser-smoke` | web | 2 | 8 | 0 | 0 |
| `bug-deep` | bugdeep | 4 | 5 | 0 | 3 |
| `cli-providers` | subagent | 3 | 5 | 0 | 0 |
| `code-deliver` | deliver | 2 | 3 | 0 | 1 |
| `code-dispatch` | dispatch | 9 | 23 | 0 | 5 |
| `code-evolve` | evolve | 4 | 6 | 0 | 4 |
| `code-fuzz` | fuzz | 4 | 8 | 0 | 0 |
| `code-implement` | code | 2 | 7 | 0 | 0 |
| `code-memory` | memory | 3 | 4 | 0 | 3 |
| `code-plan` | plan | 2 | 5 | 0 | 1 |
| `code-project` | project | 3 | 4 | 0 | 0 |
| `code-reuse` | reuse | 3 | 3 | 0 | 0 |
| `code-review` | codereview | 9 | 11 | 0 | 6 |
| `code-runloop` | code | 2 | 9 | 0 | 2 |
| `code-skill` | skill | 4 | 8 | 0 | 3 |
| `code-test` | test | 8 | 9 | 0 | 10 |
| `command-approvals` | approval | 5 | 8 | 0 | 1 |
| `complexity-gate` | model | 1 | 1 | 0 | 3 |
| `context-compact` | context | 9 | 16 | 0 | 5 |
| `deadcode` | impact | 2 | 2 | 0 | 0 |
| `dep-impact` | impact | 3 | 4 | 0 | 0 |
| `dep-scan` | depscan | 5 | 4 | 0 | 0 |
| `doc-freshness` | docs | 3 | 2 | 0 | 1 |
| `domain-review` | domain | 2 | 4 | 0 | 0 |
| `event-log` | memory | 4 | 8 | 0 | 0 |
| `git-ops` | tools | 5 | 7 | 0 | 1 |
| `guard` | guard | 7 | 13 | 0 | 1 |
| `llm-router` | llm | 4 | 8 | 0 | 0 |
| `localized` | locality | 3 | 6 | 0 | 0 |
| `mcp-client` | mcp | 5 | 14 | 0 | 2 |
| `method-impact` | impact | 2 | 6 | 0 | 0 |
| `minimalist-style` | codereview | 3 | 3 | 0 | 0 |
| `model-fallback` | model | 7 | 11 | 0 | 0 |
| `ontology-review` | ontology | 2 | 4 | 0 | 0 |
| `process-sandbox` | sandbox | 5 | 10 | 0 | 3 |
| `secret-vault` | secrets | 6 | 7 | 0 | 0 |
| `security-scan` | security | 5 | 3 | 0 | 1 |
| `session` | session | 6 | 7 | 0 | 0 |
| `task-state` | taskstate | 1 | 9 | 0 | 0 |

## 逐原子明细


### arch-review

- 域：archreview ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（4）：`archreview.boundary`、`archreview.intent`、`archreview.layers`、`archreview.surface`
- 声明输入：`path`
- 声明输出：`layers`、`by_layer`、`violations`、`boundaries`、`inventory`、`missing`、`summary`
- 实现接受：`path`

### atomicity-audit

- 域：atomic ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`atomicity.breaks`、`atomicity.manifest`、`atomicity.registry`
- 声明输入：`agents_dir`、`registry_path`
- 声明输出：`ok`、`issues`、`agents`、`registry_mismatch`、`breaks`、`verdict`
- 实现接受：`agents_dir`、`registry_path`

### browser-smoke

- 域：web ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`browsersmoke.run`、`ui.smoke`
- 声明输入：`url`、`login`、`routes`、`chrome_path`、`port`、`headless`、`wait_sec`、`min_body`
- 声明输出：`results`、`summary`、`ok`
- 实现接受：`chrome_path`、`headless`、`login`、`min_body`、`port`、`routes`、`url`、`wait_sec`

### bug-deep

- 域：bugdeep ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（4）：`bugdeep.adv`、`bugdeep.model`、`bugdeep.poc`、`bugdeep.rule`
- 声明输入：`path`、`code`、`sink`、`title`、`rules_file`
- 声明输出：`attack_surface`、`findings`、`confirmed`、`evidence`、`rule`、`summary`
- 实现接受：`code`、`path`、`rules_file`、`run`、`sink`、`timeout`、`title`、`verify`
- 漏记（清单没写、实现接受）：`run`、`timeout`、`verify`

### cli-providers

- 域：subagent ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`subagent.dispatch`、`subagent.probe`、`subagent.providers`
- 声明输入：`task`、`provider`、`cwd`、`timeout`、`name`
- 声明输出：`providers`、`count`、`usable`、`ok`、`degraded`、`provider`
- 实现接受：`cwd`、`name`、`provider`、`task`、`timeout`
- 权限：exec，不联网

### code-deliver

- 域：deliver ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`deliver.package`、`deliver.report`
- 声明输入：`chain`、`outputs`、`evidence`
- 声明输出：`report`、`artifacts`、`verdict`
- 实现接受：`artifacts`、`chain`、`evidence`、`outputs`
- 漏记（清单没写、实现接受）：`artifacts`

### code-dispatch

- 域：dispatch ｜ 版本：0.4.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（9）：`dispatch.assert_exit`、`dispatch.budget`、`dispatch.chain_default`、`dispatch.chain_select`、`dispatch.conflict`、`dispatch.permission`、`dispatch.template`、`dispatch.verify`、`dispatch.verify_artifact`
- 声明输入：`background`、`goal`、`constraint`、`redline`、`deliverable`、`task`、`files_needed`、`language`、`claims`、`workdir`、`rerun_cmd`、`tasks`、`workspace`、`policy`、`action`、`resource`、`resource_type`、`target`、`expect`、`mode`、`script_path`、`templates_path`、`name`
- 声明输出：`template`、`budget`、`items`、`summary`、`conflicts`、`safe`、`recommendation`、`decision`、`policy`、`passed`、`checks`、`exit_code`、`stdout_tail`、`ok`、`matched`、`source`、`chain`、`template_name`、`score`、`hits`、`dropped`、`reason`、`default`
- 实现接受：`action`、`available`、`background`、`budget`、`claims`、`constraint`、`decision`、`deliverable`、`expect`、`files_needed`、`goal`、`language`、`mode`、`name`、`policy`、`redline`、`rerun_cmd`、`resource`、`resource_type`、`rules`、`script_path`、`state_file`、`target`、`task`、`tasks`、`templates_path`、`workdir`、`workspace`
- 漏记（清单没写、实现接受）：`available`、`budget`、`decision`、`rules`、`state_file`
- 权限：read/write/exec，不联网

### code-evolve

- 域：evolve ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（4）：`evolve.refine`、`evolve.self_prompt`、`evolve.skill`、`evolve.tdd`
- 依赖：`memory.recall`
- 声明输入：`task`、`outcome`、`snapshot`、`memdir`、`auto_sediment`、`top_k`
- 声明输出：`observation`、`attribution`、`refinement`、`kept`、`verdict`、`skills`、`prompt`、`tdd`
- 实现接受：`action`、`auto_sediment`、`bucket`、`max_fix`、`memdir`、`outcome`、`path`、`snapshot`、`task`、`top_k`
- 漏记（清单没写、实现接受）：`action`、`bucket`、`max_fix`、`path`

### code-fuzz

- 域：fuzz ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（4）：`fuzz.gen`、`fuzz.project`、`fuzz.property`、`fuzz.run`
- 声明输入：`path`、`funcname`、`iterations`、`timeout`、`seed`、`max_cases`、`properties`、`max_funcs`
- 声明输出：`func`、`cases`、`coverage_hint`、`runs`、`crashed`、`unhandled`、`failures`、`ok`、`details`
- 实现接受：`funcname`、`iterations`、`max_cases`、`max_funcs`、`path`、`properties`、`seed`、`timeout`

### code-implement

- 域：code ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`code.implement`、`code.write`
- 声明输入：`task`、`language`、`path`、`output_dir`、`domain`、`loop`、`max_iter`
- 声明输出：`files`、`score`、`summary`、`issues`、`versions`、`written`、`write_error`、`engine`
- 实现接受：`domain`、`language`、`loop`、`max_iter`、`output_dir`、`path`、`task`

### code-memory

- 域：memory ｜ 版本：0.1.1 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`memory.recall`、`memory.save`、`memory.sediment`
- 声明输入：`findings`、`task`、`memdir`、`top_k`
- 声明输出：`added`、`lessons`、`prompt`、`skills`
- 实现接受：`action`、`bucket`、`conf`、`findings`、`memdir`、`task`、`top_k`
- 漏记（清单没写、实现接受）：`action`、`bucket`、`conf`

### code-plan

- 域：plan ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`plan.gen`、`plan.think`
- 依赖：`taskstate.track`
- 声明输入：`task`、`language`、`domain`、`files_needed`、`spec`
- 声明输出：`plan`、`assumptions`、`files_needed`、`constraint_chain`、`questions`、`files`、`score`
- 实现接受：`code`、`domain`、`files_needed`、`language`、`spec`、`task`
- 漏记（清单没写、实现接受）：`code`

### code-project

- 域：project ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`project.analyze`、`project.load`、`project.scan`
- 声明输入：`path`、`file_pattern`、`max_complexity`、`impact`
- 声明输出：`files`、`snapshot`、`issues`、`summary`
- 实现接受：`file_pattern`、`impact`、`max_complexity`、`path`

### code-reuse

- 域：reuse ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`reuse.atom`、`reuse.local`、`reuse.remote`
- 声明输入：`content`、`path`、`top_k`
- 声明输出：`suggestions`、`atoms`、`count`
- 实现接受：`content`、`path`、`top_k`

### code-review

- 域：codereview ｜ 版本：0.2.1 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（9）：`codereview.content`、`codereview.deep`、`codereview.design`、`codereview.layout`、`codereview.light`、`codereview.lsp`、`codereview.review`、`codereview.self_eval`、`codereview.semantic`
- 依赖：`impact.analyze`
- 声明输入：`path`、`code`、`mode`、`use_llm`、`reuse_atoms`、`max_complexity`、`lsp`、`lsp_server`、`semantic`、`denoise`、`base`
- 声明输出：`score`、`issues`、`static_issues`、`model`、`reuse_suggestions`、`lsp_diagnostics`、`severity_summary`、`self_eval`、`human_review`、`security_baseline`、`dataflow_findings`、`adversarial`、`changed_files`
- 实现接受：`base`、`code`、`denoise`、`file`、`findings`、`fp`、`lsp`、`lsp_server`、`max_complexity`、`missed`、`mode`、`path`、`reuse_atoms`、`semantic`、`stats_only`、`timeout`、`use_llm`
- 对账：**判不了**（吃 `**kwargs` 或签名不透明）
- 漏记（清单没写、实现接受）：`file`、`findings`、`fp`、`missed`、`stats_only`、`timeout`

### code-runloop

- 域：code ｜ 版本：0.3.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`code.patch`、`code.runloop`
- 声明输入：`task`、`path`、`output_dir`、`verify`、`language`、`max_iterations`、`fake_model`、`edits`、`write_root`
- 声明输出：`files`、`summary`、`verify_passed`、`verify_cmd`、`turns_used`、`tool_log`、`trace`、`engine`、`last_model_text`、`applied`、`changed`
- 实现接受：`edits`、`fake_model`、`language`、`max_iterations`、`model_config`、`output_dir`、`path`、`task`、`timeout`、`verify`、`write_root`
- 漏记（清单没写、实现接受）：`model_config`、`timeout`
- 权限：read/write/exec/net，需要网络

### code-skill

- 域：skill ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（4）：`skill.export`、`skill.list`、`skill.load`、`skill.sediment`
- 依赖：`memory.recall`
- 声明输入：`paths`、`name`、`content`、`task`、`action`、`bucket`、`memdir`、`frontmatter`
- 声明输出：`skills`、`skill`、`exported`、`added`、`path`
- 实现接受：`action`、`bucket`、`content`、`description`、`dest_dir`、`frontmatter`、`memdir`、`name`、`path`、`paths`、`task`
- 漏记（清单没写、实现接受）：`description`、`dest_dir`、`path`

### code-test

- 域：test ｜ 版本：0.3.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（8）：`test.affected`、`test.coverage_analysis`、`test.gen`、`test.project`、`test.run`、`test.select`、`test.snapshot`、`test.tdd`
- 声明输入：`code`、`path`、`target_dir`、`task`、`project_root`、`test_map`、`human_review`、`timeout`、`exclude`
- 声明输出：`test_files`、`smoke`、`coverage`、`unit`、`boundary`、`mutation`、`stability`、`red_green`、`coverage_analysis`、`human_review`、`smoke_failed`、`summary`、`skipped`
- 实现接受：`args_by_func`、`changed_files`、`code`、`do_boundary`、`do_mutation`、`do_stability`、`exclude`、`funcs`、`human_review`、`max_iter`、`path`、`project_root`、`run`、`snapshot_dir`、`target_dir`、`task`、`test_map`、`timeout`、`transitive`
- 漏记（清单没写、实现接受）：`args_by_func`、`changed_files`、`do_boundary`、`do_mutation`、`do_stability`、`funcs`、`max_iter`、`run`、`snapshot_dir`、`transitive`
- 权限：read/write/exec，不联网

### command-approvals

- 域：approval ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（5）：`approval.check`、`approval.classify`、`approval.orchestrate`、`approval.policy`、`approval.resolve`
- 声明输入：`resource`、`resource_type`、`policy`、`decision`、`user_choice`、`runner`、`cache_path`、`max_escalations`
- 声明输出：`decision`、`reason`、`rule`、`tier`、`verdict`、`granted`、`matched_keywords`、`pending`、`executed`、`profile`、`attempts`、`cached`
- 实现接受：`cache_path`、`custom`、`decision`、`max_escalations`、`policy`、`resource`、`resource_type`、`runner`、`user_choice`
- 漏记（清单没写、实现接受）：`custom`
- 权限：read/write，不联网

### complexity-gate

- 域：model ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（1）：`model.assess_complexity`
- 声明输入：`text`
- 声明输出：`echo`
- 实现接受：`hints`、`rules`、`task`、`text`
- 漏记（清单没写、实现接受）：`hints`、`rules`、`task`
- 权限：无，不联网

### context-compact

- 域：context ｜ 版本：0.5.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（9）：`context.budget`、`context.compact`、`context.compaction_cost`、`context.disclose`、`context.edit`、`context.estimate`、`context.offload`、`context.reveal`、`context.store`
- 声明输入：`data`、`text`、`steps`、`max_tokens`、`keep`、`cap`、`content`、`label`、`keep_head`、`keep_tail`、`store_dir`、`registry_desc`、`task`、`top_k`、`before`、`after`
- 声明输出：`tokens`、`compact`、`_compact`、`used_tokens`、`budget`、`overshoot`、`compacted`、`dropped`、`dropped_capabilities`、`action`、`enforced`、`inlined`、`offloaded_path`、`original_tokens`、`inlined_tokens`、`loaded`、`deferred_count`、`verdict`、`recoverable`、`lost`、`lost_tokens`、`reacquisition_hops`
- 实现接受：`after`、`before`、`cap`、`content`、`data`、`edits`、`handle`、`items`、`keep`、`keep_head`、`keep_tail`、`label`、`limit`、`max_tokens`、`offset`、`registry_desc`、`steps`、`store_dir`、`task`、`text`、`top_k`
- 漏记（清单没写、实现接受）：`edits`、`handle`、`items`、`limit`、`offset`
- 权限：read/write，不联网

### deadcode

- 域：impact ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`deadcode.scan`、`deadcode.stats`
- 声明输入：`path`、`threshold`
- 声明输出：`total`、`live`、`dead_count`、`ratio`、`roots`、`dead`、`dead_by_file`
- 实现接受：`path`、`threshold`
- 对账：**判不了**（吃 `**kwargs` 或签名不透明）

### dep-impact

- 域：impact ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`impact.analyze`、`impact.circular`、`impact.coupling`
- 声明输入：`path`、`symbol`、`impact`、`transitive`
- 声明输出：`files`、`entities`、`edges`、`coupling`、`circular_imports`、`impact`
- 实现接受：`impact`、`path`、`symbol`、`transitive`

### dep-scan

- 域：depscan ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（5）：`depscan.chainbreak`、`depscan.osv`、`depscan.sca`、`depscan.scan`、`depscan.taint`
- 声明输入：`target`、`osv_query`、`allow_remote`、`repos`
- 声明输出：`deps`、`vulns`、`sca`、`taint`、`findings`、`total_findings`、`summary`、`broken`、`checks`、`by_tier`
- 实现接受：`allow_remote`、`osv_query`、`repos`、`target`

### doc-freshness

- 域：docs ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`doc.anchor`、`doc.draft`、`doc.stale`
- 声明输入：`path`、`root`
- 声明输出：`file`、`anchors`、`ok_count`、`stale`、`unresolved`、`total_anchors`、`stale_count`、`unresolved_count`、`draft_count`、`guarded_count`、`managed_zones`、`drafts`、`guarded`
- 实现接受：`out`、`path`、`root`
- 对账：**判不了**（吃 `**kwargs` 或签名不透明）
- 漏记（清单没写、实现接受）：`out`
- 权限：read/write，不联网

### domain-review

- 域：domain ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`domain.imports`、`domain.valve`
- 声明输入：`repos`、`target`、`rules`、`data`
- 声明输出：`ok`、`broken`、`topology`、`violations`、`rules`、`verdict`
- 实现接受：`data`、`repos`、`rules`、`target`

### event-log

- 域：memory ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（4）：`event.append`、`event.checkpoint`、`event.replay`、`event.timeline`
- 声明输入：`session_key`、`kind`、`payload`、`outcome`、`state`、`from_seq`、`to_seq`、`store_dir`
- 声明输出：`seq`、`events`、`timeline`、`restored_state`、`up_to_seq`、`state_digest`
- 实现接受：`from_seq`、`kind`、`outcome`、`payload`、`session_key`、`state`、`store_dir`、`to_seq`
- 权限：read/write，不联网

### git-ops

- 域：tools ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（5）：`git.branch`、`git.commit`、`git.diff`、`git.log`、`git.status`
- 声明输入：`path`、`action`、`name`、`message`、`n`、`staged`、`base`
- 声明输出：`branch`、`clean`、`changed`、`diff`、`files`、`stat`、`commits`、`sha`、`committed`、`branches`、`current`、`created`、`switched`
- 实现接受：`action`、`base`、`message`、`n`、`name`、`path`、`paths`、`staged`
- 对账：**判不了**（吃 `**kwargs` 或签名不透明）
- 漏记（清单没写、实现接受）：`paths`
- 权限：exec，不联网

### guard

- 域：guard ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（7）：`guard.check`、`guard.exit_intercept`、`guard.loop_guard`、`guard.pipeline`、`guard.post`、`guard.pre`、`guard.selfcheck`
- 声明输入：`path`、`code`、`paths`、`govern`、`task`、`artifacts`、`checklist`、`session_key`、`file_path`、`bump`、`threshold`、`store_dir`、`since`
- 声明输出：`issues`、`secrets`、`verdict`、`files`、`summary`、`total`、`passed`、`failed`、`next_prompt`、`items`、`stalled`、`count`、`hint`、`evidence`
- 实现接受：`artifacts`、`bump`、`checklist`、`code`、`file_path`、`govern`、`path`、`paths`、`session_key`、`since`、`spec`、`store_dir`、`task`、`threshold`
- 漏记（清单没写、实现接受）：`spec`
- 权限：read/write/exec/net，需要网络

### llm-router

- 域：llm ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（4）：`llm.generate`、`llm.list_models`、`llm.registry`、`llm.review`
- 声明输入：`messages`、`temp`、`max_tokens`、`local_only`、`config_path`、`provider`、`model`、`api_key`
- 声明输出：`content`、`model`、`provider`、`type`、`models`、`registry`
- 实现接受：`api_key`、`config_path`、`local_only`、`max_tokens`、`messages`、`model`、`provider`、`temp`

### localized

- 域：locality ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`local.audit`、`local.chain`、`local.route`
- 声明输入：`target`、`code`、`local_only`、`candidates`、`config_path`、`timeout`
- 声明输出：`ok`、`signals`、`route`、`local_up`、`verdict`、`fallback`
- 实现接受：`candidates`、`code`、`config_path`、`local_only`、`target`、`timeout`

### mcp-client

- 域：mcp ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（5）：`mcp.call`、`mcp.connect`、`mcp.guard`、`mcp.list`、`mcp.tools`
- 声明输入：`server`、`endpoint`、`command`、`tools`、`timeout`、`local_only`、`allow_remote`、`allow_tools`、`action`、`text`、`description`、`name`、`tool`、`source`
- 声明输出：`servers`、`tools`、`result`、`server`、`guard`、`findings`、`text`
- 实现接受：`action`、`allow_remote`、`allow_tools`、`arguments`、`command`、`description`、`endpoint`、`local_only`、`name`、`server`、`servers`、`source`、`text`、`timeout`、`tool`、`tools`
- 对账：**判不了**（吃 `**kwargs` 或签名不透明）
- 漏记（清单没写、实现接受）：`arguments`、`servers`
- 权限：read/exec/net，需要网络

### method-impact

- 域：impact ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`impact.kind`、`impact.method`
- 声明输入：`path`、`symbol`、`transitive`、`max_depth`、`caller`、`to`
- 声明输出：`symbol`、`impact`、`paths`、`kind`、`entities`、`edges`
- 实现接受：`caller`、`max_depth`、`path`、`symbol`、`to`、`transitive`
- 对账：**判不了**（吃 `**kwargs` 或签名不透明）

### minimalist-style

- 域：codereview ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（3）：`minimal.deps`、`minimal.independent`、`minimal.style`
- 声明输入：`path`、`code`、`strict`
- 声明输出：`ok`、`score`、`issues`、`third_party`、`signals`、`verdict`
- 实现接受：`code`、`path`、`strict`

### model-fallback

- 域：model ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（7）：`model.cache_lookup`、`model.cache_put`、`model.candidates`、`model.cascade`、`model.chain`、`model.route`、`model.rule_match`
- 依赖：`llm.list_models`
- 声明输入：`messages`、`call_model`、`purpose`、`preference`、`local_only`、`candidates`、`text`、`rules`、`ttl_sec`、`store_dir`、`intent`
- 声明输出：`ok`、`verdict`、`provider`、`type`、`result`、`fell_back`、`candidates`、`errors`、`layer`、`shortcut`、`matched`、`handler`、`confidence`、`hit`、`key`、`size`
- 实现接受：`call_model`、`candidates`、`intent`、`local_only`、`messages`、`preference`、`purpose`、`rules`、`store_dir`、`text`、`ttl_sec`
- 权限：read/write，不联网

### ontology-review

- 域：ontology ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（2）：`ontology.chain`、`ontology.quality`
- 声明输入：`web_dir`、`kit_dir`、`data_dir`、`target`
- 声明输出：`ok`、`links`、`issues`、`quality`、`verdict`
- 实现接受：`data_dir`、`kit_dir`、`target`、`web_dir`

### process-sandbox

- 域：sandbox ｜ 版本：0.3.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（5）：`sandbox.exec`、`sandbox.guard`、`sandbox.interpreter`、`sandbox.poc`、`sandbox.validate`
- 声明输入：`code`、`cmd`、`timeout`、`max_output`、`base_dir`、`path`、`base`、`profile`、`approval`、`scope`
- 声明输出：`ran`、`verdict`、`rc`、`wall_s`、`evidence`、`out_tail`、`err_tail`、`timed_out`、`overflow`、`rejected`、`safe`、`path`、`decision`、`refused`、`pending`
- 实现接受：`approval`、`base`、`base_dir`、`cmd`、`code`、`cwd`、`env_clean`、`max_output`、`path`、`profile`、`py`、`scope`、`timeout`
- 漏记（清单没写、实现接受）：`cwd`、`env_clean`、`py`
- 权限：read/write/exec/net，需要网络

### secret-vault

- 域：secrets ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（6）：`secrets.decrypt`、`secrets.encrypt`、`secrets.load`、`secrets.mask`、`secrets.store`、`secrets.vault`
- 声明输入：`plaintext`、`value`、`passphrase`、`name`、`vault_dir`、`bundle`、`keep`
- 声明输出：`salt`、`iv`、`ciphertext`、`tag`、`decrypted`、`stored`、`name`、`masked`、`names`
- 实现接受：`bundle`、`keep`、`name`、`passphrase`、`plaintext`、`value`、`vault_dir`
- 权限：read/write，不联网

### security-scan

- 域：security ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（5）：`security.dim`、`security.govern`、`security.project`、`security.scan`、`security.secret`
- 声明输入：`path`、`code`、`dimension`
- 声明输出：`issues`、`by_dimension`、`by_tier`、`secrets`、`total`、`summary`、`files`
- 实现接受：`code`、`dimension`、`issues`、`path`
- 漏记（清单没写、实现接受）：`issues`

### session

- 域：session ｜ 版本：0.1.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（6）：`session.get`、`session.list`、`session.resume`、`session.start`、`session.status`、`session.step`
- 声明输入：`sid`、`task`、`step_capability`、`inputs`、`output`、`status`、`session_dir`
- 声明输出：`sid`、`created`、`updated`、`steps`、`status`、`step`、`next`、`sessions`、`summary`
- 实现接受：`inputs`、`output`、`session_dir`、`sid`、`status`、`step_capability`、`task`
- 权限：read/write，不联网

### task-state

- 域：taskstate ｜ 版本：0.2.0 ｜ 开源：是 ｜ 入口：`main.py`
- 能力（1）：`taskstate.track`
- 声明输入：`task`、`action`、`tid`、`state`、`progress`、`evidence`、`gate`、`status_file`、`done_evidence`
- 声明输出：`status`、`task_id`、`file`、`progress`、`state`
- 实现接受：`action`、`done_evidence`、`evidence`、`gate`、`progress`、`state`、`status_file`、`task`、`tid`
- 权限：read/write/exec，不联网
