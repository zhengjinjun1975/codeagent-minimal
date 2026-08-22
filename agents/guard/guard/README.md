# guard 原子

护栏钩子原子（`open_source:true`，P2，借鉴 Codex guardian）：code 变更前置/后置安全审查钩子流水线，复用 security_scan 10 维度 + secret + 误报治理做门禁（pre/post/pipeline/check）。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `guard.pre` | 变更前置安全审查 |
| `guard.post` | 变更后置安全审查 |
| `guard.pipeline` | 多文件门禁流水线（取最差门禁值） |
| `guard.check` | 单路径安全扫描 + 门禁判定 |

## 入参

- `path` / `code`：目标代码
- `paths`：多路径列表（pipeline 用）
- `govern`：是否启用误报治理

## 独立自测

```bash
python agents/guard/guard/main.py --capability guard.check --path <file.py>
python agents/guard/guard/main.py --capability guard.pipeline --paths '["<file1.py>","<file2.py>"]'
```

## 依赖

- `security_scan.py`（仓库内）
- 无第三方依赖
