#!/usr/bin/env python3
"""MCP 客户端原子壳（open_source:true, 新原子）。

对齐 OpenCode P0-1「MCP 客户端」：让 CodeAgent 原子能消费外部 MCP server
的本地(local/stdio)与远程(remote/http+sse)工具，把「原子注册表」升级为
「原子 + MCP 双轨插件面」，一次性接入 Cursor/OpenCode/社区 MCP server 生态。

零依赖轻量：纯标准库(json/subprocess/urllib/threading)，不复制 OpenCode 实现，只借鉴协议。
协议：MCP = JSON-RPC 2.0 over stdio(本地) 或 HTTP/SSE(远程)。仅实现代码审查需要的
最小子集(initialize / tools/list / tools/call)，失败一律 degraded 不抛。

安全红线（P0-3 反向延续）：MCP 工具默认 `local_only` 白名单——只有显式 `allow_remote=True`
且工具在 allow 白名单内才允许远程调用；敏感数据不出厂仍是默认。
"""
import json
import os
import subprocess
import sys
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent


class McpClientAgent(AtomicAgent):
    name = "mcp-client"
    version = "0.1.0"
    domain = "mcp"
    description = ("MCP 客户端原子: 连外部 MCP server(local stdio + remote http/sse), "
                   "tools/list + tools/call 接入生态工具。默认 local_only 白名单, 远程需显式 allow。")
    provides = ["mcp.list", "mcp.tools", "mcp.call", "mcp.connect"]
    depends_on = []
    inputs = ["server", "endpoint", "command", "tools", "timeout", "local_only", "allow_remote", "allow_tools"]
    outputs = ["servers", "tools", "result", "server"]

    def _register_defaults(self):
        self.register("mcp.list", self._list)
        self.register("mcp.connect", self._connect)
        self.register("mcp.tools", self._tools)
        self.register("mcp.call", self._call)

    # ── JSON-RPC 帧收发（stdio 本地 + http 远程共用）────────────
    def _send_recv_stdio(self, proc, msg: dict, timeout=30):
        """向 stdio 子进程写 JSON-RPC，读回匹配同一 id 的响应。"""
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server 关闭 stdin")
            try:
                resp = json.loads(line)
            except Exception:
                continue
            if resp.get("id") == msg.get("id"):
                return resp

    def _spawn_local(self, command, timeout):
        """spawn 本地 stdio MCP server 子进程（command 为命令列表）。"""
        if not command:
            raise ValueError("local MCP server 需 command")
        argv = command if isinstance(command, list) else [str(command)]
        # 安全：参数列表执行，shell=False，杜绝命令注入（对齐 dispatch.verify 的 P1-2）
        proc = subprocess.Popen(argv, shell=False, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
        return proc

    def _init_stdio(self, proc, timeout=30):
        self._send_recv_stdio(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2024-11-05",
                                                "capabilities": {},
                                                "clientInfo": {"name": "codeagent-mcp",
                                                               "version": "0.1.0"}}}, timeout)
        # initialized 通知
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized",
                                     "params": {}}) + "\n")
        proc.stdin.flush()
        return True

    def _http_jsonrpc(self, endpoint, method, params, timeout=30, headers=None):
        """远程 HTTP/JSON-RPC 端点。仅 OpenCode MCP 的简化：HTTP POST / JSON body。"""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                     headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ── 能力实现 ─────────────────────────────────────────
    def _list(self, servers=None):
        """列出已配置/已注册的 MCP server（从环境/本地配置文件读取候选）。"""
        cfg = self._load_config()
        return {"servers": cfg, "count": len(cfg)}

    def _load_config(self):
        """server 配置：优先 env `CODEAGENT_MCP_SERVERS`(JSON)，其次仓库内 mcp_config.json。"""
        env = os.environ.get("CODEAGENT_MCP_SERVERS", "")
        if env:
            try:
                cfg = json.loads(env)
                return cfg if isinstance(cfg, list) else [cfg]
            except Exception:
                pass
        for p in [os.path.join(REPO_ROOT, "config", "mcp_config.json"),
                  os.path.join(HERE, "config", "mcp_config.json")]:
            if os.path.isfile(p):
                try:
                    cfg = json.load(open(p, encoding="utf-8"))
                    return cfg if isinstance(cfg, list) else [cfg.get("servers", [])]
                except Exception:
                    continue
        # 内置演示 server（stdio mock，用于自测/真实连接验证）
        mock = os.path.join(HERE, "mock_server.py")
        return [{"name": "demo", "type": "local", "command": [sys.executable, mock],
                 "description": "本地 stdio MCP mock server（tools/list + tools/call）"}]

    def _connect(self, server=None, endpoint=None, command=None, timeout=30,
                 local_only=True, allow_remote=False, allow_tools=None, **_):
        """连接 MCP server，握手 initialize，返回 server 元信息 + 可用工具清单。
        local_only=True 时仅允许 local(stdio)；远程(endpoint) 需 local_only=False + allow_remote=True。"""
        allow_tools = allow_tools or []
        if server:
            cfg = next((s for s in self._load_config() if s.get("name") == server), None)
            if not cfg:
                return self._envelope(False, degraded=True, error=f"未知 MCP server: {server}")
            endpoint = cfg.get("endpoint")
            command = cfg.get("command")
        # 安全门：远程必须显式放行
        if endpoint and (local_only or not allow_remote):
            return self._envelope(
                False, degraded=True,
                error=f"远程 MCP '{endpoint}' 被 local_only/未 allow_remote 封锁(数据不出厂)")
        try:
            if command:
                proc = self._spawn_local(command, timeout)
                try:
                    self._init_stdio(proc, timeout)
                except Exception as e:
                    proc.kill()
                    raise
                tools = self._tools_from_proc(proc, timeout)
                return self._envelope(True, data={"server": server or "local",
                                                  "transport": "stdio", "tools": tools,
                                                  "proc": proc})
            elif endpoint:
                tools = self._http_jsonrpc(endpoint, "tools/list", {}, timeout)
                return self._envelope(True, data={"server": server or endpoint,
                                                  "transport": "http", "tools": tools,
                                                  "tools_count": len(tools.get("result", {}).get("tools", []))})
            return self._envelope(False, degraded=True, error="需 command(local) 或 endpoint(remote)")
        except Exception as e:
            return self._envelope(False, degraded=True,
                                  error=f"MCP 连接失败: {type(e).__name__}: {e}")

    def _tools_from_proc(self, proc, timeout):
        resp = self._send_recv_stdio(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                            "params": {}}, timeout)
        return resp.get("result", {}).get("tools", [])

    def _tools(self, server="demo", endpoint=None, command=None, timeout=30,
               local_only=True, allow_remote=False, allow_tools=None, **_):
        """列出 MCP server 可用工具（tools/list）。"""
        r = self._connect(server=server, endpoint=endpoint, command=command, timeout=timeout,
                          local_only=local_only, allow_remote=allow_remote, allow_tools=allow_tools)
        if not r["ok"]:
            return r
        d = r["data"]
        # 关闭已连接的 stdio 子进程，避免泄漏（连接为一次性快照）
        proc = d.pop("proc", None)
        if proc:
            try:
                proc.stdin.close()
                proc.kill()
            except Exception:
                pass
        tools = d.get("tools", [])
        return self._envelope(True, data={"server": d.get("server"), "transport": d.get("transport"),
                                          "tools": tools, "count": len(tools)})

    def _call(self, tool, arguments=None, server="demo", endpoint=None, command=None,
              timeout=30, local_only=True, allow_remote=False, allow_tools=None, **_):
        """调用 MCP 工具（tools/call）。默认 local_only + allow_tools 白名单双保险。"""
        allow_tools = allow_tools or []
        # 白名单：若配置了 allow_tools，则仅白名单内工具可调（细粒度权限 P1-2 延续）
        if allow_tools and tool not in allow_tools:
            return self._envelope(False, degraded=True,
                                  error=f"工具 '{tool}' 不在 allow_tools 白名单: {allow_tools}")
        if endpoint and (local_only or not allow_remote):
            return self._envelope(False, degraded=True,
                                  error=f"远程 MCP 工具 '{tool}' 被 local_only/未 allow_remote 封锁")
        cfg = next((s for s in self._load_config() if s.get("name") == server), {})
        command = command or cfg.get("command")
        endpoint = endpoint or cfg.get("endpoint")
        try:
            if command:
                proc = self._spawn_local(command, timeout)
                try:
                    self._init_stdio(proc, timeout)
                    resp = self._send_recv_stdio(proc, {"jsonrpc": "2.0", "id": 3,
                                                        "method": "tools/call",
                                                        "params": {"name": tool,
                                                                   "arguments": arguments or {}}},
                                                 timeout)
                finally:
                    try:
                        proc.stdin.close()
                        proc.kill()
                    except Exception:
                        pass
                result = resp.get("result", {})
                content = result.get("content", [])
                return self._envelope(True, data={"tool": tool, "result": result,
                                                  "content": content,
                                                  "text": _extract_text(content)})
            elif endpoint:
                resp = self._http_jsonrpc(endpoint, "tools/call",
                                          {"name": tool, "arguments": arguments or {}}, timeout)
                result = resp.get("result", {})
                return self._envelope(True, data={"tool": tool, "result": result,
                                                  "text": _extract_text(result.get("content", []))})
            return self._envelope(False, degraded=True, error="需 command(local) 或 endpoint(remote)")
        except Exception as e:
            return self._envelope(False, degraded=True,
                                  error=f"MCP 工具调用失败: {type(e).__name__}: {e}")


def _extract_text(content):
    """从 MCP 工具返回 content 提取纯文本（content 为 [{type:'text',text:...}]）。"""
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


agent = McpClientAgent()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="mcp-client 原子自测入口")
    ap.add_argument("--capability", default="mcp.list",
                    choices=["mcp.list", "mcp.tools", "mcp.call"])
    ap.add_argument("--tool", default="echo")
    ap.add_argument("--arg", default="hello-mcp")
    ap.add_argument("--local-only", action="store_true", help="强制 local_only")
    args = ap.parse_args()
    agent.load()
    print("══ mcp-client 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    if args.capability == "mcp.list":
        r = agent.run(_capability="mcp.list")
    elif args.capability == "mcp.tools":
        r = agent.run(_capability="mcp.tools", local_only=args.local_only)
    else:
        r = agent.run(_capability="mcp.call", tool=args.tool,
                      arguments={"text": args.arg}, local_only=args.local_only)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"] and not r.get("degraded"):
        sys.exit(1)
