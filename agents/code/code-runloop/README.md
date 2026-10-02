# code-runloop 原子（写码 · 真跑反馈闭环）open_source:true

把 code-agent 写码从"单发无真跑"升级为 **agentic run_loop**(设计 v2 §1-§10):
harness 一次 LLM generate 后用真工具执行+观测回喂; 循环到模型无 tool_call 收尾,
再用 `verify_cmd` 做 **DoD 硬校验**, 失败把真实输出当新消息回喂重来, 直到绿。

**引擎自包含**: 本体 = 纯 stdlib 五个模块(不 import code_agent_engine / 任何跨库活目录):

```
agents/code/code-runloop/
├─ main.py         AtomicAgent 壳(code.runloop) + CLI     (repo-loader 胶水)
├─ run_loop.py     harness 核心(真跑反馈主循环)            (自包含)
├─ tools.py        工具集: run/read_file/grep/edit/write/verify_cmd + 截断保尾 + FS白名单 (自包含)
├─ llm.py          唯一 LLM generate(纯 stdlib urllib, 读 config/model_config.json) (自包含)
├─ prompts.py      系统提示: 工具协议 + 写码纪律 + 分片构造法 (自包含)
├─ smoke_model.py  确定性自纠冒烟 fake(不连网, 证明真反馈非单发)
├─ manifest.json
└─ README.md
```

**能力**: `code.runloop` — 真跑反馈闭环写码。

**CLI 用法**:
```bash
# 确定性自纠冒烟(不连网, 证明真跑反馈能自纠到绿):
python agents/code/code-runloop/main.py \
  --task "实现一个 greet(name) 返回问候语" \
  --path <目标.py> --fake-model smoke --verbose

# 真调模型(读 config/model_config.json 的 generate):
python agents/code/code-runloop/main.py \
  --task "..." --path <目标.py> [--verify "python -m py_compile <目标.py>"] \
  [--max-iterations 12]
```

**核心机制(设计 §2/§3/§4)**:
- 真跑回喂: 每轮真执行 tool_call, 真实输出当 user 消息回喂(不解析, 让模型读)。
- 完成判据: 无 tool_call 收尾 → verify_cmd DoD 硬校验; 绿→完成; 失败回喂重来。
- FinishReasonLength → prefill 续写提示(防大块截断)。
- edit: search-replace 字面量唯一匹配, 内存 dry-run 才落盘; 失败精确回显重发失败片。
- 工具输出行数截断保尾 + 字节帽, 防 context 爆; run 超时哨兵 124。
- 分片纪律写进 system prompt(骨架整写 + 多次小 edit 追加大块, 单片 <~150 行)。

**数据流**: LLM/伪模型 → 真工具执行(本地磁盘/shell) → 观测回喂 → DoD。仅本地落文件。
