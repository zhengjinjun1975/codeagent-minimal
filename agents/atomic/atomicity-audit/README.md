# atomicity-audit 原子

原子化审查原子（`open_source:true`，P0，覆盖 codeagent-minimal 自身）：复用 `agent_loader` 审查 manifest（name==目录/字段/entry）/registry 一致性/能力断链。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `atomicity.manifest` | 逐个 manifest 审查 name==目录、字段完整、entry 存在 |
| `atomicity.registry` | registry vs 磁盘一致性（残留/缺失原子）比对 |
| `atomicity.breaks` | 能力依赖断链/冲突审查（全可解析则无断链） |

## 入参

- `agents_dir`：原子目录（默认仓库 `agents/`）
- `registry_path`：registry 路径（默认仓库 `registry.json`）

## 独立自测

```bash
python agents/atomic/atomicity-audit/main.py --capability atomicity.breaks
python agents/atomic/atomicity-audit/main.py --capability atomicity.manifest
python agents/atomic/atomicity-audit/main.py --capability atomicity.registry
```

## 依赖

- `agent_loader.py`、`registry.json`（仓库内）
- 无第三方依赖
