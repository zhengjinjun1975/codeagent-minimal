# CodeAgent Atoms 指南（16 原子）

> 本指南逐个说明 16 个原子智能体：**能力（provides）/ 入参（inputs）/ 返回信封（`{ok, data}`）/ 示例代码**。
> 所有示例均基于**已核实的真实信封输出**（`ok=true` + 真实 `data` 字段），可用两种方式运行：
>
> 1. **原子独立运行** —— 直接实例化原子（`agents/<域>/<原子>/main.py` 自测入口），或经 `AgentRuntime` 进程内能力路由。
> 2. **经统一入口调用** —— `python codeagent.py <子命令> [参数]`，统一运行时调度，产出同样的 `{ok, data}` 信封。
>
> 统一接口契约：每个原子继承 `AtomicAgent`（`atomic_base.py`），实现 `call / run / describe`，
> 统一生命周期 `discovered → loaded → ready`，统一结果信封 `{ok, data}`，失败自动降级 `{ok:false, degraded:true}`。
> 默认**数据不出厂**（本地处理），云端 LLM / 远端 OSV 需显式开启（`--remote / --osv / --llm` / `allow_remote=True`）。

---

## 快速索引

| # | 原子 | 域 | 统一入口子命令 | 能力 (provides 主能力) |
|---|------|-----|----------------|------------------------|
| 1 | `code-review` | `codereview` | `review` | `codereview.review/design/layout/content/lsp` |
| 2 | `code-test` | `test` | `test` | `test.gen/run/tdd/snapshot/affected` |
| 3 | `dep-scan` | `depscan` | `dep-scan` | `depscan.scan/sca/taint/osv` |
| 4 | `code-fuzz` | `fuzz` | `fuzz` | `fuzz.gen/run/property/project` |
| 5 | `code-dispatch` | `dispatch` | `dispatch` | `dispatch.template/budget/verify/conflict/permission` |
| 6 | `llm-router` | `llm` | `llm` / `models` | `llm.generate/review/list_models/registry` |
| 7 | `dep-impact` | `impact` | `impact` | `impact.analyze/circular/coupling` |
| 8 | `code-evolve` | `evolve` | `evolve` | `evolve.refine/skill/self_prompt/tdd` |
| 9 | `code-plan` | `plan` | `plan` | `plan.think/gen` |
| 10 | `code-reuse` | `reuse` | `reuse` | `reuse.local/atom/remote` |
| 11 | `code-memory` | `memory` | `memory` | `memory.save/recall/sediment` |
| 12 | `code-skill` | `skill` | `skill` | `skill.list/load/export/sediment` |
| 13 | `mcp-client` | `mcp` | `mcp` | `mcp.list/connect/tools/call` |
| 14 | `code-project` | `project` | `project` | `project.load/scan/analyze` |
| 15 | `task-state` | `taskstate` | （无 CLI 子命令） | `taskstate.track` |
| 16 | `code-deliver` | `deliver` | `deliver` | `deliver.report/package` |

> 注册索引见 `registry.json`；运行时用 `python codeagent.py status --json` 查看 16 原子 ready/degraded/冲突状态。

---

## 通用调用范式

### 方式 A：统一入口（CLI，推荐用户使用）

```bash
python codeagent.py <子命令> [参数] --json
```

### 方式 B：进程内能力路由（框架/脚本集成）

```python
from agent_runtime import AgentRuntime

rt = AgentRuntime(local_only=True)                 # 默认数据不出厂
res = rt.run_capability("codereview.review", path="sample_target.py", mode="code")
assert res["ok"] is True
print(res["data"])
```

### 方式 C：原子独立运行（单原子自测入口）

```bash
python agents/codereview/code-review/main.py --path sample_target.py   # 各原子 __main__ 自测
```

> 下文每个原子给出 **B（能力路由）** + **A（统一入口）** 双示例，返回信封为**真实核实**的字段。

---

## 1. code-review —— 代码审查

