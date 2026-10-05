# 任务队列（接单线）

> 往这里追加 `## Task-ID:` 块；轮询器 `scripts/intake_poll.py` 取 `Status: 待办` 的块，
> 领单后改成 `进行中 <时间>`，做完在本块下追加 `### Result`。
>
> 轮询：`python scripts/intake_poll.py [--max N] [--dry-run] [--fake-model smoke]`
> 队列路径默认 `docs/intake/tasks.md`，可用环境变量 `CODEAGENT_INTAKE_QUEUE` 覆盖。
>
> 纪律：一单一块；写清「目标文件」与「DoD 校验命令」（纯值用反引号包住）；不做 git push；做不完写 `Status: 阻塞 <原因>`。

---

## Task-ID: example-hello-20261005

From: 维护者
Status: 待办
Task: 示例单：用 code.runloop 新建 `tools/hello.py`，函数 greet(name) 返回 "hi <name>"，纯标准库

### 请你做的
1. 目标文件：`tools/hello.py`
2. DoD 校验命令：`python -m py_compile tools/hello.py`

### 回报
追加 `### Result`：选中链名 / ok 与否 / 落盘路径 / DoD 退出码。
