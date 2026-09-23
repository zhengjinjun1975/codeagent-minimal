#!/usr/bin/env python3
"""web_api_client.py — 通过完整编排前端（Lab）的 REST 接口调用 codeagent。

先启动 Lab（它是唯一前端，同时就是 REST 服务端）：
    python lab/lab_app.py --port 8087           # 绑定 127.0.0.1，数据不出厂
    （可选加 --token <T>，则需在请求头带 X-Token）

本客户端用纯标准库 urllib 实现，零第三方依赖。也演示如何把任意传统框架
（LangChain/OpenAI Agents SDK 等）的工具函数接到本 REST 端点。

REST 端点一览（lab/lab_app.py）：
    GET  /api/health        探活
    GET  /api/atoms         原子清单
    POST /api/action        单步动作 {cmd, path?}
    POST /api/pipeline/run  跑一条编排图 {name, graph}

跑通验证（需先启动 Lab）：
    python examples/web_api_client.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from codeagent_toolkit import http_tool  # noqa: E402


def health(host="127.0.0.1", port=8087):
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _main() -> int:
    print("== /api/health ==")
    print(json.dumps(health(), ensure_ascii=False))

    print("\n== /api/action {cmd:review} ==")
    r = http_tool(cmd="review", payload={"path": "sample_target.py"})
    try:
        obj = json.loads(r)
        res = obj.get("data", {}).get("result", {})
        print("  ok =", obj.get("ok"), "| result keys =", list(res.keys())[:8])
    except Exception:
        print("  (raw)", r[:300])

    print("\n== /api/action {cmd:status} ==")
    r2 = http_tool(cmd="status")
    try:
        obj2 = json.loads(r2)
        st = obj2.get("data", {}).get("result", {}).get("status", {})
        print("  ok =", obj2.get("ok"), "| count =", st.get("count"))
    except Exception:
        print("  (raw)", r2[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
