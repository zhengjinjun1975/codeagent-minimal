# code-memory 原子（open_source:true）

**记忆双通道**：主写**可选外部共享记忆库**（经 env `OPTMEM_MEMO_DIR` 配置，指向任意
`memo` / `memo_search` 命令行实现；默认未配置即关闭），本地 `experience/*.json`
保留为降级后备与私标副本。

## 能力
- `memory.save` — 经验沉淀：**主写共享记忆库**（`memo note`，条目带 `[code]` 域前缀 + `[conf:X.X]` 置信度，≤280 utf-8 字节），并双写本地 `lessons.json` 作私标副本/降级后备。
- `memory.recall` — 跨会话召回：**优先从共享记忆库语义检索**（`memo_search.py` hybrid，`--domain code` 域隔离），失败降级词法 `memo recall`，共享库整体不可达时回落本地 `self_prompt`。
- `memory.sediment` — 技能沉淀：双写共享记忆库（`[code][conf:0.9]`）与本地 `skills.json`（去重）。

## 设计
- **主写共享 + 读共享召回 + 本地降级**：一边记、另一边(其他进程/主机)能语义检索到；记忆库不可达(未配置/异常/超时)静默回落本地，绝不中断业务主流程。
- **私标隔离**：`[code]` 前缀 + 召回 `--domain code`，不污染其他域。
- **零依赖**：纯 stdlib subprocess 包装 `memo` / `memo_search.py`（桥模块 `optmem_mem.py`），MEMORY_DIR 注入 env 确保写共享库而非工具默认目录。
- **可关闭**：`OPTMEM_OFF=1` 彻底关共享只走本地；路径可用 `OPTMEM_MEMO_DIR` / `OPTMEM_MEMORY_DIR` 覆盖。

> 说明：外部共享记忆工具（`memo` / `memo_search.py`）**不随本仓库分发**，未配置时本原子自动走本地记忆，功能完整、零第三方依赖、数据不出厂。