- **能力**：`codereview.review`（语义/安全/架构 0–100 分）、`design`、`layout`、`content`、`lsp`（本地 LSP 诊断）
- **入参**：`path`、`code`、`mode(code|design|layout|content)`、`use_llm`、`reuse_atoms`、`max_complexity`、`lsp`、`lsp_server`
- **返回 `data`**（真实核实）：`files`、`score`、`issues`、`lsp_diagnostics`、`static_issues`、`summary`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("codereview.review", path="sample_target.py", mode="code")
# res["ok"] == True
# res["data"] keys: ['files','score','issues','lsp_diagnostics','static_issues','summary']
print(res["data"]["score"], res["data"]["summary"])
```

```bash
python codeagent.py review sample_target.py --json
python codeagent.py review sample_target.py --mode design --lsp        # 设计维度 + LSP 诊断
```

---

## 2. code-test —— 代码测试

- **能力**：`test.gen`（生成测试）、`run`、`tdd`、`snapshot`、`affected`
- **入参**：`code`、`path`、`target_dir`、`task`
- **返回 `data`**（真实核实）：`test_files`、`summary`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("test.gen", code=open("sample_target.py").read())
# res["data"] keys: ['test_files','summary']
print(res["data"]["summary"])
```

```bash
python codeagent.py test sample_target.py --json            # 冒烟/单元/边界/变异/稳定性
python codeagent.py test sample_target.py --no-mutation     # 跳过变异测试（更快）
```

---

## 3. dep-scan —— 依赖漏洞 SCA + 污点

- **能力**：`depscan.scan`、`sca`、`taint`、`osv`
- **入参**：`target`、`osv_query`、`allow_remote`
- **返回 `data`**（真实核实）：`sca`、`taint`、`total_findings`、`critical_high`、`summary`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("depscan.scan", target=".")          # 数据不出厂，默认不查远端 OSV
# res["data"] keys: ['sca','taint','total_findings','critical_high','summary']
print(res["data"]["total_findings"], res["data"]["summary"])
```

```bash
python codeagent.py dep-scan . --json          # 默认数据不出厂
python codeagent.py dep-scan . --osv           # 显式开启远端 OSV 漏洞库
```

---

## 4. code-fuzz —— 属性模糊测试

- **能力**：`fuzz.gen`（生成用例）、`run`、`property`、`project`
- **入参**：`path`、`funcname`、`iterations`、`timeout`、`seed`、`max_cases`、`properties`、`max_funcs`
- **返回 `data`**（真实核实）：`func`、`cases`、`coverage_hint`、`error`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("fuzz.gen", path="sample_target.py", funcname="add")
# res["data"] keys: ['func','cases','coverage_hint','error']
print(res["data"]["func"], len(res["data"]["cases"]), res["data"]["coverage_hint"])
```

```bash
python codeagent.py fuzz sample_target.py --funcname add --iterations 40 --json
```

---

## 5. code-dispatch —— 派单

- **能力**：`dispatch.template`（5 段派单模板）、`budget`（自适应预算）、`verify`（背靠背验证）、`conflict`（并行冲突）、`permission`（allow/ask/deny 细粒度权限）
- **入参**：`dispatch.template` 用 `background/goal/constraint/redline/deliverable/budget/state_file`；`budget` 用 `task/files_needed/language`
- **返回 `data`**（真实核实）：
  - `template` → 派单文本（背景/目标/约束/红线/产出）
  - `budget` → `{max_iter, api_calls, complexity, hint}`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
tmpl = rt.run_capability("dispatch.template", background="背景A", goal="目标B",
                         constraint="约束C", redline="红线D", deliverable="产出E")
budget = rt.run_capability("dispatch.budget", task="重构模块A", files_needed=2)
# budget data: {'max_iter':4,'api_calls':8,'complexity':'简单','hint':'...'}
print(budget["data"])
```

```bash
python codeagent.py dispatch --task "重构模块A" --files 2 --json   # 派单模板 + 预算
```

> `dispatch.permission/verify/conflict` 无独立 CLI 子命令，经能力路由调用，例如权限判定：

```python
rt.run_capability("dispatch.permission", action="check", resource="rm -rf /etc",
                  resource_type="command",
                  policy={"default": "ask", "rules": [
                      {"type": "command", "pattern": "git *", "effect": "allow"},
                      {"type": "command", "pattern": "rm -rf *", "effect": "deny"}]})
