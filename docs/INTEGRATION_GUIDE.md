# CodeAgent 对接传统框架指南

> 把 CodeAgent 的 32 个原子作为 **Tool / 子代理节点 / 图编排 / REST / CLI** 集成进主流传统框架：
> **LangChain · CrewAI · AutoGen · OpenAI Agents SDK · Claude Code**。
> 所有示例参考 `examples/` 已跑通代码，并遵守两条硬边界：
>
> - **本地原子 / 数据不出厂**：默认 `local_only=True`，不注入任何云端密钥；云端 LLM / 远端 OSV 需显式开启。
> - **开源内核 / 闭源编排**：本仓库开源 32 原子 + 统一运行时/入口；重型闭源编排（assembler / orchestrator / CodeMode）位于独立工作区，不随本仓库分发、不链接、非必需。

---

## 统一接入层：`examples/codeagent_toolkit.py`

框架无关、零第三方依赖。三种接入方式，全部返回**稳定可序列化的 `{ok, data}` JSON 字符串**：

| 方式 | 函数 | 说明 |
|------|------|------|
| ① 进程内 | `run_capability(capability, **kwargs)` | 直接调 `AgentRuntime` 能力路由（性能最好） |
| ② 子进程 CLI | `cli_tool(capability, *args)` | 调 `codeagent.py <子命令>` 统一入口 |
| ③ REST | `http_tool(host, port, cmd, payload)` | 调最小 web `/api/run` 端点 |

```python
from examples.codeagent_toolkit import run_capability, cli_tool, http_tool, atomic_tool

# ① 进程内能力路由（数据不出厂）
res = run_capability("codereview.review", path="sample_target.py", mode="code")

# ② 命令行子进程
res2 = cli_tool("review", "sample_target.py")

# ③ REST（需先 python web/server.py）
res3 = http_tool(cmd="review", payload={"path": "sample_target.py"})

# 便捷工厂：返回一个可直接被任意框架 @tool 装饰的普通函数
my_tool = atomic_tool("codereview.review", name="code_review",
                      description="审查 Python 文件的语义/安全/架构，返回 {ok,data} JSON")
```

> 安全：`_env()` 显式剥离 `OPENAI_API_KEY / ANTHROPIC_API_KEY / ZHIPUAI_API_KEY`，保证原子只跑本地。

---

## 1. LangChain —— 作为 Tool

参考：`examples/langchain_integration.py`（依赖可选：`pip install langchain-core`）。

**三种形态：`@tool` 装饰 / `StructuredTool` 带 Schema / `bind_tools` 给 Agent。**

```python
from langchain_core.tools import tool, StructuredTool
from examples.codeagent_toolkit import run_capability

@tool
def code_review(path: str) -> str:
    """审查 Python 文件的语义/安全/架构，返回 {ok,data} JSON（含 0-100 分）。"""
    return run_capability("codereview.review", path=path, mode="code")

@tool
def dep_scan(target: str) -> str:
    """依赖漏洞 SCA + 污点扫描（数据不出厂），返回 {ok,data} JSON。"""
    return run_capability("depscan.scan", target=target)

impact_tool = StructuredTool.from_function(
    func=lambda path: run_capability("impact.analyze", path=path),
    name="impact_analyze",
    description="对 Python 模块做依赖图影响分析，返回 {ok,data} JSON。",
)

# 绑定到 Agent（OpenAI 风格 tool-calling）
llm_with_tools = llm.bind_tools([code_review, dep_scan, impact_tool])
```

运行自测：

```bash
python examples/langchain_integration.py
```

---

## 2. CrewAI —— 作为 Crew Tool

参考：`examples/crewai_integration.py`（依赖可选：`pip install crewai`）。

**两种官方形态：函数式 `@tool` / 类式 `BaseTool`（可单测、可复用 `_run`）。**

