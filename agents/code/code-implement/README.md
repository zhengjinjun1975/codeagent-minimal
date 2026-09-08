# code-implement 原子（open_source:true）

**真写生产代码到磁盘**。本原子就是本仓库自包含写码引擎的承载，import 仓库根下
`code_agent_engine.py`(CodeAgentEngine) 的 `CodeAgent().implement()`，引擎逻辑
**唯一实现即位于本仓库**，本原子只做加壳转发。

引擎继承/包含：
- 内置写码工程纪律（YAGNI/复用/极简/不抄近路）
- 自动复用（按关键词路由到代码库并注入 prompt）
- loop 迭代优化（可选）
- 可选工作日志落库（env CODEAGENT_OBSIDIAN_VAULT 启用）
- 静态安全审查 / AST 测试生成（零模型层）

**能力**：
- `code.implement` — 真写码（task/language/output_dir/domain/loop/max_iter）
- `code.write` — 别名

**落盘**：output_dir > path 所在目录 > 仓库根 `_impl_output`。

**数据流**：LLM 生成 → 本地写盘，仅本地落文件，不额外上传。
