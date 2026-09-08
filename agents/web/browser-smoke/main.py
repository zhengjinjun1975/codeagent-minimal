#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser-smoke 原子壳（open_source:true）— 真实浏览器 UI 冒烟。

能力：
  browsersmoke.run / ui.smoke — 起/复用 headless Chrome + CDP，对 Web 前端做冒烟：
      可选登录 → 逐路由导航 → 断言(无白屏 / 无错误边界 / 关键文本在 / console 错误数)
      → 逐路由 PASS/FAIL 报告。

只加壳：CDP 驱动逻辑全部在本原子内实现（复用 lab/_cdp_frontend.py 的写法：
urllib PUT /json/new 建 tab + websocket-client 单连接同步收发 + Runtime.evaluate）。
纯 stdlib + 现成依赖(websocket-client)，不引新 pip/npm。数据不出私域。

真实浏览器决策要点(遵循 lab 既有 CDP 踩坑):
  - 起 headless 用 user-data-dir 正斜杠绝对路径 + --remote-allow-origins=*（新版 Chrome 必加，
    否则 CDP WebSocket 握手 403）。
  - 建 tab 用 PUT /json/new（不带 url），导航一律显式 Page.navigate（有的版本 /json/new?url= 停 about:blank）。
  - console/异常/网络失败捕获用页面内 hook（onerror / console.error / unhandledrejection / fetch），
    不依赖 CDP Network/Log 事件（单连接同步 recv 会吞事件）。每次 Page.navigate 都是全新 JS world，
    导航完成后重注 hook 一次。
  - 每路由独立断言：白屏(正文过短) / 错误边界文本 / 关键文本在(轮询到出现) / console 错误计数。

加壳不改核心：本文件只 import atomic_base 提供信封，不触碰核心算法。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
# agents/web/browser-smoke → 向上 3 层到 code-agent 仓库根
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent  # noqa: E402

# 默认被判定为“错误边界/崩溃”的正文标记（用户 routes[].expectNo 追加）
DEFAULT_ERROR_MARKERS = [
    "页面出现错误",
    "抱歉，页面遇到了一些问题",
    "抱歉,页面遇到了一些问题",
    "Application error: a client-side exception has occurred",
    "An error occurred in the application",
    "Internal Server Error",
    "Oops! Something went wrong",
    "Something went wrong",
]

# 浏览器候选（先探 override，再常见路径；存在才用）
def _chrome_candidates():
    cands = []
    env = os.environ.get("BROWSER_SMOKE_CHROME")
    if env:
        cands.append(env)
    cands += [
        r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        r"C:/Program Files/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files/Google/Chrome Dev/Application/chrome.exe",
    ]
    out = []
    for c in cands:
        if c and os.path.isfile(c) and c not in out:
            out.append(c)
    return out


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _as_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            if isinstance(v, list):
                return v
        except Exception:  # noqa: BLE001
            pass
        return []
    return []


def _as_dict(x):
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            if isinstance(v, dict):
                return v
        except Exception:  # noqa: BLE001
            pass
    return {}


class _CDP:
    """单连接同步 CDP 驱动：urllib 建 tab + websocket-client 收发。"""

    def __init__(self, ws_url):
        import websocket  # 现成依赖（项目已用 websocket-client）
        self.ws = websocket.create_connection(ws_url, timeout=120)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                return m.get("result", {})
            # 自动接受对话框，避免卡死
            if m.get("method") == "Page.javascriptDialogOpening":
                try:
                    self.ws.send(json.dumps({"id": self._id + 60000,
                                             "method": "Page.handleJavaScriptDialog",
                                             "params": {"accept": True, "promptText": ""}}))
                except Exception:  # noqa: BLE001
                    pass

    def ev(self, expr, timeout=None):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if r.get("exceptionDetails"):
            return {"ERR": json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400]}
        return r.get("result", {}).get("value")

    def nav(self, url):
        self.send("Page.navigate", {"url": url})

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


