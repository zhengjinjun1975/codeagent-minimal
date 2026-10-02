# Changelog

所有显著变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [0.7.13] - 2026-10-02

### 修：接线门引用错了模块名

0.7.12 同步接线门时带进了母体的模块名（engine_llm），本库应为 code_agent_engine。
本次修正，接线门在本库恢复可跑（26 项断言全过）。

## [0.7.12] - 2026-10-02

### 代码宪法按层加载：评审条文真正接上

- 装载器新增按层读取：`review_text()` / `inject_review()`（缺标记即报错，不静默少一层）。
- 评审路径（`review.py` 的 LLM 审查系统提示）注入评审条文 L1..L5 + 交付前自检 + 例外机制；
  写码路径仍只带常驻核心（实测不含 L1..L5）。
- 接线门把"评审条文齐全、评审路径真注入、两层不重叠"从"暂不检查"改成必须检查。

## [0.7.11] - 2026-10-02

### 补漏：加回"一次只做一件事"一条

v3 去重时把"一次只做一件事 / 无关文件零改动 / 不顺手改格式 / 重构与功能变更分开 / 新依赖默认 0"
连同重复部分一起删掉了（属错删）。本次补回常驻核心，断言列表同步。

## [0.7.10] - 2026-10-02

### 代码宪法 v3：去重 + 分级（写码路径上下文减半）

- **去重**：删掉与系统提示"极简阶梯 + 禁去绿捷径"清单重复的条文，同一件事只说一处。
- **分级**：宪法拆成"常驻核心"（注入写码路径）与"评审条文"（只在评审/复核时用），
  常驻正文从 1622 字降到 **814 字**（约 -50%）；真跑闭环每轮少带约 800 字。
- **判定项一条没少**：执法者判的还是这些，阈值仍单点在 config/constitution_thresholds.json。
- 接线门与用例同步到 v3 口径（常驻核心断言 P0 五条 + C1/C3/C4/C6；评审条文断言 L1..L5 且不在写码路径里）。

## [0.7.9] - 2026-10-02

### 脱敏：清掉本机环境信息

- 全库替换本机环境相关标识：本机采样模块名、采样目录环境变量、本机判决模块名、内部记忆库环境变量名
  与文件名，以及本机绝对路径；对外统一说"外部采样目录""外部记忆库（经环境变量配置）"。
- `agents/memory/code-memory/external_mem.py` 定名（原文件名里带内部产品名），引用同步更新。
- 功能不变：212 条测试全过。

## [0.7.8] - 2026-10-02

### 补上：沙箱、审批、文档草案的测试

- `tests/test_codex_borrow_atoms.py`：补 7 条——沙箱拒绝高危命令、Windows 高危命令被拦、
  匹配前先归一绝对路径、审批策略的拒绝/升级重试/人工确认与拒绝缓存、上下文预算真的被强制执行。
- `tests/test_optimization_p0_atoms.py`：补 3 条文档草案用例——能力已注册可调用、
  有标记的托管块不许手改、没有标记的文档不建议重写。

## [0.7.7] - 2026-10-02

### 修复：0.7.6 误带本机专属代码

0.7.6 提交时文件是 CRLF 换行，我按 \n 匹配没删干净，把本机专属的两段（数据采集旁路、
本机分诊门）连同调用点一起带了进去。本次真正移除，主流程不受影响。

## [0.7.6] - 2026-10-02

### 补上：运行时的步数上限与成本记账

- `agent_runtime.py`：跑链时带步数/重复动作/模型调用上限，超限如实停机；每一步记账
  （步数、模型调用、工具调用、估算 token、耗时），可导出给成本回归比对。
- 新增 `cost_ledger.py`（纯标准库）：成本账本读写与两次运行的可比性判断——提示措辞、
  编排链、努力档、模型任一不同就报"不可比"，不硬比出一个好看的结论。
- 开源版**不含**本机数据采集旁路与本机分诊门（两者都要挂本机环境，属外部依赖），
  相关调用点已移除，主流程不受影响。

## [0.7.5] - 2026-10-02

