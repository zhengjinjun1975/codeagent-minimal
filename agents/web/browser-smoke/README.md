# browser-smoke（浏览器冒烟原子）

新增能力：**真实浏览器 UI 冒烟** —— 起/复用 headless Chrome/Edge + CDP，对 Web 前端做冒烟验收，
而不只是审 Python 代码。

## 能力
- `browsersmoke.run` / `ui.smoke`（中文名“浏览器冒烟”）
  可选登录 → 逐路由导航 → 断言(无白屏 / 无错误边界文本 / 关键文本在 / console 错误计数)
  → 逐路由 PASS/FAIL 报告。

## 入参
| 参数 | 说明 |
|---|---|
| `url` | 必填，被测站点根（登录页或应用首页）。如 `http://127.0.0.1:8087` |
| `login` | 可选 dict：`{user, pass, userSel, passSel, submitSel?, realKey?, waitAfter?}`。`realKey=true` 用真实键盘注入（兼容 Svelte `bind:value`）；默认原生赋值+事件（vanilla/React） |
| `routes` | 路由清单 `[{path, expectText?, expectNo?, waitSec?, strictConsole?}]`。`expectText` 关键文本（须出现，轮询）；`expectNo` 视为错误边界的额外标记；`strictConsole=true` 时 console 有错即 FAIL。也允许传字符串 path |
| `chrome_path` | 可选，浏览器可执行文件绝对路径（默认自动探测 Edge/Chrome） |
| `port` | 可选，CDP 调试端口（默认自动找空闲端口） |
| `headless` | 默认 true |
| `wait_sec` | 每路由等待加载秒数（默认 10） |
| `min_body` | 正文长度阈值，低于视为白屏（默认 20） |

## 输出
`{ ok, results:[{route,url,ok,reason,bodyLen,consoleErrors,httpErrors,white,errorBoundary,missingText}], summary, login }`

冒烟失败（任一路由 FAIL）→ 顶层信封 `ok=false` + 逐路由 reason，供 loop.retry“可修回绕”语义；
原子内单次执行不做无限重试。

## 实现说明
CDP 驱动写法复用 `lab/_cdp_frontend.py`（urllib `PUT /json/new` 建 tab + websocket-client
单连接同步收发 + `Runtime.evaluate`），console/异常/网络失败用页面内 hook 捕获（onerror /
console.error / unhandledrejection / fetch），不引新 pip/npm 依赖。浏览器启动遵循既有坑：
user-data-dir 正斜杠绝对路径 + `--remote-allow-origins=*` + 显式 `Page.navigate`。

## 调用示例（进程内/编排体）
```python
from agent_loader import load_agent, AGENTS_DIR
r = load_agent(os.path.join(AGENTS_DIR, "web", "browser-smoke"))
agent = r["data"]["agent"]
out = agent.call("browsersmoke.run", url="http://127.0.0.1:8087", routes=[
    {"path": "/", "expectText": "CodeAgent Lab"},
    {"path": "/", "expectText": "__必缺文本__"},   # 期望 FAIL
])
```
或 CLI：`python agents/web/browser-smoke/main.py --capability browsersmoke.run --url http://127.0.0.1:8087 --routes '[...]'`