# 页面内 hook：捕获 JS 异常 / console.error / 网络失败。每导航后的全新 JS world 注入一次。
_SMOKE_HOOK = r"""
(function(){
  if (!window.__bsHook) {
    window.__bsHook = true;
    window.__bs = { err: [], net: [] };
    try {
      window.onerror = function(m, src, ln, col, e){
        try { window.__bs.err.push({t:'error', m: String(m).slice(0,300)}); } catch(_){}
        return false;
      };
    } catch(_){}
    try {
      window.addEventListener('unhandledrejection', function(ev){
        try { var r = ev && ev.reason; var m = (r && (r.message || r.toString)) ? (r.message || String(r)) : String(r||''); window.__bs.err.push({t:'unhandledrejection', m: m.slice(0,300)}); } catch(_){}
      });
    } catch(_){}
    try {
      var ce = window.console.error.bind(window.console);
      window.console.error = function(){
        var parts = [];
        for (var i=0;i<arguments.length;i++){ try { var a=arguments[i]; parts.push(typeof a==='string'?a:(JSON.stringify(a)||String(a))); } catch(_){ parts.push(String(arguments[i])); } }
        window.__bs.err.push({t:'console', m: parts.join(' ').slice(0,300)});
        ce.apply(window.console, arguments);
      };
    } catch(_){}
    try {
      var orig = window.fetch.bind(window);
      window.fetch = function(){
        var arg0 = arguments[0];
        var u = (typeof arg0==='string') ? arg0 : ((arg0 && arg0.url)||'');
        var p = orig.apply(window, arguments);
        p.then(function(r){ try { window.__bs.net.push({u:u, s:r.status}); } catch(_){ } return r; },
               function(err){ try { window.__bs.net.push({u:u, ERR:String(err)}); } catch(_){ } throw err; });
        return p;
      };
    } catch(_){}
  }
})(); true
"""


