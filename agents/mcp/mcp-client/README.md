# mcp-client 原子（新原子 · OpenCode P0-1 吸收）

> 对齐 OpenCode「MCP 客户端」（local+remote 工具接入生态）。让 CodeAgent 12 原子消费外部
> MCP server 工具，把「原子注册表」升级为「原子 + MCP 双轨插件面」。

## 能力
- `mcp.list` — 列出已配置/内置 MCP server（env `CODEAGENT_MCP_SERVERS` 或仓库内 `config/mcp_config.json`）
- `mcp.connect` — 连接 + 握手 initialize，返回 server 元信息 + 工具清单
- `mcp.tools` — tools/list 列出可用工具
- `mcp.call` — tools/call 调用工具

## 传输
- **local（stdio）**：`command` 命令列表 spawn 子进程，JSON-RPC 2.0 over stdin/stdout（命令列表参数执行，shell=False，防注入）
- **remote（http）**：`endpoint` HTTP POST JSON-RPC（简化实现）

## 安全红线（数据不出厂延续）
- MCP 工具默认 `local_only=True`：远程 `endpoint` 需 `local_only=False` + `allow_remote=True` 才可连接
- 可选 `allow_tools` 白名单：仅白名单内工具可调（细粒度权限 P1-2 延续）

## 真实数据验证
```bash
# 连本地 stdio mock MCP server（真实 JSON-RPC 协议）并列出工具
python agents/mcp/mcp-client/main.py --capability mcp.tools
# 调用 echo 工具（回显"你好MCP"）
python agents/mcp/mcp-client/main.py --capability mcp.call --tool echo --arg "你好MCP"
```

## 零依赖
纯标准库 `json/subprocess/urllib/threading`，不复制 OpenCode 实现，只借鉴协议。
