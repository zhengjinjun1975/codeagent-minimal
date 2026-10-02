# dep-scan 原子

依赖漏洞 SCA + 污点分析原子（`open_source:true`）。复用 `dep_scan.py`（核心零改动），纯 stdlib，数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `depscan.scan` | SCA + taint 一站式扫描（`scan_all`）：SCA 漏洞数 + 污点/注入链数 + 严重汇总 |
| `depscan.sca` | 依赖漏洞 SCA（`scan_dependencies`）：对照漏洞库 + 可疑包，默认完全离线 |
| `depscan.taint` | Semgrep 级污点分析（`taint_analyze`）：source→sink 数据流 + SQL 注入模式 |
| `depscan.osv` | 显式 OSV 在线查询（需 `allow_remote=True`，数据不出厂默认关） |

## 入参

- `target`：目标文件或目录（必填）
- `osv_query`：是否启用 OSV 在线查询（默认关）
- `allow_remote`：是否允许联网（数据不出厂默认关）

## 独立自测

```bash
python agents/depscan/dep-scan/main.py <target> [--capability depscan.scan|sca|taint|osv] [--osv] [--remote]
```

## 依赖

- 无（零 LLM，纯 stdlib 静态分析；OSV 联网需显式 `--remote`）