class BrowserSmokeAgent(AtomicAgent):
    name = "browser-smoke"
    version = "0.1.0"
    domain = "web"
    description = ("浏览器冒烟原子(真实浏览器 UI 冒烟): 起/复用 headless Chrome+CDP, "
                   "可选登录→逐路由→断言无白屏/无错误边界/关键文本在/console错误数→逐路由 PASS/FAIL 报告。"
                   "复用 lab/_cdp_frontend.py 的 CDP 写法，纯 stdlib+现成依赖。")
    provides = ["browsersmoke.run", "ui.smoke"]
    depends_on = []
    inputs = ["url", "login", "routes", "chrome_path", "port", "headless", "wait_sec", "min_body"]
    outputs = ["results", "summary", "ok"]

    def _register_defaults(self):
        self.register("browsersmoke.run", self._run)
        self.register("ui.smoke", self._run)

    # ── 浏览器二进制定位 / 启动 ────────────────────────
    def _launch(self, chrome_path, port):
        """尝试用候选浏览器启动 headless + CDP。返回 (proc, user_dir, base) 或抛异常。"""
        user_dir = tempfile.mkdtemp(prefix="bs_smoke_")
        # Windows 坑：user-data-dir 必须正斜杠绝对路径，否则被判默认 profile 拒远程调试
        udir_fwd = user_dir.replace("\\", "/")
        cands = [chrome_path] if chrome_path else []
        cands = [c for c in cands if c] + _chrome_candidates()
        if not cands:
            raise RuntimeError("未找到 Chrome/Edge 可执行文件（可传 chrome_path 指定）")
        last = None
        for exe in cands:
            proc = None
            try:
                proc = subprocess.Popen(
                    [exe,
                     "--headless=new", "--disable-gpu",
                     f"--remote-debugging-port={port}",
                     "--remote-allow-origins=*",
                     f"--user-data-dir={udir_fwd}",
                     "--no-first-run", "--no-default-browser-check",
                     "about:blank"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=os.path.dirname(HERE))
                # 轮询就绪
                base = f"http://127.0.0.1:{port}"
                for _ in range(60):
                    try:
                        with urllib.request.urlopen(base + "/json/version", timeout=1):
                            return proc, user_dir, base
                    except Exception:  # noqa: BLE001
                        time.sleep(0.3)
                # 该二进制没起来 → 清掉再试下一个
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                last = f"浏览器启动失败(端口未就绪): {exe}"
            except Exception as e:  # noqa: BLE001
                last = f"浏览器启动异常 {exe}: {type(e).__name__}: {e}"
                if proc is not None:
                    try:
                        proc.terminate()
                    except Exception:  # noqa: BLE001
                        pass
        raise RuntimeError(str(last or "无可用浏览器"))

    def _new_tab(self, base):
        req = urllib.request.Request(base + "/json/new", method="PUT")
        tab = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        return tab["webSocketDebuggerUrl"]

    # ── 单路由冒烟 ─────────────────────────────────
    def _snapshot_expr(self, markers):
        mj = json.dumps(markers, ensure_ascii=False)
        return ("(function(m){var b=(window.__bs&&document.body)?document.body.innerText:'';"
                "var h=(m||[]).filter(function(x){return (b||'').indexOf(x)>=0;});"
                "var e=(window.__bs||{err:[],net:[]});"
                "var nf=(e.net||[]).filter(function(n){return (n&&n.s&&n.s>=400)||(n&&n.ERR);}).length;"
                "return {len:(b||'').length, hit:h, err:(e.err||[]).length, netf:nf};})(" + mj + ")")

    def _smoke_route(self, cdp, base, route, wait_sec, min_body, markers):
        """导航 + 逐断言。返回单路由 result dict。"""
        path = route.get("path", route if isinstance(route, str) else "")
        if path and path.startswith("http"):
            full = path
        else:
            full = base + str(path).lstrip("/")
        expect = route.get("expectText") or []
        expect = [expect] if isinstance(expect, str) else list(expect or [])
        expect_no = list(route.get("expectNo") or [])
        expect_wait = float(route.get("waitSec", wait_sec) or wait_sec)
        strict_console = bool(route.get("strictConsole", False))
        r_min_body = int(route.get("minBody", min_body) or min_body)
        r_markers = markers + expect_no
        sel = route.get("selectors") or {}  # 可选的点击选择器等，预留

        cdp.nav(full)
        # 等待导航离开 about:blank / 正文就绪
        cdp.ev(_SMOKE_HOOK)  # 先注入（空页也可，页面加载完成会再注一次兜底）

        # —— 1) 等加载：正文长度达标 或 命中错误标记(尽早判错) ——
        body_len, hits, console_err, net_fail = 0, [], 0, 0
        t0 = time.time()
        while time.time() - t0 < wait_sec:
            snap = cdp.ev(self._snapshot_expr(r_markers))
            if isinstance(snap, dict):
                body_len = snap.get("len", 0)
                hits = snap.get("hit") or []
                console_err = snap.get("err", 0)
                net_fail = snap.get("netf", 0)
                if hits:
                    break
                if body_len >= r_min_body:
                    break
                if body_len == 0 and full.startswith("http") and "about:blank" in str(cdp.ev("location.href")):
                    pass
            time.sleep(0.4)
        # —— 2) 关键文本在（轮询到出现） ——
        key_missing = []
        for k in expect:
            found = self._wait_text(cdp, k, expect_wait, r_min_body)
            if not found:
                key_missing.append(k)
        # —— 3) 读最终快照 ——
        final = cdp.ev(self._snapshot_expr(r_markers))
        if isinstance(final, dict):
            body_len = final.get("len", body_len)
            hits = final.get("hit") or hits
            console_err = final.get("err", console_err)
            net_fail = final.get("netf", net_fail)

        white = body_len < r_min_body
        boundary = bool(hits)
        fails = []
        if white:
            fails.append(f"白屏/空正文(len={body_len}<{r_min_body})")
        if boundary:
            fails.append(f"错误边界文本: {hits}")
        if key_missing:
            fails.append(f"缺关键文本: {key_missing}")
        if strict_console and console_err > 0:
            fails.append(f"console/异常错误 {console_err} 条(strict)")
        ok = not fails
        reason = "; ".join(fails) if fails else "OK"

        return {
            "route": path,
            "url": full,
            "ok": ok,
            "reason": reason,
            "bodyLen": body_len,
            "consoleErrors": console_err,
            "httpErrors": net_fail,
            "white": white,
            "errorBoundary": bool(boundary),
            "missingText": key_missing,
        }

    def _wait_text(self, cdp, text, timeout, min_body):
        t0 = time.time()
        expr = ("(function(t){var b=(window.__bs&&document.body)?document.body.innerText:'';"
                "return (b||'').indexOf(t)>=0;})(" + json.dumps(text, ensure_ascii=False) + ")")
        while time.time() - t0 < timeout:
            try:
                if cdp.ev(expr) is True:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)
        return False

    # ── 登录（可选） ────────────────────────────────
    def _do_login(self, cdp, base, login):
        """填写并提交登录表单。login: {url?, user, pass, userSel, passSel, submitSel, waitAfter?}。
        realKey=True 时用真实键盘(Input)注入，兼容 Svelte bind:value；默认原生赋值+事件(vanilla/React)。"""
        lg = dict(login)
        lg_url = lg.get("url") or base
        need = ["user", "pass", "userSel", "passSel"]
        for k in need:
            if k not in lg or lg[k] is None or lg[k] == "":
                return {"ok": False, "reason": f"登录缺字段 {k}"}
        cdp.nav(lg_url)
        time.sleep(0.8)
        cdp.ev(_SMOKE_HOOK)
        # 等登录框出现
        t0 = time.time()
        user_sel = lg["userSel"]
        while time.time() - t0 < 8:
            if cdp.ev(f"!!document.querySelector({json.dumps(user_sel)})") is True:
                break
            time.sleep(0.4)
        real = bool(lg.get("realKey", False))
        self._fill(cdp, lg["userSel"], lg["user"], real)
        self._fill(cdp, lg["passSel"], lg["pass"], real)
        # 提交
        sub_sel = lg.get("submitSel")
        if sub_sel:
            cdp.ev("(function(){var el=document.querySelector(" + json.dumps(sub_sel) + ");"
                   "if(!el)return 'NOSUB';"
                   "(el.click?el.click():el.dispatchEvent(new MouseEvent('click',{bubbles:true})));return 'ok';})()")
        else:
            # 无 submitSel → 回车
            cdp.send("Input.dispatchKeyEvent",
                     {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
            cdp.send("Input.dispatchKeyEvent",
                     {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        wait_after = float(lg.get("waitAfter", 1.5) or 1.5)
        time.sleep(wait_after)
        return {"ok": True, "reason": "login submitted"}

    def _fill(self, cdp, sel, val, real_key):
        val = str(val)
        if real_key:
            # 真实键盘：focus → 全选 → 输入（兼容受控组件 bind:value）
            cdp.ev("(function(){var el=document.querySelector(" + json.dumps(sel) +
                   ");if(!el)return 'NOEL';el.focus();el.scrollIntoView({block:'center'});return 'ok';})()")
            for evt in (("keyDown", "a", "KeyA", 65, 2), ("keyUp", "a", "KeyA", 65, 2)):
                cdp.send("Input.dispatchKeyEvent", {"type": evt[0], "key": evt[1], "code": evt[2],
                                                    "windowsVirtualKeyCode": evt[3], "modifiers": evt[4]})
            cdp.send("Input.dispatchKeyEvent",
                     {"type": "keyDown", "key": "Backspace", "code": "Backspace",
                      "windowsVirtualKeyCode": 8})
            cdp.send("Input.dispatchKeyEvent",
                     {"type": "keyUp", "key": "Backspace", "code": "Backspace",
                      "windowsVirtualKeyCode": 8})
            cdp.send("Input.insertText", {"text": val})
        else:
            # 原生 value setter + input/change（vanilla / React 受控组件通用）
            expr = ("(function(){var el=document.querySelector(" + json.dumps(sel) + ");"
                    "if(!el)return 'NOEL';"
                    "var proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
                    "var set=Object.getOwnPropertyDescriptor(proto,'value').set;"
                    "set.call(el, " + json.dumps(val) + ");"
                    "el.dispatchEvent(new Event('input',{bubbles:true}));"
                    "el.dispatchEvent(new Event('change',{bubbles:true}));return 'ok';})()")
            cdp.ev(expr)

    # ── 主能力 ─────────────────────────────────────
    def _run(self, url=None, login=None, routes=None, chrome_path=None, port=None,
             headless=True, wait_sec=10, min_body=20):
        """浏览器冒烟。url 必填；routes 可选(list[{path,expectText?,expectNo?,strictConsole?}] 或 str 路径)。
        返回 {results:[{route,ok,reason,...}], summary, ok}。单次执行失败返回 ok=false+reason，不自行无限重试。"""
        if not url:
            return {"ok": False, "error": "缺必填入参 url",
                    "data": {"results": [], "summary": "no url"}}
        headless = False if headless in (False, "false", "0", 0) else True
        wait_sec = float(wait_sec or 10)
        min_body = int(min_body or 20)
        if min_body <= 0:
            min_body = 1
        port = int(port or 0) or _free_port()
        routes = _as_list(routes)
        if not routes:
            routes = [{"path": ""}]
        login = _as_dict(login)
        markers = list(DEFAULT_ERROR_MARKERS)

        base = url if url.endswith("/") else url + "/"
        cdp = None
        proc = None
        user_dir = None
        try:
            proc, user_dir, cdp_base = self._launch(chrome_path, port)
            ws_url = self._new_tab(cdp_base)
            cdp = _CDP(ws_url)
            cdp.send("Runtime.enable")
            cdp.send("Page.enable")
            # 页面级 hook 必须在文档 start 注入，才能在每次导航捕获异步 JS 错误/console/网络失败；
            # addScriptToEvaluateOnNewDocument 对后续每次新文档都执行一次（覆盖所有路由）。
            try:
                cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": _SMOKE_HOOK})
            except Exception:  # noqa: BLE001
                pass
            if headless is False:
                try:
                    cdp.send("Emulation.setDeviceMetricsOverride",
                             {"width": 1280, "height": 900, "deviceScaleFactor": 1,
                              "mobile": False})
                except Exception:  # noqa: BLE001
                    pass
            login_note = ""
            if login:
                lr = self._do_login(cdp, base, login)
                login_note = ("login:ok" if lr.get("ok") else f"login:fail({lr.get('reason')})")
            results = []
            for route in routes:
                try:
                    results.append(self._smoke_route(cdp, base, route, wait_sec, min_body, markers))
                except Exception as e:  # noqa: BLE001
                    path = route.get("path", route if isinstance(route, str) else "")
                    results.append({"route": path, "ok": False,
                                    "reason": f"冒烟执行异常 {type(e).__name__}: {e}",
                                    "consoleErrors": 0, "bodyLen": 0, "white": True})
            passes = [r for r in results if r.get("ok")]
            summary = (f"{len(passes)}/{len(results)} PASS" + (f" | {login_note}" if login else ""))
            return {
                "ok": len(passes) == len(results),
                "data": {
                    "results": results,
                    "summary": summary,
                    "login": login_note,
                    "chrome": proc.args[0] if proc is not None else None,
                },
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False,
                    "data": {"results": [], "summary": f"浏览器冒烟失败: {type(e).__name__}: {e}"},
                    "error": f"{type(e).__name__}: {e}"}
        finally:
            if cdp is not None:
                try:
                    cdp.close()
                except Exception:  # noqa: BLE001
                    pass
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
            if user_dir and os.path.isdir(user_dir):
                try:
                    import shutil
                    shutil.rmtree(user_dir, ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass


agent = BrowserSmokeAgent()

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(BrowserSmokeAgent(), run_args={
        "capability": {"default": "browsersmoke.run", "choices": list(BrowserSmokeAgent.provides)},
        "url": {},
        "login": {},
        "routes": {},
        "chrome_path": {},
        "port": {},
        "headless": {},
        "wait_sec": {},
        "min_body": {},
    }))
