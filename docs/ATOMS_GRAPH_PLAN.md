# codeagent-minimal 编排契约改动方案 v1

> 状态：**定稿待实施**。本文只定义改什么、为什么、怎么验收，不含代码实施。
> 基线时间：2026-10-23　｜　对象：`D:\codeagent-minimal`（42 原子 / 165 能力 / 166 个 py 文件，纯标准库）
> 配套材料：`docs/ATOMS_GRAPH_DESIGN.md`（设计论述）、`docs/atoms_graph.html`（完整性图，2026-10-23 快照）。
> 机读图 `atoms_graph.json` 与体检清单 `atoms_graph_report.md` 属生成物，待一期脚本落地后随 `--check-drift` 一并入库。

---

## 0. 这份方案要解决的问题

不是"再检查一遍代码有没有 bug"，而是把三件已经存在但互相不知道对方存在的东西对齐：**声明（manifest）、实现（main.py 的注册表）、调用（全仓 run_capability 调用点）**。

现状实测出的四个事实：

| 事实 | 数字 | 后果 |
|---|---|---|
| 能力声明完整、挂载完整 | 165/165，42 个原子逐条相等 | 原子本身完备，问题是没人调 |
| 有真实调用入口的原子 | 17 / 41 | 24 个零调用原子完整但零入口 |
| 能力被调到 | 27 / 165（16.4%） | 83.6% 的能力没有可执行入口 |
| 原子↔原子调用边 | 0（纯星型） | `depends_on` 5 条全是装饰 |

所以改动目标只有一句：**让"声明的东西"与"实际的发生"之间任何偏离，都在合并前变成红灯，而不是在生产里变成 degraded。**

---

## 1. 基线（实施前必须复算）

```bash
cd D:/codeagent-minimal
python -m pytest tests/ -q                    # 当前 242 passed
python scripts/verify_download_ready.py
python scripts/verify_evals.py && python scripts/run_evals.py
python scripts/constitution_gate_changed.py    # 当前 waived=22
```

图侧基线（由 `build_graph.py` 生成，改动后用同一脚本复算比对）：

| 指标 | 值 |
|---|---|
| 原子 / 能力 | 41 / 165 |
| `run_capability` 调用点 | 73 处（静态可解析 32，动态能力名 41） |
| 有调用边的原子 | 17 |
| 零调用原子 | 24 |
| `depends_on` 声明 / 被真调 | 5 / 0 |
| 命名漂移（能力前缀 ≠ domain） | 25 |
| G1 幻影调用 / G5 契约错位 | 0 / 0 |

> 施工前先确认工作树干净。当前有 3 个未跟踪文件（`docs/社区定位与测试状态测评-2026-10-14.md`、`scripts/verify_declaration_consistency.py`、`scripts/verify_run_id.py`），其中后两个不在仓库里、CI 看不到，会让本地与 CI 口径分叉。先决定它们是入库还是删掉，再开工。

---

## 2. 五条硬约束（全部验收的总纲）

| 编号 | 约束 | 由谁保证 |
|---|---|---|
| **C1** | 只能调用已声明的能力 | 门 G1 + 运行时 `CAP_UNKNOWN` |
| **C2** | 每个能力要么可达，要么显式标 dormant | 门 G2 + manifest `dormant` 字段 |
| **C3** | `depends_on` 必须被真实调用覆盖 | 门 G3 |
| **C4** | 调用点实参必须落在目标签名内 | 门 G5 + 运行时 `BAD_ARGS` |
| **C5** | 原子内跨原子调用必须登记依赖 | 门 G4 |

---

## 3. 改动总览

| # | 类型 | 文件 | 一期 | 二期 | 三期 |
|---|---|---|---|---|---|
| A1 | 新增 | `atoms_graph_core.py` | ● | | |
| A2 | 新增 | `scripts/verify_atoms_graph.py` | ● | | |
| A3 | 新增 | `config/atoms_graph_baseline.json` | ● | | |
| A4 | 新增 | `tests/test_atoms_graph_gate.py` | ● | | |
| A5 | 新增 | `docs/ATOMS_GRAPH.md`（生成物） | ● | | |
| B1 | 修改 | `.github/workflows/ci.yml`（第五道门） | ● | | |
| B2 | 修改 | `codeagent.py`（`graph` 子命令） | ● | | |
| C1 | 修改 | 41 个 `agents/*/*/manifest.json`（dormant / owner） | | ● | |
| C2 | 修改 | `agent_loader.py`（透传新字段 + 校验） | | ● | |
| C3 | 修改 | `agent_runtime.py`（调用链留痕 + 错误码） | | ● | |
| C4 | 修改 | `atomic_base.py`（参数预检，不再吞成 degraded） | | ● | |
| D1 | 修改 | `docs/ATOMS_GUIDE.md`（补齐 24 个零调用原子） | | | ● |
| D2 | 修改 | `config/domain_prefix.json`（命名映射） | | | ● |
| D3 | 修改 | `README.md` / `CHANGELOG.md` | ● | ● | ● |

