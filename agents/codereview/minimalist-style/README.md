# minimalist-style 原子

极简风格审查原子（`open_source:true`，P0）：解析 AST 审查代码是否纯标准库/不过度依赖/不炫技/可独立部署。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `minimal.style` | 极简风格整体审查（纯 stdlib/不过度依赖/不炫技） |
| `minimal.deps` | 第三方依赖审查 |
| `minimal.independent` | 可独立部署性（无硬编码绝对路径/无第三方依赖） |

## 入参

- `path` / `code`：目标代码
- `strict`：严格模式开关

## 独立自测

```bash
python agents/codereview/minimalist-style/main.py --capability minimal.style --path <file.py>
python agents/codereview/minimalist-style/main.py --capability minimal.independent --path <dir>
```

## 依赖

- 无（纯 stdlib）
