---
name: add-function
description: 实现加法函数 add(a,b) 的标准技能（SKILL.md 示例）
version: 1.0.0
license: Apache-2.0
compatibility:
  - claude
  - opencode
  - agents
metadata:
  domain: math
---

# 加法函数技能

## 步骤
1. 定义 `def add(a, b):` 
2. 返回 `a + b`
3. 补参数校验：非数值时抛 ValueError

## 边界
- `add(0,0)`, `add(-1,2)`, `add(1.5, 2.5)`
