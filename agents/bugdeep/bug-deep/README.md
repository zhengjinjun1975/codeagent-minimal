# bug-deep 原子

深挖 bug 原子（`open_source:true`）：威胁建模先建攻击面 + 对抗性审查 + 自动化 PoC 沙箱跑证据 + AI 规则反哺闭环（验证漏洞沉淀规则）。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `bugdeep.model` | 威胁建模：从代码识别 sink/攻击面 |
| `bugdeep.adv` | 对抗性审查：先假设误报再证伪 |
| `bugdeep.poc` | 自动生成 PoC 并在沙箱验证（取证） |
| `bugdeep.rule` | 规则沉淀：验证漏洞后写入规则库闭环 |

## 入参

- `path` / `code`：目标代码
- `sink`：PoC/rule 的危险函数名
- `title`：规则标题
- `rules_file`：规则沉淀文件（`--rules`）

## 独立自测

```bash
python agents/bugdeep/bug-deep/main.py <path.py> --capability bugdeep.model
python agents/bugdeep/bug-deep/main.py <path.py> --capability bugdeep.adv
python agents/bugdeep/bug-deep/main.py <path.py> --capability bugdeep.poc --sink eval
python agents/bugdeep/bug-deep/main.py <path.py> --capability bugdeep.rule --sink eval
```

## 依赖

- `bug_deep.py`、`pathguard.py`（仓库内）
- 无第三方依赖