一期**不碰任何原子代码**，只加"看的能力"。

---

## 4. A1/A2：第五道门 `verify_atoms_graph.py`

### 4.1 为什么拆成 core + 薄壳

抽取逻辑（读 manifest、AST 解析 register、扫调用点、构图、判规则）会被三处使用：CI 门、`codeagent.py graph`、测试。写成一份 `atoms_graph_core.py`，另两个只做参数解析和退出码，避免"CI 判的和 CLI 显示的不一样"。

```
atoms_graph_core.py          # 纯标准库，无副作用，可 import
  scan_manifests(repo)       -> {name: manifest}
  scan_impl(repo, atom)      -> {cap: {method, params, required, has_kwargs, opaque}}
  scan_calls(repo)           -> [(caller_atom|'<编排层>', cap, file, line, kwargs|None)]
  build_graph(repo)          -> {nodes, edges, findings, metrics}
  check(graph, baseline, changed) -> (violations, notes)

scripts/verify_atoms_graph.py  # 薄壳：参数 → core → baseline 过滤 → 退出码
```

### 4.2 接口

```bash
python scripts/verify_atoms_graph.py                 # 全量体检，按 baseline 判红
python scripts/verify_atoms_graph.py --changed-only --base <sha>   # CI 用，只判本次触及
python scripts/verify_atoms_graph.py --write-baseline              # 把当前命中写入台账（需人工审 diff）
python scripts/verify_atoms_graph.py --json                        # 机读输出
python scripts/verify_atoms_graph.py --report out.md --html out.html  # 生成文档产物
python scripts/verify_atoms_graph.py --check-drift                 # 生成物是否与代码同步
```

退出码：`0` 通过；`1` 有未记账的阻断项；`2` 门自身异常（读不到 manifest、解析失败），**不静默放行**。

### 4.3 八条规则（定级与处置）

| 规则 | 级别 | 判据 | 一期处置 |
|---|---|---|---|
| G1 幻影调用 | **P0 阻断** | 调用点能力名不在任何 manifest 的 provides 里 | 新出现即红 |
| G5 契约错位 | **P1 阻断** | 调用点实参名不在目标签名内，或必填参数没给（AST 取 kwargs，跳过 `**kwargs` 目标） | 新出现即红 |
| G4 未登记跨原子调用 | **P1 阻断** | A 原子目录里调用了 B 原子的能力，但 A 的 `depends_on` 没有这条 | 新出现即红 |
| G7 依赖成环 | **P0 阻断** | `depends_on` 图有环 | 新出现即红 |
| G2 未接线能力 | P2 | 声明+挂载，但全仓零调用，且 manifest 未标 `dormant` | 存量记账，新增红 |
| G3 声明未兑现 | P2 | `depends_on` 声明了某能力，该原子代码里没有对应调用 | 存量记账，新增红 |
| G6 命名漂移 | P3 | 能力名前缀 ≠ domain | 只提示，不阻断 |
| G8 order 冲突 | P3 | `registry.order` 与依赖先序不一致 | 只提示，不阻断 |

**关于 G2 的口径**：命中不叫"孤儿能力"，叫"未接线能力"。它记的是编排覆盖度的账，不是原子质量的账。原子本身是完整的（登记在册、能力全挂载、入口可调用），缺的是一条路径或者一句 dormant。

### 4.4 baseline 三条纪律

台账文件 `config/atoms_graph_baseline.json`：

```json
{
  "schema": "atoms-graph-baseline-v1",
  "generated_at": "2026-10-23",
  "entries": [
    {"rule": "G2_ORPHAN_CAPABILITY", "subject": "command-approvals.approval.check",
     "reason": "本季度无调用方，待编排接入或标 dormant", "owner": "郑", "expires": "2027-01-31"},
    {"rule": "G6_NAMING_DRIFT", "subject": "dep-impact.deadcode.scan",
     "reason": "能力名已对外曝光，改名成本高于收益", "owner": "郑", "expires": "2027-06-30"}
  ]
}
```