### 补上：对话入口的意图缓存 + 文件定位边界测试

- `lab/chat_router.py`：常见意图缓存（命中过的问题不必每次再问一遍模型）；回答里带上命中的意图。
- `tests/test_fs_locate_boundary.py`：补一条文件定位边界用例。

## [0.7.4] - 2026-10-02

### 补上：测试脚手架与代码审查的两处改进

- `test_harness.py`：跑目标函数时把它自己的 stdout/stderr 收走（`_quiet_call`），
  免得目标脚本里的 print 冲掉调用方的 --json 输出；加载目标模块时把所在目录临时插进 sys.path，
  同目录的兄弟模块才 import 得到（用完再摘掉）。
- `review.py`：SQL 注入风险除了报问题，还报出命中的行号（`_sql_hit_line`）。

## [0.7.3] - 2026-10-02

### 补上：原子入口导入共用、数据库连接可关闭

- `agent_loader.py`：把"导入原子入口模块"抽成 `import_entry()`，校验器和加载器共用这一份，
  避免两处各写一套导致行为不一致；加载失败的原子额外记录原因（`degraded_reasons`），
  不再只有一个名字看不清为什么没加载起来。
- `lab/history_db.py`：补 `close()`。Windows 上不关 sqlite 连接会占着文件，临时目录删不掉。

## [0.7.2] - 2026-10-02

### 修复：归档后历史读不到

- `agents/memory/event-log/main.py` 的 timeline / replay 原来只读主文件，归档（`_archive/<session>-<起>-<止>.jsonl`）
  里的历史看不见。现在改为主文件与归档段合并后按编号排序再返回（实测：把 3 条事件移进归档段后，
  timeline 仍能看到 3 条、replay 仍能还原状态）。

## [0.7.1] - 2026-10-02

### 修复：事件账本并发写入、验收命令用错 Python

- **事件账本并发写入会丢数据**：`agents/memory/event-log/main.py` 原来先数一遍文件行数再追加
  （`seq = len(read_lines(path)) + 1`），多进程同时写会撞编号、丢记录，而且每写一条都要读整个文件。
  现在改用 `atomic_base.exclusive_file_lock` 加锁，编号改为只读文件尾部（实测 4 进程各写 25 条：
  修前落地 76 行、编号只有 27 个唯一；修后 100 行、编号 1..100 全唯一）。
  `atomic_base.exclusive_file_lock` 一并加入本库（纯标准库，Windows 用 msvcrt，Linux/macOS 用 fcntl）。
- **验收命令用到了 PATH 上的 Python**：`agents/code/code-runloop/run_loop.py` 的默认验收命令写的是
  `python -m pytest`，会落到 PATH 上那个 Python（可能没装 pytest），导致验收永远失败。
  现在改用当前解释器（`sys.executable`），py_compile 两处同样修掉。

## [0.7.0] - 2026-10-02

### 代码宪法：写码硬口径 + 阈值管理 + 执法者

- **代码宪法（强宪法 P0 / 重核心 P1 / 轻外围 P2）**：把"代码极简"落成可判定条文——P0 五条（死码=0、静默失败=0、
  占位/空壳=0、真跑才算完成、不可信输入与安全默认）机器可判；P1 六条按**阈值**判（函数≤50 行、参数≤5、
  嵌套≤3、圈复杂度≤10…）；P2 五条只作提示。条文带"怎么算达标"，不是漂亮话。
- **两条写码路径都注入**：`code_agent_engine.PONYTAIL_SYSTEM`（单发写码）与
  `agents/code/code-runloop/prompts.build_system_prompt()`（真跑闭环）；宪法是**单一真相源**
  （`docs/代码宪法.md` 的 BEGIN/END 标记之间），代码里不另抄一份。
- **阈值单点**：`config/constitution_thresholds.json`；文档与阈值的一致性由门核对，防两边漂。
- **执法者**：`scripts/constitution_gate.py`（量 P0/P1，越限即红；确有理由写 `# 宪法例外: <理由>` 登记放行）；
  `scripts/constitution_survey.py`（全仓体检 + 基线趋势）；`scripts/verify_code_constitution.py`（接线门，含"门真会咬"的自证）。
