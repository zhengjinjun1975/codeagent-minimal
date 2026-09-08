#!/usr/bin/env python3
"""web/server.py — CodeAgent 最小化运行前端（纯标准库，无重型框架）。

纯 http.server 实现：
  - 托管静态 index.html（浅色/中文/按钮/结果展示）
  - API 端点真实调用统一入口 codeagent.py（subprocess），
    显示 16 原子状态(ready/degraded/冲突) + 运行 review/test/chain/guard/evolve/status。

安全加固（codeagent-security-hardening）：
  - P0 路径穿越：review/test/guard 的 target 必须是 ROOT 内 `_code_targets()` 白名单源码文件，
    拒绝 `../` 与绝对路径（防任意文件读取）。
  - P1 DoS：POST body 上限 `_MAX_BODY`（默认 1MB），超限 413 拒绝，防内存耗尽。
  - P1 CSRF/跨站：默认绑定 127.0.0.1；`Access-Control-Allow-Origin` 不再 `*`，
    仅回显同源 Origin；跨源请求（Origin 不匹配）403 拒绝，防恶意网页驱动本地 agent。
  - P1 未授权访问：配置 `CODEAGENT_WEB_TOKEN`（或 `--token`）后，所有 /api/* 须带
    `X-Token`（常数时间比较）才放行，401 拒绝。
  - P2 敏感信息：`/api/health` 不返回本机绝对路径 root，只回布尔探活。

启动：
  python web/server.py [--host 127.0.0.1] [--port 8080] [--token <可选>]
访问：http://127.0.0.1:8080
"""

import argparse
import hmac
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

# 安全加固参数
_MAX_BODY = 1 * 1024 * 1024            # POST body 上限 1MB（防 DoS）
_TOKEN = ""                            # 启动时从 env/CLI 读取
_ALLOWED_ORIGINS = set()               # 同源 Origin 白名单


def _code_targets():
    """列出可选作 review/test 目标的项目源码文件（白名单，安全加固依赖）。"""
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


def _code_target_set():
    """白名单集合（返回前实时构建，文件增删自动同步）。统一归一化为正斜杠。"""
    return {p.replace("\\", "/") for p in _code_targets()}


def _validate_target(target):
    """P0 路径穿越防护：仅放行 ROOT 内白名单源码文件（拒绝 ../ 与绝对路径）。"""
    if not target:
        return None, "缺少 target"
    t = str(target).replace("\\", "/")
    allowed = _code_target_set()
    if t in allowed:
        return t, None
    # 归一化兜底：即使 `..`/绝对路径被拼进 ROOT，仍必须在白名单内才放行
    try:
        real = os.path.realpath(os.path.join(ROOT, t))
        root = os.path.realpath(ROOT)
        if os.path.commonpath([real, root]) == root and os.path.isfile(real):
            rel = os.path.relpath(real, ROOT).replace("\\", "/")
            if rel in allowed:
                return rel, None
    except ValueError:
        pass
    return None, f"非法路径(仅允许项目内源码文件): {t}"


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
        return {"error": f"调用失败: {e.__class__.__name__}"}


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
    if ln > _MAX_BODY:                       # P1 DoS：body 超限
        return {"_oversize": True, "_len": ln}
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

    # ── 安全门：跨站拦截 + 可选 token ─────────────────────
    def _origin_allowed(self):
        origin = self.headers.get("Origin")
        if not origin:                        # 非浏览器(CLI/curl)无 Origin → 放行
            return True
        return origin in _ALLOWED_ORIGINS

    def _authed(self):
        if not _TOKEN:
            return True
        got = self.headers.get("X-Token", "")
        return hmac.compare_digest(got, _TOKEN)

    def _guard(self):
        """返回 (ok, err, status)。跨站/未授权拦截。"""
        if not self._origin_allowed():
            return False, "跨站请求被拒(Origin 不匹配)", 403
        if not self._authed():
            return False, "未授权(缺少或错误 X-Token)", 401
        return True, None, 200

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        origin = self.headers.get("Origin")
        if origin and origin in _ALLOWED_ORIGINS:   # 仅回显同源，不再 `*`
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def do_OPTIONS(self):
        ok, err, status = self._guard()
        if not ok:
            self._json({"ok": False, "error": err}, status)
            return
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin and origin in _ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
        self.end_headers()

    def do_GET(self):
        ok, err, status = self._guard()
        if not ok:
            self._json({"ok": False, "error": err}, status)
            return
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
                        "codeagent": os.path.exists(CODEAGENT)})   # 不回显本机绝对路径
        else:
            self._json({"ok": False, "error": "404"}, 404)

    def do_POST(self):
        ok, err, status = self._guard()
        if not ok:
            self._json({"ok": False, "error": err}, status)
            return
        path = self.path.split("?")[0].rstrip("/")
        if path != "/api/run":
            self._json({"ok": False, "error": "404"}, 404)
            return
        body = _parse_json_body(self)
        if body.get("_oversize"):
            # 排空未读请求体，避免客户端读到连接重置（防 DoS 同时保持干净 413 响应）
            try:
                self.rfile.read(min(int(body.get("_len", 0)), 4 * 1024 * 1024))
            except Exception:  # noqa: BLE001
                pass
            self._json({"ok": False, "error": f"请求体过大(上限 {_MAX_BODY} 字节)"}, 413)
            return
        cmd = body.get("cmd")
        payload = body.get("payload", {})
        if cmd == "status":
            r = _run_codeagent(["status"])
            self._json({"ok": True, "cmd": "status", "result": r})
            return
        if cmd == "review":
            target, terr = _validate_target(payload.get("path"))
            if terr:
                self._json({"ok": False, "error": terr}, 400)
                return
            r = _run_codeagent(["review", target])
            self._json({"ok": True, "cmd": "review", "target": target, "result": r})
            return
        if cmd == "test":
            target, terr = _validate_target(payload.get("path"))
            if terr:
                self._json({"ok": False, "error": terr}, 400)
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
            target, terr = _validate_target(payload.get("path"))
            if terr:
                self._json({"ok": False, "error": terr}, 400)
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
    global _TOKEN, _ALLOWED_ORIGINS
    ap = argparse.ArgumentParser(description="CodeAgent 最小化运行前端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--token", default=None, help="可选：访问令牌(等价 env CODEAGENT_WEB_TOKEN)")
    args = ap.parse_args()

    _TOKEN = args.token or os.environ.get("CODEAGENT_WEB_TOKEN", "") or ""
    host = args.host
    port = args.port
    # 同源白名单：前端由本服务托管，Origin 应为 http://<host>:<port>（含 127.0.0.1/localhost 变体）
    origins = {f"http://{host}:{port}", f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    _ALLOWED_ORIGINS = {o for o in origins if o.startswith("http://127.0.0.1") or o.startswith("http://localhost")}

    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"CodeAgent 运行前端已启动: http://{host}:{port}")
    if _TOKEN:
        print(f"[安全] 已启用访问令牌：所有 /api/* 需 X-Token 头")
    else:
        print("[安全] 未配置令牌（CODEAGENT_WEB_TOKEN）——建议内网/本机使用；已默认封锁跨站请求")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.server_close()


if __name__ == "__main__":
    main()
