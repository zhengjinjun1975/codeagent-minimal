# CodeReview Minimal — 专业化代码审查 + 测试 harness

> 给需要审代码但不写代码的人用的**专业化代码审查 + 测试工具**。纯标准库零依赖，静态审查（语法/BUG/安全/架构/复用）+ 测试 harness（冒烟/单元/边界/变异/稳定性），0-100 分。

[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

## 纯标准库 · 零依赖

**核心卖点：`Python` 标准库零第三方依赖**（`ast`/`re`/`json`/`subprocess`/`importlib`）。开箱即用，不装任何包。可选：环境里有 `pytest` 时单元测试自动优先用它，没有则标准库兜底。

## 代码方法论：复用优先 · 极简落地（Reuse-First Minimalism）

审查器内置这套方法论，判断代码是否**能复用却重写、该极简却过度抽象**。完整文档：`E:/knowledge-base/obsidian-vault/knowledge/code/patterns/reuse-first-minimalism.md`

```
┌─ 找的阶梯（Reuse）─ 优先 ─┐
│  ① 本地 Obsidian 代码原子库（极简原子） │
│  ② GitHub 远端开源代码（--reuse-atoms）│
│  ③ 大模型兜底（--llm）                  │
└────────────────────────────┘
          ↓ 未命中
┌─ 写的阶梯（极简）─ 原极简原则 ─┐
│  标准库 → 已装依赖 → 一行 → 最少代码 │
└────────────────────────────┘
```

- **`--reuse-atoms`**：审查时先检索本地 Obsidian 代码原子库（智能制造/地球物理/本体/检索/深度学习 5 领域），未命中自动降级检索 GitHub 开源代码，给"复用建议"。**全程静默不报错**（无 Obsidian/断网/限流都自动跳过）。
- **`_static_check_reuse` 审查维度**：静态检查"冗余抽象 / 转发函数 / 重复字符串 / 仅 __init__ 的类"，提醒该复用/该极简。

## 双模块

### 模块 A：专业化静态审查（0-100 分）

| 维度 | 检测 |
|------|------|
| **语法** | `ast.parse` 通过性 |
| **软件 BUG** | 裸 except、可变默认参数、`== None`（应 is None）、可能未定义名 |
| **安全 BUG** | 命令注入（os.system/shell=True）、eval/exec、硬编码密钥、反序列化 |
| **架构稳健** | 圈复杂度、函数过长、文件过大、import 依赖过多 |
| **import/命名** | 未使用 import、命名规范 |

### 模块 B：测试 harness（审→测→修→回归闭环）

| 能力 | 检测 |
|------|------|
| **冒烟** | 能否 import + 跑起来 |
| **覆盖率** | 顶层函数被调用比例（估算） |
| **单元测试** | 自动发现 test_*.py，pytest 优先 / 标准库兜底 |
| **边界测试** | 对函数喂 None/空/极值/错类型，找未处理边界异常 |
| **变异测试** | 故意改一处代码（限次），看现有测试能否捕获（测测试质量） |
| **稳定性** | 重复 N 次 + 超时，看崩溃/挂起 |

## 用法

```bash
# 静态审查
python review.py 你的文件.py
python review.py 你的项目目录/ --json

# 审查 + 测试 harness（闭环）
python review.py 你的文件.py --test --test-dir tests/

# 可配置审查（真实项目用）
python review.py 你的项目/ --max-complexity 30   # 放宽圈复杂度阈值(默认10)
python review.py 你的项目/ --strict-undefined    # 启用未定义名检查(默认关,启发式易误报)

# CI 门禁：平均得分低于阈值则 exit 1
python review.py 你的项目/ --threshold 70

# 可选对接专业工具（装了 bandit/ruff 才深度增强，没装自动用内置）
python review.py 你的项目/ --external
```

## 示例

```bash
$ python review.py bad_sample.py
📄 bad_sample.py   静态得分: 67/100
   问题: critical 0 / major 3 / minor 1
   🟠 [major] 命令拼接风险 → os.system 传动态字符串易注入
   🟠 [major] 不安全的 eval/exec
   🟠 [major] 硬编码密钥/密码

$ python review.py sample_target.py --test
📄 sample_target.py   静态得分: 100/100
🧪 测试 harness
   ✅ smoke: 模块导入成功
   ✅ unit: pytest 无测试用例
   ❌ boundary: 检查 5 个函数，发现 13 个边界未处理
      → parse_int 参数 s 遇 '' 抛 ValueError，考虑加校验
   ✅ stability: 无崩溃无挂起
```

## 可选增强

- **`--llm`**：设 `LLM_API_KEY` 后启用模型审查（补过度工程/可证伪预测）
- **`--external`**：环境装了 **bandit**（深度安全）或 **ruff/pyflakes**（深度静态）时自动对接，没装则用内置（纯标准库核心不变）

核心静态分析始终零依赖；LLM 与外部工具都是可选增强。

## 诚实边界

- 静态分析是**启发式**，不是形式化验证——可能误报/漏报，结论需人工复核
- 边界/变异测试是**探测**，不是穷举——找到的边界是真实风险，未找到不代表没有
- 目标是"给审代码的人一个专业起点"，不是替代人工审查

## License

[Apache License 2.0](LICENSE)
