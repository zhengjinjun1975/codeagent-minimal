#!/usr/bin/env python3
"""langchain_integration.py — 把 codeagent 原子作为 LangChain Tool 接入。

依赖（可选，缺失自动提示）：`pip install langchain-core`

三种接入形态：
  A) @tool 装饰：把 codeagent_toolkit 的统一函数变成 LangChain Tool
  B) StructuredTool：带 JSON Schema 入参声明的工具
  C) bind_tools 给 Agent（OpenAI 风格 tool-calling）示意

统一信封：所有工具返回 `{ok, data}` 的 JSON 字符串，下游可 json.loads 解析。
数据不出厂：默认 local_only=True，不注入任何云端密钥。

运行自测：
    python examples/langchain_integration.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))       # 让 codeagent-minimal 顶层可 import
sys.path.insert(0, HERE)

try:
    from langchain_core.tools import tool, StructuredTool
except ImportError:
    print("缺少 langchain-core：请先 `pip install langchain-core`")
    sys.exit(2)

from codeagent_toolkit import run_capability


# ── A) @tool 装饰：code-review 原子 → LangChain Tool ─────────────
@tool
def code_review(path: str) -> str:
    """审查一个 Python 文件的语义/安全/架构，返回 {ok,data} JSON（含 0-100 分）。"""
    return run_capability("codereview.review", path=path, mode="code")


@tool
def dep_scan(target: str) -> str:
    """对目录/文件做依赖漏洞 SCA + 污点扫描（数据不出厂），返回 {ok,data} JSON。"""
    return run_capability("depscan.scan", target=target)


# ── B) StructuredTool：声明入参 Schema 的更严格工具 ───────────────
impact_tool = StructuredTool.from_function(
    func=lambda path: run_capability("impact.analyze", path=path),
    name="impact_analyze",
    description="对一个 Python 模块做依赖图影响分析，返回 {ok,data} JSON。",
    args_schema=None,  # 可用 pydantic model 声明；此处演示无 schema 用法
)


# ── C) bind_tools 给 Agent（OpenAI 风格 tool-calling）示意 ────────
def build_tools():
    """返回全部 CodeAgent 原子工具清单，可直接 bind 到 LangChain/OpenAI Agent。"""
    return [code_review, dep_scan, impact_tool]


# ── 自测 ────────────────────────────────────────────────────────
def _main() -> int:
    tools = build_tools()
    print("== LangChain 工具清单 ==")
    for t in tools:
        print(f"  - {t.name}: {t.description.splitlines()[0]}")

    print("\n== 直接调用 code_review 工具 ==")
    r = code_review.invoke({"path": "sample_target.py"})
    try:
        obj = json.loads(r)
        print("  ok =", obj.get("ok"), "| keys =", list(obj.get("data", {}).keys()))
    except Exception:
        print("  (raw)", r[:200])

    print("\n== 直接调用 impact_tool ==")
    r2 = impact_tool.invoke({"path": "sample_target.py"})
    try:
        obj2 = json.loads(r2)
        print("  ok =", obj2.get("ok"), "| keys =", list(obj2.get("data", {}).keys()))
    except Exception:
        print("  (raw)", r2[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
