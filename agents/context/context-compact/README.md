# context-compact 原子

上下文压缩原子（`open_source:true`，P2，借鉴 Codex context_manager/compaction）：token 估算 + 链结果压缩（保留 summary/score/verdict 丢弃大 payload）+ max_tokens 预算裁剪。防组装链上下文膨胀。纯 stdlib。

## 能力

| 能力 | 说明 |
|---|---|
| `context.estimate` | token 估算 |
| `context.compact` | 压缩链结果（保留摘要丢弃大 payload） |
| `context.budget` | 按 max_tokens 裁剪步骤预算 |

## 入参

- `data` / `text`：待压缩/估算内容
- `steps`：链步骤序列（budget 用）
- `max_tokens`：预算上限（默认 4000）
- `keep`：保留字段列表（compact 用）
- `cap`：压缩上限（默认 200）

## 独立自测

```bash
python agents/context/context-compact/main.py --capability context.estimate --text "..."
python agents/context/context-compact/main.py --capability context.compact --data '{"summary":"x","payload":[...]}'
python agents/context/context-compact/main.py --capability context.budget --steps '[{"name":"s1"}]' --max_tokens 4000
```

## 依赖

- 无（纯 stdlib）
