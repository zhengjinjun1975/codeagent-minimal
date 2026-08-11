# Changelog

所有显著变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [0.1.0] - 2026-08-11

### 变更（版本降维 + 方法论融合）

- **版本降维**：v2.3 → **v0.1.0**，从 0.1 重新开始（诚实定位：融入复用优先·极简落地方法论后，作为新起点的首个版本）
- **方法论融合**：内置「复用优先·极简落地（Reuse-First Minimalism）」——能复用就不写，必须写就极简
- **新增 `_static_check_reuse` 审查维度**：静态检查冗余抽象 / 转发函数 / 重复字符串 / 仅 `__init__` 的类，提醒该复用/该极简
- **新增 `--reuse-atoms` 应用接口**：审查时检索本地 Obsidian 代码原子库（5 领域）→ 未命中自动降级 GitHub 远端开源代码搜索 → 给复用建议；全程静默不报错（无 Obsidian/断网/限流自动跳过）

### 方法论：复用优先 · 极简落地

```
找的阶梯（Reuse）：① 本地 Obsidian 代码原子 → ② GitHub 远端开源 → ③ 大模型兜底
写的阶梯（极简）：标准库 → 已装依赖 → 一行 → 最少代码
```

完整方法论文档：`E:/knowledge-base/obsidian-vault/knowledge/code/patterns/reuse-first-minimalism.md`

## 旧版（v2.x，已降维，历史不在此记录）

> 降维前为 v2.3（专业化代码审查 + 测试 harness，含 --external 可选对接 bandit/ruff、网络安全隐患扫描、CI 门禁等）。历史版本见 git。
