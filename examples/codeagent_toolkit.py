#!/usr/bin/env python3
"""codeagent_toolkit.py — 框架无关的 CodeAgent 对接工具集（零第三方依赖）。

把 codeagent 的「统一入口 / 统一运行时」包装成可在任何框架(LangChain/CrewAI/
AutoGen/OpenAI Agents SDK/Claude Code)中复用的工具函数。三种接入方式：

  1) run_capability()    —— 进程内直接调用统一运行时（AgentRuntime 能力路由）
  2) cli_tool()          —— 通过 codeagent 命令行子进程调用（统一入口 codeagent 命令）
  3) http_tool()         —— 通过最小 web HTTP /api/run 调用（REST 集成）

所有函数统一返回「稳定可序列化」的结果字符串（工具下游好解析），并强制
数据不出厂（local_only=True 默认）。

用法示例（LangChain）:
    from examples.codeagent_toolkit import run_capability
    from langchain_core.tools import tool
    @tool
    def code_review(path: str) -> str:
        return run_capability("codereview.review", path=path)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # codeagent-minimal 项目根
sys.path.insert(0, ROOT)


def _env() -> dict:
    """数据不出厂默认：仅继承本机环境，不注入任何云端凭据。"""
    env = dict(os.environ)
    # 显式封锁可能携带云端密钥的变量（默认本地原子/数据不出厂）
    env.pop("OPENAI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ZHIPUAI_API_KEY", None)
    return env


def run_capability(capability: str, **kwargs) -> str:
    """方式①进程内：统一运行时能力路由。返回 JSON 字符串(含 ok/data 信封)。"""
    from agent_runtime import AgentRuntime
    rt = AgentRuntime(local_only=True)
    res = rt.run_capability(capability, **kwargs)
    return json.dumps(res, ensure_ascii=False, default=str)


def cli_tool(capability: str, *args: str, timeout: int = 180) -> str:
    """方式②子进程：调用 codeagent 统一命令。capability→codeagent 子命令。"""
    cmd = [sys.executable, os.path.join(ROOT, "codeagent.py"), capability, *args, "--json"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace",
                          env=_env())
    out = proc.stdout.strip()
    if not out:
        return json.dumps({"ok": False, "error": proc.stderr[-500:],
                           "code": proc.returncode}, ensure_ascii=False)
    try:
        return out
    except Exception:
        return json.dumps({"ok": False, "raw": out}, ensure_ascii=False)


def http_tool(host: str = "127.0.0.1", port: int = 8080,
              cmd: str = "review", payload: dict | None = None,
              token: str = "", timeout: int = 180) -> str:
    """方式③REST：调用最小 web 的 /api/run 端点。返回 JSON 字符串。"""
    import json as _json
    body = _json.dumps({"cmd": cmd, "payload": payload or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/api/run", data=body,
        headers={"Content-Type": "application/json",
                 "X-Token": token} if token else {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return _json.dumps({"ok": False, "error": e.reason, "code": e.code},
                           ensure_ascii=False)


def atomic_tool(capability: str, name: str, description: str):
    """便捷工厂：返回一个统一的、可被任意框架 `@tool` 装饰的普通函数。

    返回函数签名: fn(**kwargs) -> str（统一 JSON 信封字符串，数据不出厂）。
    """
    def _fn(**kwargs) -> str:
        return run_capability(capability, **kwargs)
    _fn.__name__ = name
    _fn.__doc__ = description
    return _fn


if __name__ == "__main__":
    # 自测：三种方式各跑一个原子
    print("== ① 进程内 run_capability: codereview.review ==")
    print(run_capability("codereview.review", path="sample_target.py")[:220])
    print("\n== ② 命令行 cli_tool: review ==")
    print(cli_tool("review", "sample_target.py")[:220])
