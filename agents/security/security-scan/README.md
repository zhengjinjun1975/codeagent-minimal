# security-scan 原子

安全扫描原子（`open_source:true`）：10 安全维度（注入/认证/授权/反序列化/文件/SSRF/加密/配置/业务/供应链）+ 危险函数库 + secret 检测 + 误报治理。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `security.scan` | 全维度安全扫描 |
| `security.secret` | 密钥/敏感信息检测 |
| `security.govern` | 误报治理（验证后消除误报） |
| `security.dim` | 单维度扫描（如注入） |
| `security.project` | 工程级安全基线 |

## 入参

- `path` / `code`：目标代码
- `dimension`：单维度扫描维度（如 `注入`）

## 独立自测

```bash
python agents/security/security-scan/main.py <path> --capability security.scan
python agents/security/security-scan/main.py <path> --capability security.secret
python agents/security/security-scan/main.py <path> --capability security.dim --dimension 注入
python agents/security/security-scan/main.py <path> --capability security.govern
```

## 依赖

- `security_scan.py`（仓库内）
- 无第三方依赖
