#!/usr/bin/env python3
"""CDP 真实浏览器验证 CodeAgent 前端：页面加载 + 16原子 + 运行审查/测试 + 状态."""
import json, time, urllib.request, subprocess, shutil, base64
from subprocess import Popen
import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
import tempfile
PROFILE = os.path.join(tempfile.gettempdir(), "cdp_codeagent_profile")
PORT = 9223
URL = "http://127.0.0.1:8099/"

# 清 profile
shutil.rmtree(PROFILE, ignore_errors=True)

proc = Popen([CHROME, "--headless", "--disable-gpu", "--no-first-run",
              f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
              f"--user-data-dir={PROFILE}", "about:blank"],
             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 等 chrome 起来
for _ in range(40):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
        break
    except Exception:
        time.sleep(0.25)

# 建空 tab 再导航（可靠做法）
req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new",
                             data=b"", method="PUT")
page = json.loads(urllib.request.urlopen(req, timeout=5).read())
ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
mid = 0
def send(method, params=None):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == mid:
            return m

def ev(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                  "awaitPromise": True})
    try:
        return r["result"]["result"].get("value")
    except Exception:
        return {"ERR": r.get("result", {}).get("exceptionDetails", {})}

send("Page.enable"); send("Runtime.enable")
send("Page.navigate", {"url": URL})

# 等页面加载
loaded = False
for _ in range(40):
    t = ev("document.title")
    if t and t != "about:blank":
        loaded = True
        break
    time.sleep(0.3)
print("PAGE_TITLE:", ev("document.title"))
print("PAGE_LOADED:", loaded)

# 等 16 原子渲染
atoms = []
for _ in range(40):
    atoms = ev("document.querySelectorAll('.atom').length")
    if atoms and atoms >= 16:
        break
    time.sleep(0.3)
print("ATOM_PILLS:", atoms)

# 原子状态文本
st = ev("[...document.querySelectorAll('.atom')].map(x=>x.className)")
print("ATOM_STATES_SAMPLE:", st[:5], "..." if isinstance(st, list) and len(st) > 5 else "")

# 填审查目标文件 select + 点运行审查
print("RV_OPTIONS:", ev("[...document.querySelectorAll('#rv-file option')].map(o=>o.value)"))
print("RV_SET:", ev("(()=>{const s=document.querySelector('#rv-file');s.value='bad_sample.py';s.dispatchEvent(new Event('change',{bubbles:true}));return s.value;})()"))
print("RUNNING_BEFORE:", ev("window.running"))
ev("runReview();''")
print("RUNNING_AFTER:", ev("window.running"))
time.sleep(12)
rv = ev("document.querySelector('#rv-result pre').textContent")
print("REVIEW_RESULT_OK:", "运行审查" in (rv or "") or ("review" in (rv or "")))
print("REVIEW_FRAGMENT:", (rv or "")[:160].replace("\n", " "))
print("REVIEW_ERRBAR:", ev("document.querySelector('#errbar').textContent"))
print("REVIEW_WRAP_SHOW:", ev("document.querySelector('#rv-result').className"))

# 运行测试
ev("(()=>{const s=document.querySelector('#ts-file');s.value='sample_target.py';s.dispatchEvent(new Event('change',{bubbles:true}));return s.value;})()")
ev("runTest();''")
time.sleep(12)
ts = ev("document.querySelector('#ts-result pre').textContent")
print("TEST_RESULT_OK:", "test" in (ts or "") or "测试" in (ts or ""))
print("TEST_FRAGMENT:", (ts or "")[:160].replace("\n", " "))

# 运行状态
ev("runStatus();''")
time.sleep(3)
st2 = ev("document.querySelector('#st-result pre').textContent")
print("STATUS_RESULT_OK:", "status" in (st2 or ""))
print("STATUS_FRAGMENT:", (st2 or "")[:120].replace("\n", " "))

# 截图落盘
shot = send("Page.captureScreenshot", {"format": "png"})
data = shot.get("result", {}).get("data")
if data:
    import base64
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_verify_frontend.png"), "wb").write(
        base64.b64decode(data))
    print("SCREENSHOT_SAVED: web/_verify_frontend.png")

proc.terminate()
print("DONE")
