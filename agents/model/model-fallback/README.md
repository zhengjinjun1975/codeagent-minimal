# model-fallback 原子

模型降级链原子（`open_source:true`，P2，借鉴 Codex model-provider fallback）：按候选链尝试 provider，失败自动降级（cloud→local），local_only 剔除云端数据不出厂。复用 llm-router provider 注册表概念。

## 能力

| 能力 | 说明 |
|---|---|
| `model.route` | 按 purpose/preference 路由到首个可用模型 |
| `model.candidates` | 生成候选模型链（含降级顺序） |
| `model.chain` | 沿候选链尝试调用，失败自动降级 |

## 入参

- `messages`：对话消息（chain 用）
- `call_model`：单模型调用函数（chain 用）
- `purpose`：用途（generic 等）
- `preference`：倾向（local_first 等）
- `local_only`：仅本地开关（剔除云端）

## 独立自测

```bash
python agents/model/model-fallback/main.py --capability model.route --preference local_first
python agents/model/model-fallback/main.py --capability model.candidates --local_only 1
```

## 依赖

- 复用 `llm-router` provider 注册表概念（无硬依赖）
- 无第三方依赖