- **不静默失败**：宪法缺失/标记缺失/超预算一律**抛错**，`inject_or_mark` 退化为**显式缺失标记**，绝不静默少注入一段。
- **门自身的判据也按证据收敛**：排除 `__future__`/`# noqa`/抽象方法桩三类假阳性；"疑似死定义"跨文件证不了 → 降为提示；
  夹具排除走 `.constitutionignore`（内置 `bad_sample*` 约定）。

### 验证
- `scripts/verify_code_constitution.py`：**25 项断言全过**（文件/标记/预算/两路径注入/幂等/缺失必抛/阈值一致/门真会咬）。
- `tests/test_code_constitution.py`：9 项（含阈值-文档一致性）。
- 咬合测试：干净样本 PASS(exit 0) / 违规样本 FAIL(exit 1)，逐类报出（死导入·静默吞错·占位·空壳·明文凭据 / 参数·嵌套·复杂度）。
- **A/B 简单对照**（n=1/臂）：4 个单文件任务里 1 个明显不同（带宪法 9 行 vs 不带 49 行），其余 3 个看不出差别；
  口径与全部证据见 `docs/_review/`。**结论只到"有信号"，不宣称能力已涨**。

## [0.6.1] - 2026-09-23

### 与母体对齐到同一口径 + 边界修复
- **口径对齐**：补齐 3 个核心原子（`event-log` / `secret-vault` / `session`），本库现为 **39 核心原子 / 161 能力**（Lab 加载 41 = 39 核心 + 2 扩展），与母体完全一致；前端与后端接线文件逐字节相同（单一真相源在母体，本库单向同步）。
- **仓库只保留 Lab 一个前端**（`web/` 已在前一版移除）：它同时是「用预设·点一下就跑」与「自己组装·拖拽搭流程」的入口。
- **16 套预设编排（不再有"单原子卡"）**：全量代码审查 / 快速体检 / 修 bug / 写新功能 / 测试补强 / 影响面分析 / 重构精简 / 质量闸门 / 交付验收 / 按任务自动选链 / 深挖 Bug 根因 / 自我进化 / 外部工具·模型分工 / 前端冒烟 / 长任务续跑 / 上手新项目。每张卡 = 一条完整多阶段编排（并行分支 + 按需循环/门控 + 交付），覆盖 39 原子 / 161 能力零遗漏。
- **画布**：按依赖自动布局（主干从左到右横排、并行原子竖着叠、宽扇拆子列）；绘图区自适应内容 + 更大的节点与字号；循环以橙色虚线反馈弧画出（回环看得见）。画布标题栏「编排图」下拉可载入任一套编排或空白自搭，运行名跟随所选编排。
- **边界修复（真机端到端抓出）**：`deliver.report` 缺 `chain`/`outputs` 时不再把整条编排判红——容器型入参缺省即为空（如实表示"没有上游输入"，不猜测拼装）；交付原子的能力签名同步给出默认值。
- **不静默失败**：载入某套编排失败会在画布上显示真实原因（画布保持原样），选择器里单张卡构图失败也不影响其它卡。

### 验证
- 真机端到端（Lab 8098，单进程干净实例）：代码审查 ✅ → 安全扫描 ✅ → 死代码 ✅ → 门控放行 ✅ → 交付报告 ✅，`ok=1`；且**故意不提供** `chain`/`outputs`，边界容错同时验证。
- `pytest -q` 全量；门 `tests/test_capability_consistency.py` 与 `tests/test_security_boundary.py` 一致通过；按 CI 同法做干净检出对等验证。

## [0.6.0] - 2026-09-23

