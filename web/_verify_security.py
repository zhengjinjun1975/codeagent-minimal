#!/usr/bin/env python3
"""web/_verify_security.py — Web 安全边界 live 实测（真实起服务，逐项探测）。

安全加固 codeagent-security-hardening 的逐项实测：
  1. 未授权访问：配置 token 后，无/错 X-Token → 401；正确 token → 200
  2. 路径穿越：review 目标 `../LICENSE`、绝对路径 → 400 拒绝；白名单内文件 → 200
  3. 跨站/越权：跨源 Origin(evil) → 403；同源/无 Origin → 放行
  4. 请求体超限(>1MB) → 413（防 DoS）
  5. 敏感信息不泄露：/api/health 不回显本机绝对路径 root
  6. 未知命令 → 400；数据不出厂 local_only 由 tests/test_security_boundary.py 覆盖

用法：python web/_verify_security.py
退出码 0=全 PASS；非 0=有 FAIL。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(HERE, "server.py")

TOKEN = "boundary-test-token-2026"
fails = []
passed = []


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(port, method, path, body=None, token=None, origin=None, timeout=30):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Token", token)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore"), dict(e.headers)


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    (passed if cond else fails).append(name)
    print(f"  [{tag}] {name}" + (f"  {detail}" if detail else ""))


def _start_server(port, token=None):
    cmd = [sys.executable, SERVER, "--host", "127.0.0.1", "--port", str(port)]
    if token:
        cmd += ["--token", token]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    # 等就绪
    for _ in range(50):
        try:
            st, body, h = _req(port, "GET", "/api/health", token=token, timeout=2)
            if st in (200, 401):
                return proc
        except Exception:
            pass
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError(f"server {port} 未就绪")


def main():
    print("═══ CodeAgent Web 安全边界 live 实测 ═══")

    # ── A. token 鉴权模式 ─────────────────────────────
    pa = _free_port()
    proc_a = _start_server(pa, token=TOKEN)
    try:
        print(f"\n── A. token 鉴权模式 (port {pa}) ──")
        # 未授权访问：无 token / 错 token
        st, body, _ = _req(pa, "GET", "/api/status")
        check("未授权访问：无 token 拒绝(401)", st == 401, f"st={st}")
        st, body, _ = _req(pa, "GET", "/api/status", token="wrong-token")
        check("未授权访问：错误 token 拒绝(401)", st == 401, f"st={st}")
        # 正确 token
        st, body, _ = _req(pa, "GET", "/api/status", token=TOKEN)
        check("授权访问：正确 token 放行(200)", st == 200, f"st={st}")
        st, body, _ = _req(pa, "GET", "/api/health", token=TOKEN)
        check("授权访问：health 放行(200)", st == 200, f"st={st}")

        # 路径穿越：../ 与绝对路径
        st, body, _ = _req(pa, "POST", "/api/run", body={"cmd": "review", "payload": {"path": "../LICENSE"}}, token=TOKEN)
        check("路径穿越：../LICENSE 拒绝(400)", st == 400, f"st={st} body={body[:80]}")
        st, body, _ = _req(pa, "POST", "/api/run", body={"cmd": "review", "payload": {"path": "E:/Windows/win.ini"}}, token=TOKEN)
        check("路径穿越：绝对路径拒绝(400)", st == 400, f"st={st}")
        st, body, _ = _req(pa, "POST", "/api/run", body={"cmd": "review", "payload": {"path": "../../../LICENSE"}}, token=TOKEN)
        check("路径穿越：归一化逃逸拒绝(400)", st == 400, f"st={st}")
        # 白名单内合法文件放行
        st, body, _ = _req(pa, "POST", "/api/run", body={"cmd": "review", "payload": {"path": "agents/codereview/code-review/main.py"}}, token=TOKEN, timeout=120)
        check("合法白名单文件放行(200)", st == 200, f"st={st}")

        # 越权/跨站：跨源 Origin 拒绝
        st, body, _ = _req(pa, "POST", "/api/run", body={"cmd": "status"}, token=TOKEN, origin="http://evil.example.com")
        check("跨站越权：evil Origin 拒绝(403)", st == 403, f"st={st} body={body[:80]}")

        # 请求体超限（>1MB）
        big = {"cmd": "status", "payload": {"pad": "x" * (2 * 1024 * 1024)}}
        st, body, _ = _req(pa, "POST", "/api/run", body=big, token=TOKEN)
        check("请求体超限(>1MB)拒绝(413)", st == 413, f"st={st}")

        # 未知命令
        st, body, _ = _req(pa, "POST", "/api/run", body={"cmd": "system", "payload": {}}, token=TOKEN)
        check("未知命令拒绝(400)", st == 400, f"st={st}")

        # 敏感信息：health 不回显本机绝对路径
        st, body, _ = _req(pa, "GET", "/api/health", token=TOKEN)
        leaked = ("root" in body and (r"E:" in body or r"C:" in body or "/" in json.loads(body).get("root", "")))
        check("health 不回显本机绝对路径", "root" not in json.loads(body), f"body={body[:120]}")
    finally:
        proc_a.kill()
        proc_a.wait()

    # ── B. 无 token 默认模式（默认封锁跨站）────────────
    pb = _free_port()
    proc_b = _start_server(pb)
    try:
        print(f"\n── B. 默认无 token 模式 (port {pb}) ──")
        st, body, _ = _req(pb, "GET", "/api/status")
        check("默认模式：status 放行(200)", st == 200, f"st={st}")
        st, body, _ = _req(pb, "POST", "/api/run", body={"cmd": "status"}, origin="http://evil.example.com")
        check("默认模式：evil Origin 仍拒绝(403)", st == 403, f"st={st}")
        st, body, _ = _req(pb, "GET", "/api/health")
        check("默认模式：health 不回显 root 路径", "root" not in json.loads(body), f"body={body[:120]}")
    finally:
        proc_b.kill()
        proc_b.wait()

    print("\n" + "=" * 50)
    print(f"PASS {len(passed)} 项 | FAIL {len(fails)} 项")
    if fails:
        print("FAIL:", fails)
        sys.exit(1)
    print("✅ 全部通过：Web 安全边界逐项实测全绿")
    sys.exit(0)


if __name__ == "__main__":
    main()
