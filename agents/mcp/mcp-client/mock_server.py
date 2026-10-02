#!/usr/bin/env python3
"""mcp-client 的本地 stdio mock MCP server（真实 JSON-RPC 2.0 over stdio）。

用途：mcp-client 原子连本地 MCP server 的**真实数据验证**——它实现 MCP 协议的
initialize / notifications/initialized / tools/list / tools/call 子集，
暴露 echo / upper / add / now 四个工具。零第三方依赖，纯标准库。

运行方式：`python mock_server.py`（由 mcp-client 原子作为子进程 spawn，命令列表参数执行）。
"""
import json
import sys
import datetime

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {"name": "echo", "description": "回显文本", "inputSchema": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "upper", "description": "转大写", "inputSchema": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "add", "description": "两数相加", "inputSchema": {
        "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"]}},
    {"name": "now", "description": "当前时间", "inputSchema": {"type": "object"}},
]


def _handle(method, params):
    """分发 MCP 方法。返回 (result_dict) 或抛异常(→ error)。"""
    if method == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                "serverInfo": {"name": "codeagent-mock-mcp", "version": "0.1.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        if name == "echo":
            return {"content": [{"type": "text", "text": args.get("text", "")}]}
        if name == "upper":
            return {"content": [{"type": "text", "text": str(args.get("text", "")).upper()}]}
        if name == "add":
            return {"content": [{"type": "text",
                                 "text": str(float(args.get("a", 0)) + float(args.get("b", 0)))}]}
        if name == "now":
            return {"content": [{"type": "text", "text": datetime.datetime.now().isoformat()}]}
        raise ValueError(f"未知工具: {name}")
    raise ValueError(f"未知方法: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        if method == "notifications/initialized":
            continue
        resp = {"jsonrpc": "2.0", "id": msg.get("id")}
        try:
            resp["result"] = _handle(method, msg.get("params", {}))
        except Exception as e:
            resp["error"] = {"code": -32603, "message": str(e)}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