```python
from crewai.tools import tool as crewai_tool, BaseTool
from examples.codeagent_toolkit import run_capability

@crewai_tool("code_review_tool")
def code_review_tool(path: str) -> str:
    """审查 Python 文件的语义/安全/架构，返回 {ok,data} JSON（含 0-100 分）。"""
    return run_capability("codereview.review", path=path, mode="code")

class FuzzTool(BaseTool):
    """对目标函数做覆盖率驱动属性模糊。"""
    name: str = "code_fuzz_tool"
    description: str = "对目标 Python 文件的指定函数做属性模糊测试，返回 {ok,data} JSON。"
    def _run(self, path: str, funcname: str = "add") -> str:
        return run_capability("fuzz.run", path=path, funcname=funcname, iterations=20)

# 加入 Crew
# my_crew = Crew(agents=[...], tasks=[...], tools=[code_review_tool, FuzzTool()])
```

运行自测：

```bash
python examples/crewai_integration.py
```

---

## 3. AutoGen —— 作为可注册 Tool / 子代理节点

AutoGen 允许把任意「入参 → 返回字符串」函数注册为工具。CodeAgent 统一信封天然适配：

```python
from autogen import ConversableAgent
from examples.codeagent_toolkit import run_capability

def code_review_tool(path: str) -> str:
    return run_capability("codereview.review", path=path, mode="code")

def dep_scan_tool(target: str) -> str:
    return run_capability("depscan.scan", target=target)

assistant = ConversableAgent(
    "assistant",
    llm_config={"config_list": [...], "tools": [
        {"type": "function", "function": {"name": "code_review_tool",
                                           "description": code_review_tool.__doc__,
                                           "parameters": {"type": "object",
                                                          "properties": {"path": {"type": "string"}},
                                                          "required": ["path"]}}},
        {"type": "function", "function": {"name": "dep_scan_tool",
                                           "description": dep_scan_tool.__doc__,
                                           "parameters": {"type": "object",
                                                          "properties": {"target": {"type": "string"}},
                                                          "required": ["target"]}}},
    ]},
)
# 注册执行器：把 tool 名称映射到 run_capability
executor = ConversableAgent("executor", llm_config=False,
                            functions=[code_review_tool, dep_scan_tool])
```

> 数据不出厂：不配置远端 API Key，工具仅做本地原子调用；如需 LLM 参与编排，在独立配置中显式声明。

---

## 4. OpenAI Agents SDK —— 作为 `FunctionTool`

OpenAI Agents SDK 用 `FunctionTool` 包装任意函数：

```python
from agents import Agent, FunctionTool, Runner
from examples.codeagent_toolkit import run_capability

def _code_review(args_json: str) -> str:
    import json
    kw = json.loads(args_json)
    return run_capability("codereview.review", path=kw["path"], mode="code")

code_review_tool = FunctionTool(
    name="code_review",
    description="审查 Python 文件的语义/安全/架构，返回 {ok,data} JSON。",
    params_json_schema={"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]},
    on_invoke_tool=_code_review,
)

agent = Agent(name="codeguard", instructions="用 code_review 工具审查代码。",
              tools=[code_review_tool])
# result = await Runner.run(agent, "审查 sample_target.py")
```

> 注意：Agents SDK 的 `on_invoke_tool` 接收 JSON 字符串参数，先 `json.loads` 再透传给 `run_capability`。

---

## 5. Claude Code —— 作为 CLI 子代理 / 本地工具

Claude Code 通过命令调用本地工具。CodeAgent 统一入口本身即 CLI，可直接接入：

```bash
# 方式一：Claude Code 系统提示中让模型调用本仓库命令
claude  "运行 python codeagent.py guard sample_target.py --json 并解读结果"

# 方式二：把 codeagent 注册为本地可调用命令（slash-command / hooks 示例）
#   /review  →  python codeagent.py review <file> --json
#   /test    →  python codeagent.py test   <file> --json
#   /guard   →  python codeagent.py guard  <file> --json
```

CLI 集成要点：

```python
import subprocess, json, os
ROOT = os.path.dirname(os.path.abspath("."))

def run_codeagent(subcmd, *args):
    env = dict(os.environ)
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZHIPUAI_API_KEY"):
        env.pop(k, None)                     # 数据不出厂
    p = subprocess.run([sys.executable, os.path.join(ROOT, "codeagent.py"),
                        subcmd, *args, "--json"], cwd=ROOT,
                       capture_output=True, text=True, env=env)
    return json.loads(p.stdout)              # {ok, data} 信封
```