1. **必须有 expires**，过期即红。台账不能变成永久豁免。
2. **只减不增**在同一 PR 内：新增命中默认红；要用 `--write-baseline` 记账，必须在 PR 描述里写清为什么这次不修。
3. **stale 条目要报**：条目对应的命中已经消失 → P3 提示删除，防止台账虚胖。

### 4.5 增量判定（CI 关键）

`--changed-only --base <sha>` 下，先算 `git diff --name-only base...HEAD`，再映射成"受影响范围"：

| 改了什么 | 硬红范围 |
|---|---|
| 某个原子的 `manifest.json` / `main.py` / 目录内任何 py | 该原子的全部节点、能力、边 |
| 任意 `manifest.json` 的 `provides` / `depends_on` | 该原子 + 所有依赖它的原子 |
| `atomic_base.py` / `agent_runtime.py` / `agent_loader.py` | **全仓**（公共层改动影响所有原子，不允许绕过） |
| 其他文件 | 该文件内的调用点 |

这条"公共层改动 → 全仓红"是防绕过的关键。否则改一下 `run_capability` 就能让 42 个原子的契约全失真而门全绿。

### 4.6 门自己的测试

`tests/test_atoms_graph_gate.py`，用 `tmp_path` 造迷你仓库，至少覆盖：

1. 迷你仓库植入 5 类缺陷（错参数名、缺必填、幻影能力、未声明跨原子调用、命名漂移）→ 规则如期命中（期望集相等，不是"至少命中一条"）
2. baseline 过期 → 退出码 1
3. baseline stale → 输出提示但退出码 0
4. 只改公共层 → 全仓红
5. 真实仓库 + 全量 baseline → 退出码 0

第 1 条是防"门写了但永远绿"的关键，必须是**精确集合断言**。

---

## 5. A5：文档产物不许手写

`docs/ATOMS_GRAPH.md` 与 `docs/atoms_graph.html` 都是**生成物**，由 `--report` / `--html` 产出。CI 加一步 `--check-drift`：重新生成到临时目录，与仓库里的版本比对，不一致即红。理由是生成物一旦手改就会腐烂，而腐烂的图比没有图更误导人。

`docs/ATOMS_GRAPH.md` 结构固定 5 段：完整性判据（3 条，全绿/红）、分域完整性表、未接线原子清单（带 dormant 状态）、依赖边是否兑现、规则命中汇总。

---

## 6. B1：CI 接入

`.github/workflows/ci.yml` 的 `test` job 末尾追加一步，与现有宪法门同构：

```yaml
      - name: 原子图谱契约门（只判本次改动；存量走基线台账）
        env:
          ATOMS_GRAPH_BASE: ${{ github.event.pull_request.base.sha || github.event.before }}
        run: |
          python scripts/verify_atoms_graph.py --changed-only --base "$ATOMS_GRAPH_BASE"
          python scripts/verify_atoms_graph.py --check-drift
```

不单开 job：纯标准库、全量跑 < 3 秒，单开 job 只是多一次 checkout。

---

## 7. B2：`codeagent.py graph` 子命令

```bash
python codeagent.py graph            # 人读：完整性 + 命中摘要
python codeagent.py graph --json     # 机读，等同 atoms_graph.json
python codeagent.py graph --html x.html
python codeagent.py graph --check    # 等价于门的全量模式，退出码同上
```

实现照现有子命令的写法（`add_parser` + 分支调 core）。这一条的价值是：**开发和 CI 看的是同一份判定**，而不是本地一个说法、CI 另一个说法。

---

## 8. C1/C2：manifest 扩展 `dormant`

**`registry.json` 是 `agent_loader.scan()` 的产物，绝不能手改**（`load_registry` 发现缺失会自动重建，手改的内容会被覆盖）。声明源在各原子的 `manifest.json`。

新增三个可选字段，全部写在 manifest 里：

```json
{
  "name": "git-ops",
  "dormant": true,
  "dormant_reason": "手册有完整用法，但当前编排链未接入；保留为待接插槽",
  "owner": "郑"
}
```

`agent_loader._load_manifest` 只需透传（它已用 `setdefault` 归一化其余字段，新字段天然不影响校验）。`agent_loader` 再校验两条：

- `dormant: true` 必须同时有非空 `dormant_reason`，否则视为无效声明（防止拿空标记糊过去）
- `dormant: true` 的原子若被真实调用命中，报 P3 提示"标记过期，应清除 dormant"

24 个零调用原子分三类处置（**二期由你拍板**）：