# data: {'decision':'deny','granted':False,'blocked':True,'resource':'rm -rf /etc',...}
```

---

## 6. llm-router —— 多模型路由

- **能力**：`llm.generate`、`review`、`list_models`、`registry`
- **入参**：`messages`、`temp`、`max_tokens`、`local_only`、`config_path`、`provider`、`model`、`api_key`
- **返回 `data`**（真实核实，`list_models`）：`models`、`count`、`providers`、`local_only_gate`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("llm.list_models")
# res["data"] keys: ['models','count','providers','local_only_gate']
print(res["data"]["count"], res["data"]["providers"])
```

```bash
python codeagent.py models --json            # 模型注册表
python codeagent.py llm --action list_models --json
```

> 数据不出厂：`local_only=True` 默认，不注入云端密钥。需远端生成时显式 `--llm`/`--remote`。

---

## 7. dep-impact —— 依赖图影响分析

- **能力**：`impact.analyze`、`circular`、`coupling`
- **入参**：`path`、`symbol`、`impact`、`transitive`
- **返回 `data`**（真实核实）：`targets`、`files`、`entities`、`call_edges`、`coupling`、`circular_imports`、`edges`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("impact.analyze", path="sample_target.py")
# res["data"] keys: ['targets','files','entities','call_edges','coupling','circular_imports','edges']
print(res["data"]["entities"], res["data"]["circular_imports"])
```

```bash
python codeagent.py impact sample_target.py --json
python codeagent.py impact sample_target.py --symbol add --transitive
```

---

## 8. code-evolve —— 自进化

- **能力**：`evolve.refine`、`skill`、`self_prompt`、`tdd`
- **入参**：`task`、`outcome`、`snapshot`、`memdir`、`auto_sediment`、`top_k`
- **返回 `data`**（真实核实）：`observation`、`attribution`、`refinement`、`kept`、`verdict`、`snapshot`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("evolve.refine", task="示例任务", outcome={"ok": True})
# res["data"] keys: ['observation','attribution','refinement','kept','verdict','snapshot']
print(res["data"]["observation"], res["data"]["refinement"])
```

```bash
python codeagent.py evolve --task "示例任务" --outcome '{"ok": true}' --json
python codeagent.py evolve-loop --task "..." --json            # 大自进化闭环
```

---

## 9. code-plan —— 方案设计

- **能力**：`plan.think`、`gen`
- **入参**：`task`、`language`、`domain`、`files_needed`、`spec`
- **返回 `data`**（真实核实，`plan.think`）：`plan`（5 段方案：`背景/目标/约束/红线/产出` + `files_needed` 等）+ 顶层元数据 `task/language/domain/files_needed/constraint_chain/assumptions/questions`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("plan.think", task="修复登录校验漏洞")
# res["data"]["plan"] 含中文 '背景'/'目标'/'约束'/'红线'/'产出' 5 段
print(res["data"]["plan"]["目标"], res["data"]["plan"]["constraint_chain"])
```

```bash
python codeagent.py plan --task "修复登录校验漏洞" --json
```

---

## 10. code-reuse —— 代码复用检索

- **能力**：`reuse.local`、`atom`、`remote`
- **入参**：`content`、`path`、`top_k`、`task`
- **返回 `data`**（真实核实）：`suggestions`、`count`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("reuse.local", content="def add(a,b): return a+b")
# res["data"] keys: ['suggestions','count']
print(res["data"]["count"])
```

```bash
python codeagent.py reuse --content "def add(a,b): return a+b" --json
```

---

## 11. code-memory —— 记忆沉淀