---

## 6. 子代理节点 + 图编排（LangGraph / AutoGen GroupChat / CrewAI Process.sequential / 自研 DAG）

参考：`examples/graph_orchestration.py`（零第三方依赖，自带最小 DAG 编排器）。

**每个节点 = 一次 `run_capability`（或 `codeagent <子命令>` 子进程），节点间传 `{ok, data}` 信封。**

```python
from examples.codeagent_toolkit import run_capability, cli_tool

def node_review(state: dict) -> dict:
    """子代理节点①：审查。返回 {ok,data} 信封。"""
    return json.loads(run_capability("codereview.review", path=state["path"], mode="code"))

def node_guard(state: dict) -> dict:
    """子代理节点②：安全·质量组装链（review+dep-scan+fuzz）。"""
    return json.loads(cli_tool("guard", state["path"]))["guard"]

def node_deliver(state: dict) -> dict:
    """子代理节点③：交付报告，把上游信封摘要写进报告。"""
    return json.loads(run_capability(
        "deliver.report", chain=["review", "guard"],
        outputs={"review_score": state.get("review", {}).get("data", {}).get("score")}))

# 拓扑序：review → guard → deliver
for step, fn in [("review", node_review), ("guard", node_guard), ("deliver", node_deliver)]:
    state[step] = fn(state)                  # 下游读取上游信封
```

运行自测：

```bash
python examples/graph_orchestration.py
```

> 图编排框架（LangGraph / AutoGen GroupChat / CrewAI `Process.sequential`）只需把「节点函数」换成对应框架的节点回调，信封传递逻辑不变。

---

## 7. REST 集成（web_api_client）

参考：`examples/web_api_client.py`。最小 web 服务 `web/server.py`（纯标准库 `http.server`）暴露 REST 端点，供任意语言/框架调用：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 探活（不泄露本机绝对路径） |
| `/api/status` | GET | 32 原子状态（ready/degraded/冲突） |
| `/api/files` | GET | 可作审查/测试目标的白名单源码文件 |
| `/api/run` | POST | `{cmd: review|test|chain|guard|evolve|status, payload:{...}}` |

```bash
# 启动（绑定 127.0.0.1，数据不出厂；可选 --token 加访问令牌）
python web/server.py --port 8080
python web/server.py --port 8080 --token mysecret        # 需请求头带 X-Token
```

```python
from examples.codeagent_toolkit import http_tool

print(http_tool(cmd="review", payload={"path": "sample_target.py"}))
print(http_tool(cmd="status"))
```

运行自测：

```bash
python web/server.py --port 8080 &        # 先启动服务
python examples/web_api_client.py
```

> 安全加固：默认绑定 `127.0.0.1`；路径穿越防护（仅白名单源码文件）；POST body 上限 1MB 防 DoS；跨站 Origin 拦截；可选 `X-Token` 鉴权。

---

## 集成边界与最佳实践

1. **本地原子 / 数据不出厂**：默认 `local_only=True`；`_env()` 剥离云端密钥；`depscan` 默认不查远端 OSV；LLM/MCP 远端需显式 `allow_remote`/`--remote`。
2. **开源内核 / 闭源编排**：本仓库只分发 32 原子 + 运行时/入口。重型编排（assembler/orchestrator/CodeMode）在独立工作区，不随本仓库分发、不链接、非必需。你在框架内自行编排原子即「开源内核 + 开源编排」。
3. **统一信封**：所有原子返回 `{ok, data}` JSON 字符串，任何框架 `json.loads` 后按 `data` 字段取用；失败自动 `{ok:false, degraded:true}`，下游需做降级判断。
4. **权限最小化**：`dispatch.permission` 提供 allow/ask/deny 细粒度策略，可拦截工具/命令/文件三类资源，接入高风险动作时建议启用。
5. **目标白名单**：REST `/api/run` 仅允许 `web/server.py` 白名单内的项目源码文件，防任意文件读取。
