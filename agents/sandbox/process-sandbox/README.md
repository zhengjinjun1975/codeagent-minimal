# process-sandbox 原子

进程沙箱原子（`open_source:true`，P0，借鉴 Codex sandboxing）：安全执行不可信 PoC/命令，隔离 + 超时 + 资源限制 + 输出封顶 + 白名单 + env 净化 + 路径防护。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `sandbox.poc` | 沙箱执行不可信 PoC（隔离+超时+资源限制+输出封顶） |
| `sandbox.exec` | 安全执行命令 |
| `sandbox.validate` | 校验代码/命令是否可安全沙箱执行 |
| `sandbox.interpreter` | 可执行解释器白名单校验 |
| `sandbox.guard` | 路径穿越防护（安全解析路径） |

## 入参

- `code` / `cmd`：待执行的不可信 PoC/命令
- `timeout`：超时
- `max_output`：输出封顶字节数
- `base_dir` / `base`：路径防护基准
- `path`：路径

## 独立自测

```bash
python agents/sandbox/process-sandbox/main.py --capability sandbox.poc --code 'print("hi")'
python agents/sandbox/process-sandbox/main.py --capability sandbox.validate --cmd 'rm -rf /'
python agents/sandbox/process-sandbox/main.py --capability sandbox.guard --path <path>
```

## 依赖

- `bug_deep.py`、`pathguard.py`（仓库内）
- 无第三方依赖