### 单一前端：删掉第二套前端，Lab 成为唯一入口（点一点用预设 / 拖一拖自己搭）
- **删除 `web/`（最小运行前端 + 其 REST 服务）**：原有 9 个动作（status/review/test/chain/guard/evolve/project/git/evals）不再有独立前端，全部并入 Lab，能力一个不丢（`lab/web_viz.py` 的动作口 + 门内白名单校验）。
- **Lab 新增「预设模式」（默认首页）**：11 张预设卡（全量审查 / 快速体检 / 修 bug / 写新功能 / guard 链 / 自动选链 / 黑箱调试 / 交付验收 / 题集评测 / Git 巡检 / 空白画布自己搭），点一下即跑；卡片显示真实「上次结果」，运行时右侧逐步 ✔/⏳/○ 变亮，结果统一落一处（含失败原因）。
- **Lab 组装模式更顺手**：左侧原子「点一下就入编排」并按顺序自动串线（也可拖拽/连线/改参）；预设与组装不割裂——卡片点「✎ 改图」把整张图铺到画布再改。
- **API**：新增 `POST /api/action`（单步动作口，与 git 只读同源）。未知动作 / git 写动作 / chain 缺任务均明确拒绝，不静默。
- **接线同步**：`examples/codeagent_toolkit.http_tool` 与 `examples/web_api_client.py` 改连 Lab（`127.0.0.1:8087`，`/api/action`）；README / INTEGRATION_GUIDE 改为 Lab 单前端说明。
- **门改造（只加不松）**：门由「扫 `web/server.py` 命令面」改为「扫 `lab/web_viz.py` 动作口」（需与白名单一一相等 + `run_action` 执行体存在）；安全边界测试改为离线断言 `lab_config.resolve_target` 的越界拒绝 + 动作口白名单（**新增**未知动作 / git 写动作 / chain 缺任务三项拒绝断言）。

### 验证
- 门：`scripts/verify_capability_consistency.py` / `tests/test_capability_consistency.py` 全绿；`tests/test_security_boundary.py` 8 项全过。
- 真机：Lab（8087）预设卡真跑（全量审查 15 节点图 / 快速体检 2 步 / 题集 / git 等），逐步进度与结果区均真实更新；组装模式点原子自动入图 + 自动串线实测通过；零 JS 报错。

## [0.5.0] - 2026-09-23

### 与母版能力同步（11 个原子升版/入册 + 运行前端 11 预设 + 题集）
- **原子 34 → 36、能力 118 → 145**（`codeagent.py status --json` 实测：`degraded=[]`、`conflicts=[]`）。
- 升版：`code-test` 0.3.0（+`test.project` 项目级验收）、`code-runloop` 0.3.0（+`code.patch`）、`command-approvals` 0.2.0（+`approval.orchestrate`）、`context-compact` 0.5.0（+6 能力，预算**真裁到预算内**）、`code-dispatch` 0.4.0（+3 能力）、`doc-freshness` 0.2.0（+`doc.draft`）、`guard` 0.2.0（+3 能力）、`mcp-client` 0.2.0（+`mcp.guard`）、`model-fallback` 0.2.0（+4 能力）、`process-sandbox` 0.3.0、`task-state` 0.2.0。
- 同步支撑模块：`capability_scope.py`、`mcp_guard.py`、`context_harness.py`、`chain_selector.py`、`verify_gate.py`、`cascade_router.py`、`harness_guard.py`，并更新 `approval_policy.py` / `doc_freshness.py` / `security_scan.py`。
- **脱敏**：`llm.py` 的 key 兜底路径与提示不再指向内部目录（改用 `~/.codeagent/.env`）；`task-state` 注释去掉内部名。新增文件内部名复扫 = 0。
- **文档计数统一**：README / ATOMS_GUIDE（索引表 16 → **36 行**（+`git-ops`））/ INTEGRATION_GUIDE / PROMOTION / CAPABILITY 全部归 36 原子 / 145 能力；新增 `docs/POSITIONING.md`（与开源生态的真实对照：8 个编码代理类 + 20 个质量治理工具类，优势 8 条 / 短板 6 条逐条显性）。
- **新增门** `tests/test_capability_consistency.py`（7 项：registry 自洽 / README 计数 / 白名单文档计数 / 指南索引集合 / 前端中文名覆盖 / server 命令白名单 / 分辨力自检）——**上线即抓出多处真实不一致**（指南 3 处计数、CAPABILITY.md 与前端中文名表各 1 处），已修。
- **新增原子 `git-ops` 0.1.0**（`git.status/diff/log/commit/branch`；只做日常操作，**不暴露 `push`/`reset --hard`/`force`**，`commit` 为受控写）→ 原子 **36**。
- **题集 + 运行前端同步**：`evals/cases/*`（10 题，按 `pass^k` 口径）+ `scripts/run_evals.py` / `scripts/verify_evals.py`；用例路径改**相对仓库根**（跨机可移植）；`web/` 运行前端同步为 **11 个预设动作**（新增「运行 git / 运行项目验收 / 跑题集」）。

