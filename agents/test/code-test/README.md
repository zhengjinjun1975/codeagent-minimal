# code-test 原子

测试原子（`open_source:true`）。复用 `test_harness.py`（核心零改动），红绿回归，数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `test.gen` | 从代码(AST)生成基本 + 边界测试文件 |
| `test.run` | 复用 `test_harness.run_all`：冒烟/覆盖率/单元/边界/变异/稳定，推导红绿 |
| `test.tdd` | 红→绿→回归 反馈闭环（红时给改进建议，不改核心算法） |

## 入参

- `path`：目标 Python 文件（必填）
- `target_dir` / `dir`：测试文件所在目录（默认 `.`）
- `code`：`{文件名: 代码}`（test.gen 用）

## 独立自测

```bash
python agents/test/code-test/main.py <path.py> [--dir <dir>] [--capability test.run|test.gen|test.tdd]
```

## 依赖

- 无（复用仓库内 `test_harness.py`）
