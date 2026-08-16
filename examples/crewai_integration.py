#!/usr/bin/env python3
"""crewai_integration.py — 把 codeagent 原子作为 CrewAI Tool 接入。

依赖（可选，缺失自动提示）：`pip install crewai`

两种官方接入形态：
  A) `@tool` 装饰器（函数式）
  B) 继承 `BaseTool`（类式，可复用内部 `_run` 逻辑、便于单测）

统一信封：工具返回 `{ok, data}` 的 JSON 字符串。数据不出厂默认。
运行自测：
    python examples/crewai_integration.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

try:
    from crewai.tools import tool as crewai_tool, BaseTool
except ImportError:
    print("缺少 crewai：请先 `pip install crewai`")
    sys.exit(2)

from codeagent_toolkit import run_capability


# ── A) 函数式 @tool ─────────────────────────────────────────────
@crewai_tool("code_review_tool")
def code_review_tool(path: str) -> str:
    """审查一个 Python 文件的语义/安全/架构，返回 {ok,data} JSON（含 0-100 分）。"""
    return run_capability("codereview.review", path=path, mode="code")


# ── B) 类式 BaseTool（可单测、可复用内部逻辑）─────────────────────
class FuzzTool(BaseTool):
    """CrewAI 工具：对目标函数做覆盖率驱动属性模糊。"""

    name: str = "code_fuzz_tool"
    description: str = "对目标 Python 文件的指定函数做属性模糊测试，返回 {ok,data} JSON。"

    def _run(self, path: str, funcname: str = "add") -> str:
        # 复用统一原子：fuzz.run 单函数模糊
        return run_capability("fuzz.run", path=path, funcname=funcname, iterations=20)


# ── 自测 ────────────────────────────────────────────────────────
def _main() -> int:
    print("== CrewAI 工具 ==")
    print("  -", code_review_tool.name, ":", code_review_tool.description.splitlines()[0])
    print("  -", FuzzTool().name, ":", FuzzTool().description.splitlines()[0])

    print("\n== 调用 code_review_tool（函数式）==")
    r = code_review_tool.run(path="sample_target.py")
    try:
        obj = json.loads(r)
        print("  ok =", obj.get("ok"), "| keys =", list(obj.get("data", {}).keys()))
    except Exception:
        print("  (raw)", r[:200])

    print("\n== 调用 FuzzTool（BaseTool 类式）==")
    r2 = FuzzTool().run(path="sample_target.py", funcname="add")
    try:
        obj2 = json.loads(r2)
        print("  ok =", obj2.get("ok"), "| func =", obj2.get("data", {}).get("func"),
              "| runs =", obj2.get("data", {}).get("runs"))
    except Exception:
        print("  (raw)", r2[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
