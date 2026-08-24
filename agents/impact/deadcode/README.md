# deadcode（死代码检测）

**域**：`impact` · **提供**：`deadcode.scan` / `deadcode.stats` · **版本**：0.1.0 · **开源**：是

## 定位

检测代码库里**不可达的死符号**（借鉴 code-graph-rag「死代码 = 入口点反向可达」）。
从程序入口/测试符号/框架装饰器/`__main__` 出发正向 BFS，未到达的已定义符号即死代码候选。
契合工厂本地代码工具的多年脚本清理诉求。

## 能力

| 能力 | 入参 | 返回 |
|---|---|---|
| `deadcode.scan` | `path`(目录) `threshold` | `total/live/dead_count/ratio` `roots` `dead`(列表) `dead_by_file` |
| `deadcode.stats` | `path` | `total/live/dead_count/ratio/roots`(概况) |

## 实现

复用根模块 `deadcode.py`（入口启发式：`test_*` / `main/run/start` / 框架装饰器 / `__main__` 守卫），
基于 `method_impact.py` 的方法级图做正向可达。纯 stdlib，核心零改动。

## 自测

```bash
python agents/impact/deadcode/main.py <目录>
python -m pytest tests/test_optimization_p0_atoms.py -k deadcode
```
