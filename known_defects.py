#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""known_defects.py — 缺陷根因库（独立固化文件，P2-8 固化）。

把原先内嵌在 review.py 的已知坑模式，沉淀为**可读 / 可扩展 / 可审查加载**的
独立根因库文件。每次代码审查命中这些模式时，给出"已知坑"提示（根因沉淀，
防重复踩坑）。

每条含：
  - id          稳定唯一标识（可用于去重/引用）
  - pattern     触发正则（re 编译，兼容 .match）
  - title       人类可读标题
  - tier        P0(critical)/P1(major)/P2(minor) 严重级
  - suggestion  修复建议
  - root_cause  根因（为何会出这个坑，审查时随发现输出）

新增缺陷条目只需往 KNOWN_DEFECTS 追加 {id, pattern, title, tier, suggestion, root_cause}，
无需改 review.py 代码 —— 根因库与审查逻辑解耦。

用法：
    import known_defects as kd
    issues = kd.match_defects(source, line_index)     # 全库匹配
    kd.defects_by_id("bool-field-json")               # 按 id 查
"""

import re

# ═══════════════ 缺陷根因库：已知坑模式库 ═══════════════
# 布尔字段 / 中文 tokenize / 引号转义 / 时区 / 浮点 / 静默吞异常 / 全局可变状态
KNOWN_DEFECTS = [
    {"id": "bool-field-json", "pattern": r"\b(bool|boolean|is_[a-z_]+|has_[a-z_]+|flag|enabled|active)\b\s*=\s*[\"'](?:true|false|1|0)[\"']",
     "title": "布尔字段用字符串/数字表示", "tier": "P1",
     "suggestion": "布尔字段用 JSON 布尔 true/false 而非字符串/0/1，否则 'false' 在 JS 中为真值",
     "root_cause": "类型系统不一致：字符串'false'在多数语言是 truthy"},
    {"id": "cn-tokenize", "pattern": r"\.split\([\"']{1}[^\"']{1,6}[\"']{1}\)|split\(\)",
     "title": "中文文本按单字符/空格切分（tokenize 失真）", "tier": "P1",
     "suggestion": "中文分词用 jieba/字符bigram，勿用 str.split() 按空格切（中文无空格分隔）",
     "root_cause": "英文空格分词假设对中文失效"},
    {"id": "quote-escape", "pattern": r"[\"']\\\"[\"']|\\\['\"]",
     "title": "引号转义混淆（可能是 SQL/HTML/JS 注入点）", "tier": "P1",
     "suggestion": "检查引号转义是否双重转义；用参数化/转义库而非手工 \\\"",
     "root_cause": "多层字符串嵌套时引号转义易错"},
    {"id": "datetime-naive", "pattern": r"datetime\.now\(\)|datetime\.utcnow\(\)",
     "title": "用本地/UTC 朴素时间（非 timezone-aware）", "tier": "P2",
     "suggestion": "用 datetime.now(timezone.utc) 带时区，避免夏令时/时区偏移 bug",
     "root_cause": "naive datetime 无时区信息"},
    {"id": "float-eq", "pattern": r"==\s*0?\.?[0-9]+(?:\.\d+)?",
     "title": "浮点数直接 == 比较（精度误差）", "tier": "P2",
     "suggestion": "用 abs(a-b) < eps 或 round 比较",
     "root_cause": "浮点二进制表示不精确"},
    {"id": "silent-except", "pattern": r"except\s*:\s*pass|except\s+[A-Za-z]+\s*:\s*pass",
     "title": "静默吞异常（except: pass）", "tier": "P0",
     "suggestion": "至少记录日志，或指定异常类型处理",
     "root_cause": "异常被吞导致静默失败（能通但不生效）"},
    {"id": "global-mutation", "pattern": r"^\s*global\s+",
     "title": "global 可变状态（跨调用污染/并发竞态）", "tier": "P1",
     "suggestion": "用参数传递或类实例状态替代 global",
     "root_cause": "全局可变状态难追踪"},
    {"id": "reasoning-model-token-exhaust", "pattern": r"deepseek-reasoner",
     "title": "推理模型 max_tokens 被 reasoning 吃光 → content 空", "tier": "P1",
     "suggestion": "deepseek-reasoner 对实质任务推理链发散，reasoning_content 随 max_tokens 线性增长，content 永远空(finish_reason=length)。修复：请求体加 thinking:{\"type\":\"disabled\"} 关闭推理链，或改用非推理模型 deepseek-chat，或把 max_tokens 显著加大（bump 治标不治本，reasoning 会跟着涨）",
     "root_cause": "带 CoT 的推理模型对复杂代码/生成任务推理发散，reasoning 吃满 max_tokens，content 被吃光返回空"},
]

_SEV = {"P0": "critical", "P1": "major", "P2": "minor"}


def defects_by_id(defect_id: str):
    """按 id 查询单条缺陷定义。不存在返回 None。"""
    return next((d for d in KNOWN_DEFECTS if d["id"] == defect_id), None)


def match_defects(content: str, line_index=None):
    """按已知坑模式库匹配源码，命中即提示（根因沉淀防重复踩坑）。

    返回与 review.py 旧版一致的 issue 结构：[{severity, title, line, suggestion,
    root_cause, defect_id, semantic}]。
    """
    issues = []
    line_index = line_index or content.split("\n")
    for kd in KNOWN_DEFECTS:
        try:
            pat = re.compile(kd["pattern"])
        except Exception:
            continue
        m = pat.search(content)
        if not m:
            continue
        line = 0
        for ln, text in enumerate(line_index, 1):
            if m.start() < len(text):
                line = ln
                break
        issues.append({"severity": _SEV.get(kd["tier"], "minor"),
                       "title": f"[已知坑·{kd['id']}] {kd['title']}",
                       "line": line,
                       "suggestion": kd["suggestion"],
                       "root_cause": kd["root_cause"],
                       "defect_id": kd["id"],
                       "semantic": True})
    return issues


# 兼容旧内嵌引用：review.py 直接 import 本模块的 KNOWN_DEFECTS / 匹配逻辑
if __name__ == "__main__":
    import sys
    src = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not src:
        print(f"缺陷根因库: {len(KNOWN_DEFECTS)} 条已知坑模式")
        for d in KNOWN_DEFECTS:
            print(f"  [{d['id']}] {d['tier']} {d['title']} — {d['root_cause']}")
    else:
        import json
        print(json.dumps(match_defects(src), ensure_ascii=False, indent=2))