- **能力**：`memory.save`、`recall`、`sediment`
- **入参**：`findings`、`task`、`memdir`、`top_k`
- **返回 `data`**（真实核实）：`added`、`lessons`、`memdir`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("memory.save", findings="示例经验：优先用标准库", task="demo")
# res["data"] keys: ['added','lessons','memdir']
print(res["data"]["added"], res["data"]["memdir"])
```

```bash
python codeagent.py memory --findings '{"经验":"示例"}' --task demo --json
```

---

## 12. code-skill —— SKILL 技能资产

- **能力**：`skill.list`、`load`、`export`、`sediment`
- **入参**：`paths`、`name`、`content`、`task`、`action`、`bucket`、`memdir`、`frontmatter`
- **返回 `data`**（真实核实，`list`）：`skills`、`count`、`search_paths`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("skill.list")
# res["data"] keys: ['skills','count','search_paths']
print(res["data"]["count"], res["data"]["search_paths"])
```

```bash
python codeagent.py skill --action list --json
python codeagent.py skill --action load --name code-review --json
```

---

## 13. mcp-client —— MCP 工具接入

- **能力**：`mcp.list`、`connect`、`tools`、`call`
- **入参**：`server`、`endpoint`、`command`、`tools`、`timeout`、`local_only`、`allow_remote`、`allow_tools`
- **返回 `data`**（真实核实，`list`）：`servers`、`count`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("mcp.list")
# res["data"] keys: ['servers','count']
print(res["data"]["count"])
```

```bash
python codeagent.py mcp --action tools --json
python codeagent.py mcp --action call --server <name> --tools '{"tool":"f"}'
```

> `allow_remote`/`allow_tools` 需显式开启，默认仅列本地工具，数据不出厂。

---

## 14. code-project —— 项目分析

- **能力**：`project.load`、`scan`、`analyze`
- **入参**：`path`、`file_pattern`、`max_complexity`、`impact`
- **返回 `data`**（真实核实，`load`）：`files`、`snapshot`、`count`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("project.load", path=".")
# res["data"] keys: ['files','snapshot','count']
print(res["data"]["count"])
```

```bash
python codeagent.py project . --json
```

---

## 15. task-state —— 跨会话任务状态

- **能力**：`taskstate.track`
- **入参**：`task`、`action(new|set|ev|gate)`、`tid`、`state`、`progress`、`evidence`、`gate`、`status_file`
- **返回 `data`**（真实核实）：`task_id`、`action`、`progress`、`file`、`status`、`evidence`
- **说明**：状态文件默认落 `仓库/.taskstate/`，可用 env `TASK_STATE_DIR` 覆盖（可移植、零硬编码）。无独立 CLI 子命令，经能力路由或原子自测入口调用。

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("taskstate.track", task="实现add", action="new")
# res["data"] keys: ['task_id','action','progress','file','status','evidence']
print(res["data"]["task_id"], res["data"]["status"], res["data"]["file"])
# action 还有: set(status,progress) / ev(evidence) / gate(gate)
```

```bash
python agents/taskstate/task-state/main.py --task "实现 add(a,b)" --action new    # 原子独立运行
TASK_STATE_DIR=/tmp/states python agents/taskstate/task-state/main.py --task "T" --action set --state done
```

---

## 16. code-deliver —— 交付验收报告

- **能力**：`deliver.report`、`package`
- **入参**：`chain`、`outputs`、`evidence`
- **返回 `data`**（真实核实）：`report`、`verdict`、`ok_steps`

```python
from agent_runtime import AgentRuntime
rt = AgentRuntime(local_only=True)
res = rt.run_capability("deliver.report",
                        chain=["review"],
                        outputs={"review": {"ok": True, "data": {"score": 88}}})
# res["data"] keys: ['report','verdict','ok_steps']
print(res["data"]["verdict"])
```

```bash
python codeagent.py deliver --chain "think,gen,review,test,evolve" --json
```

---

## 组装链与护栏

16 原子经统一运行时按能力依赖（`depends_on`）做拓扑排序 + 冲突检测，可任意组装成链：

```bash
# 安全·质量组装链：review + dep-scan + fuzz 协同
python codeagent.py guard sample_target.py --json

# 通用组装链：think→gen→review→test→evolve
python codeagent.py chain --task "修复登录校验漏洞" --code '<code>' --language python --json

# 运行时全貌（16 原子 ready/degraded/冲突）
python codeagent.py status --json
```

> 组装链的节点即原子，节点间传递 `{ok, data}` 信封作为下游入参。跨框架编排见 `docs/INTEGRATION_GUIDE.md`。