| 类别 | 数量 | 处置 |
|---|---|---|
| 该接线 | 待定 | 补一条从编排层过来的调用路径，纳入 G2 防回归 |
| 该标 dormant | 待定 | 显式声明"我知道它没被调，这是决定不是遗漏" |
| 该删 | 待定 | 不建议。删原子会掉能力覆盖面，且 165 条能力有对外承诺 |

---

## 9. C3：运行时留痕与错误分类

### 9.1 现状的洞

`agent_runtime._run_capability_impl` 已经能区分"能力无原子"和"原子有但不可调用"两种情况，但两者都返回 `_fail(...)`，信封里只有一串中文 error，**没有机器可判的失败类型**。上层拿到 `{ok:false, degraded:true}` 无法判断是自己参数传错、还是原子内部业务失败。

### 9.2 改法

`_fail()` 增加 `code` 字段，取值固定：

| code | 含义 | 产生位置 |
|---|---|---|
| `CAP_UNKNOWN` | 能力名不在任何原子 | `_run_capability_impl` |
| `CAP_NOT_CALLABLE` | 声明了但未挂载/未 ready | `_run_capability_impl` / `AtomicAgent.call` |
| `BAD_ARGS` | 实参名或必填项与签名不符 | `AtomicAgent.call` 的预检（见 §10） |
| `ATOM_FAIL` | 原子内部业务异常 | `AtomicAgent.call` 的 except |
| `DEPTH` / `BUDGET` / `TIMEOUT` | 派活深度 / 预算 / 超时 | 现有拦截点 |

信封向后兼容：`code` 是新增字段，旧消费方读不到就当没有。

### 9.3 调用链留痕

`_try_event_log(runtime, capability, inputs, result)` 已存在（fail-open，只记入参名与成败）。扩两个字段：

- `caller`：调用方的能力名。现在 `_dispatch_stack` 只有 `depth` 和 `cap`（存的是 max_depth 上限，不是能力名），需要在 `run_capability` 里维护一个 `_call_chain` 栈，入栈时压 capability、出栈时弹出。
- `error_code`：`result["code"]` 有就带上。

这样事件流就能回答"谁调了谁、怎么失败的"，动态调用（41 处能力名靠变量拼的）也终于有了回灌来源。

---

## 10. C4：`AtomicAgent.call` 不再把参数错吞成 degraded

现在 `except Exception` 一网打尽，`TypeError: _review() got an unexpected keyword argument 'pathx'` 会被包成 `degraded:true`，调用方只看到"降级了"，根因在日志里消失。

改法：**进 try 之前先 bind 一次**。

```python
import inspect
try:
    _sig.bind(**kwargs)          # 只做形参匹配，不执行
except TypeError as e:
    return self._envelope(False, error=f"BAD_ARGS: {e}", degraded=False,
                          data={"code": "BAD_ARGS", "accepted": list(_params)})
```

`bind` 失败 → 明确 `BAD_ARGS`，`degraded=False`（这不是"降级"，是调用方写错了）。`bind` 通过后再进原来的 try，业务异常归 `ATOM_FAIL`。

签名对象按能力缓存（原子加载后签名不变），避免每次调用都做 `inspect.signature`。

风险：有原子接受 `**kwargs`，`bind` 一律通过，行为不变；真正被拦下的是写错参数名的调用——正是要拦的。

---

## 11. 分期与依赖顺序

### 一期：只建不改（可独立交付、可独立回滚）

1. `atoms_graph_core.py`（抽取 + 构图 + 规则）
2. `scripts/verify_atoms_graph.py`（薄壳 + baseline 过滤 + 退出码）
3. `config/atoms_graph_baseline.json`（首次由 `--write-baseline` 生成，人工审一遍）
4. `tests/test_atoms_graph_gate.py`（5 组断言，含精确集合的负向验证）
5. `docs/ATOMS_GRAPH.md` + `docs/atoms_graph.html`（生成物）
6. `codeagent.py graph` 子命令
7. `ci.yml` 追加第五道门

**一期不碰任何原子代码，不改 manifest，不改运行时**。这一期结束后，图与报告可以交给 CI 天天重算。

### 二期：把"不在场"变成"有说法"

8. manifest 加 `dormant` / `dormant_reason` / `owner`，24 个零调用原子逐个定性
9. `agent_loader` 校验 dormant 声明有效性 + 过期提示
10. `atomic_base.call` 加 `bind` 预检（BAD_ARGS）
11. `agent_runtime` 加错误码 + `_call_chain` + 事件流扩字段

