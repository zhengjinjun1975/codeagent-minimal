#!/usr/bin/env python3
"""web/server.py — CodeAgent 最小化运行前端（纯标准库，无重型框架）。

纯 http.server 实现：
  - 托管静态 index.html（浅色/中文/按钮/结果展示）
  - API 端点真实调用统一入口 codeagent.py（subprocess），
    显示 16 原子状态(ready/degraded/冲突) + 运行 review/test/chain/guard/evolve/status。

启动：
  python web/server.py [--host 127.0.0.1] [--port 8080]
访问：http://127.0.0.1:8080
"""

import argparse
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # codeagent-minimal 项目根
CODEAGENT = os.path.join(ROOT, "codeagent.py")
INDEX = os.path.join(HERE, "index.html")

# 审查/测试可选的目标源码文件（相对 ROOT，排除库/测试自身）
SKIP = {"codeagent.py", "server.py"}
_EXT = (".py",)


def _code_targets():
    """列出可选作 review/test 目标的项目源码文件。"""
    out = []
    for root, _dirs, files in os.walk(ROOT):
        rel = os.path.relpath(root, ROOT)
        # 跳过隐藏目录/构建缓存/web 自身；但 ROOT 顶层(rel==".")要保留
        if rel != ".":
            parts = rel.split(os.sep)
            if parts[0].startswith(".") or parts[0] == "__pycache__" or parts[0] == "web":
                continue
        for f in files:
            if f.endswith(_EXT) and f not in SKIP:
                p = os.path.relpath(os.path.join(root, f), ROOT)
                if not p.startswith("."):
                    out.append(p)
    return sorted(out)


def _run_codeagent(args, timeout=180):
    """真实调用统一入口 codeagent.py，返回标准输出 JSON。"""
    cmd = [sys.executable, CODEAGENT] + args + ["--json"]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True,
                              text=True, timeout=timeout, encoding="utf-8",
                              errors="replace")
        out = proc.stdout.strip()
        try:
            return json.loads(out) if out else {"error": "(空输出)", "code": proc.returncode}
        except json.JSONDecodeError:
            return {"raw": out, "stderr": proc.stderr[-2000:], "code": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"error": f"命令超时(>{timeout}s)"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"调用失败: {e}"}


def _status_atoms():
    """16 原子状态：ready / degraded / 冲突。"""
    d = _run_codeagent(["status"])
    atoms = d.get("status") or {}
    order = atoms.get("order") or atoms.get("atoms") or []
    degraded = set(atoms.get("degraded") or [])
    conflicts = set(atoms.get("conflicts") or [])
    loaded = set(atoms.get("atoms") or [])
    rows = []
    for name in order:
        if name in conflicts:
            st = "conflict"
        elif name in degraded:
            st = "degraded"
        elif name in loaded:
            st = "ready"
        else:
            st = "unknown"
        rows.append({"name": name, "status": st})
    return {"count": len(rows), "rows": rows,
            "degraded": atoms.get("degraded"), "conflicts": atoms.get("conflicts"),
            "runtime": atoms.get("runtime")}


def _parse_json_body(self):
    ln = int(self.headers.get("Content-Length") or 0)
    raw = self.rfile.read(ln) if ln else b""
    if not raw:
        return {}
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 精简日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/":
            if os.path.exists(INDEX):
                self._send(200, open(INDEX, encoding="utf-8").read(),
                           "text/html; charset=utf-8")
            else:
                self._send(500, "index.html 缺失")
        elif path == "/api/status":
            self._json({"ok": True, "data": _status_atoms()})
        elif path == "/api/files":
            self._json({"ok": True, "data": _code_targets()})
        elif path == "/api/health":
            self._json({"ok": True, "service": "codeagent-minimal-web",
                        "root": ROOT, "codeagent": os.path.exists(CODEAGENT)})
        else:
            self._json({"ok": False, "error": "404"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if path != "/api/run":
            self._json({"ok": False, "error": "404"}, 404)
            return
        body = _parse_json_body(self)
        cmd = body.get("cmd")
        payload = body.get("payload", {})
        if cmd == "status":
            r = _run_codeagent(["status"])
            self._json({"ok": True, "cmd": "status", "result": r})
            return
        if cmd == "review":
            target = payload.get("path")
            if not target:
                self._json({"ok": False, "error": "请选择审查目标文件"}, 400)
                return
            r = _run_codeagent(["review", target])
            self._json({"ok": True, "cmd": "review", "target": target, "result": r})
            return
        if cmd == "test":
            target = payload.get("path")
            if not target:
                self._json({"ok": False, "error": "请选择测试目标文件"}, 400)
                return
            args = ["test", target]
            if payload.get("no_mutation"):
                args.append("--no-mutation")
            r = _run_codeagent(args)
            self._json({"ok": True, "cmd": "test", "target": target, "result": r})
            return
        if cmd == "chain":
            task = payload.get("task")
            if not task:
                self._json({"ok": False, "error": "请填写组装链任务描述"}, 400)
                return
            args = ["chain", "--task", task]
            if payload.get("code"):
                try:
                    args += ["--code", json.dumps(payload["code"], ensure_ascii=False)]
                except Exception:  # noqa: BLE001
                    pass
            r = _run_codeagent(args, timeout=300)
            self._json({"ok": True, "cmd": "chain", "result": r})
            return
        if cmd == "guard":
            target = payload.get("path")
            if not target:
                self._json({"ok": False, "error": "请选择 guard 目标文件"}, 400)
                return
            r = _run_codeagent(["guard", target])
            self._json({"ok": True, "cmd": "guard", "target": target, "result": r})
            return
        if cmd == "evolve":
            task = payload.get("task")
            outcome = payload.get("outcome")
            if not task or not outcome:
                self._json({"ok": False, "error": "evolve 需 task 与 outcome 参数"}, 400)
                return
            r = _run_codeagent(["evolve", "--task", task,
                                "--outcome", json.dumps(outcome, ensure_ascii=False)])
            self._json({"ok": True, "cmd": "evolve", "result": r})
            return
        self._json({"ok": False, "error": f"未知命令: {cmd}"}, 400)


def main():
    ap = argparse.ArgumentParser(description="CodeAgent 最小化运行前端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"CodeAgent 运行前端已启动: http://{args.host}:{args.port}")
    print(f"项目根: {ROOT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.server_close()


if __name__ == "__main__":
    main()
