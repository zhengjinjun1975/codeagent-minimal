# code-fuzz 原子

属性模糊测试原子（`open_source:true`）。复用 `fuzz_engine.py`（核心零改动），纯 stdlib，子进程隔离执行，数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `fuzz.gen` | 覆盖率驱动生成用例：从函数 AST 分支/条件生成针对性输入 |
| `fuzz.run` | 属性/模糊测试单函数：随机输入子进程隔离执行，捕获未处理异常/崩溃 |
| `fuzz.property` | 不变量校验：随机输入调函数，校验每条 property 是否成立 |
| `fuzz.project` | 项目级模糊：批量函数找未处理异常 |

## 入参

- `path`：目标 Python 文件（必填）
- `funcname`：目标函数名（`fuzz.gen` 可缺省扫全部函数）
- `iterations`：模糊轮数（默认 100）
- `timeout`：单次子进程超时（秒，防死循环）
- `seed`：随机种子（确定性复现）
- `properties`：`[(描述, callable(返回值)->bool)]`，`fuzz.property` 用

## 独立自测

```bash
python agents/fuzz/code-fuzz/main.py <path> [--funcname <fn>] [--capability fuzz.gen|run|property|project]
```

## 依赖

- 无（零 LLM，纯 stdlib；隔离子进程防崩溃污染）