### 验证
- `python -m pytest -q`：**195 passed**；`codeagent.py status --json`：36 原子 / 145 能力 / `degraded=[]` / `conflicts=[]`。
- 题集：`python scripts/run_evals.py` → **10/10 通过（pass_k 1.00）**；运行前端（8080）真浏览器实测 11 个预设动作全通（`运行 git` 返回真实分支/改动、`运行项目验收` 返回真实快照、`跑题集` 10/10），零 JS 报错。

## [0.4.1] - 2026-09-08

### 修复与 CI

- **atomicity 豁免独立原子**：`atomicity.registry` 现认识 `standalone` 原子（独立运行、不进 fusion registry 的原子，如 code-runloop），不再将其误报为「磁盘已有但未注册」，全量 pytest 由 179/1 → **180 全绿**。`code-runloop/manifest.json` 标记 `standalone:true`。
- **补 GitHub CI**：新增 `.github/workflows/ci.yml`（ubuntu + `pytest tests/`），main 推送自动跑全量单测，验证纯标准库零依赖。

## [0.4.0] - 2026-09-08

### 一体化完整版（带前端）开源

本版本 = **code-agent-lab 带前端完整版**并入开源仓库 codeagent-minimal，替代旧「纯内核 29 原子 + 闭源编排」形态，`git clone` 即用。

- **完整原子集**：统一运行时注册 **34 原子 / 能力全绿**（`codeagent.py status --json`：`degraded=[]`、`conflicts=[]`）。在纯内核基础上补齐 `code-implement`（代码实现/写码）、`browser-smoke`（浏览器冒烟）、`doc-freshness`（文档新鲜度审计）、`deadcode`（死代码检测）、`method-impact`（方法级影响分析）等原子。
- **Lab 编排 + 浏览器前端开源**：`lab/`（原子库可视化编排系统，含 `lab/frontend`）与 `web/`（最小运行前端 + REST）全部随仓库分发，纯标准库 `http.server` 起服务：
  - `python lab/lab_app.py --port 8087` → http://127.0.0.1:8087（编排画布 / 代码浏览 / 黑箱调试 / 报告 / 对话）
  - `python web/server.py --port 8080` → http://127.0.0.1:8080（状态 / review / test / guard / chain / evolve + REST）
- **写码引擎 LLM 通道纯标准库化**：`code_agent_engine` 的 LLM 调用由 `requests` 改写为原生 `urllib`（`_http_post_json`，无代理直连、JSON 解析等价），彻底移除最后一个第三方 import。
- **代码自包含化**：去除对个人环境/外部目录的硬编码依赖（obsidian_ops / env 门控、内部盘符路径 env 化等），运行时指向自身仓库目录，克隆即用。
- **零依赖零三方**：全部纯 Python 标准库，无需 `pip install` / venv；默认数据不出厂，仅绑定本机。

> 历史追溯：0.2.x–0.3.4 为**纯内核原子化迭代**，完整历史见 git。
> 0.3.4 = 33 原子纯内核版（新增 browser-smoke）；0.3.0 = 原子化重构（16 原子 + 统一运行时/入口 + 组装链）；0.2.x = 原子化重构 P0（atomic_base + agent_loader + registry）；0.1.x = 版本降维与复用优先·极简落地；旧版 v2.x 历史不在此记录，见 git。
