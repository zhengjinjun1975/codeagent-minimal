# llm-router 原子

模型路由原子（`open_source:true`）。复用 `config/model_config.json`（改配置即切模型，零改代码）。

## 能力

| 能力 | 说明 |
|---|---|
| `llm.generate` | 云端 GLM 生成（openai 兼容）。`local_only=True` 时禁止，数据不出厂 |
| `llm.review` | 本地 ornith 审查（ollama），天然本地，数据不出厂 |

## 敏感数据红线（local-only）

- `local_only=True` → `generate` 立即返回 `{ok:false, degraded:true}`，**不发任何网络请求**。
- 涉及甲方私有代码/经验/任务状态时，透传该开关，禁止数据出网。

## 配置来源

按顺序查找 `model_config.json`：仓库 `config/` → 原子本地 `config/` → `E:/code_agent/config/` → 内置默认。key 走 env（`ZHIPU_API_KEY`）。

## 独立自测

```bash
python agents/llm/llm-router/main.py --capability generate --local-only   # 验证数据不出厂门
python agents/llm/llm-router/main.py --capability review                   # 本地 ornith
```