二期改动集中在公共层，**必须全量回归 242 个测试**，并且逐原子跑一遍 `codeagent.py status --json` 比对 42 个原子的 `provides/registered` 不变。

### 三期：把图变成入口

12. `docs/ATOMS_GUIDE.md` 补齐 24 个零调用原子的详讲（现在只有索引），章节用 `<!-- ATOM: name -->` 锚定，门校验覆盖率 41/41
13. `config/domain_prefix.json` 命名映射，G6 从"提示"改"按映射判"（改不改名见 §13 决策 2）
14. 动态调用回灌：事件流的 `(caller, capability)` 定期回写一张 `config/atoms_graph_observed.json`，门的 G2 用它区分"静态看不见"与"真的没人调"

---

## 12. 验收标准

| # | 标准 | 判定方式 |
|---|---|---|
| 1 | 门在真实仓库跑通 | `python scripts/verify_atoms_graph.py` 退出码 0，耗时 < 3s |
| 2 | 门不是空跑 | `tests/test_atoms_graph_gate.py` 5 组全过，负向用例是**精确集合断言** |
| 3 | 存量不回归 | `pytest tests/ -q` 仍 242 passed（二期后只增不减） |
| 4 | CI 五道门全绿 | push 后 `test` job 全部 step 通过 |
| 5 | 口径一致 | `codeagent.py graph --json` 与 `verify_atoms_graph.py --json` 的 nodes/edges/findings 完全相等 |
| 6 | 生成物不腐烂 | `--check-drift` 在 CI 通过 |
| 7 | 错误可归因（二期） | 每个失败分支都带 `code`，`grep -c 'code='` 覆盖所有 `_fail` 调用点 |
| 8 | 参数错不再被吞（二期） | 构造一个错参数调用，返回 `code=BAD_ARGS` 且 `degraded=false` |

---

## 13. 风险与回滚

| 风险 | 影响 | 对策 |
|---|---|---|
| baseline 变成永久豁免 | 门形同虚设 | 强制 `expires`，过期即红；stale 条目必报 |
| 改 `atomic_base.call` 影响全部 42 原子 | 全仓行为变化 | 二期单独一个 PR，先只加 `bind` 预检（不改变业务异常路径），全量回归后再合 |
| 改 `_dispatch_stack` 影响嵌套派活 | 深度门可能误判 | 新增 `_call_chain` 独立栈，不动原 `depth`/`cap` 语义 |
| 生成物与代码不同步 | 图误导 | `--check-drift` 卡在 CI |
| 全量红一上来就阻断所有人 | 没人能合 PR | 一期只判改动（`--changed-only`），存量进台账 |
| 误把 registry.json 当声明源手改 | 下次 scan 被覆盖，白改 | 在 `registry.json` 文件头无注释可加，改在 README 与方案里写死：**只改 manifest** |

回滚粒度：一期全是新增文件 + 一个 CI step + 一个 CLI 子命令，删掉即回滚，零影响。二期改公共层，按"一个改动一个 commit"分开，出问题单点 revert。

---

## 14. 明确不做

1. **不引入图数据库**（Neo4j / networkx 都不用）。41 个节点、165 条能力，纯 json + 标准库足够，引入即违背"下载即用"。
2. **不让模型生成图**。图必须能确定性重算，否则每次跑出来不一样，就没法当门。
3. **不做全自动依赖发现**。41/73 的调用点能力名是动态拼的，静态分析有天花板；用运行时回灌补，不猜。
4. **不承诺图能保证"能力正确"**。它只保证名字对、参数对、依赖登记对、可达性清楚。能力本身对不对，还是 `test/` + `evals/` + `fuzz/` 的活。

---

## 15. 待你拍板的四件事

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| 1 | 一期门的判红范围 | (a) 只判改动 (b) 一上来全量红 | **(a)**。存量 143 条 P2 + 25 条 P3，全量红会立刻堵死所有 PR |
| 2 | G6 命名漂移怎么处理 | (a) 改 25 个能力名 (b) 建 domain→prefix 映射白名单 | **(b)**。改名要连带改所有调用点和对外文档，收益只是整齐 |
| 3 | 24 个零调用原子怎么办 | (a) 全部接线 (b) 全部标 dormant (c) 逐个定性 | **(c)**。逐个定性，一批接线、一批标 dormant，理由写进 `dormant_reason` |
| 4 | 门的位置 | (a) 现有 `test` job 加一步 (b) 独立 job | **(a)**。全量 3 秒，独立 job 只是多一次 checkout |

这四件事定了，一期就可以开工。方案里其余部分不依赖它们。
