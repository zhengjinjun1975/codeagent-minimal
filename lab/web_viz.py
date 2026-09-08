#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web_viz.py — HTTP 端点层（FR1-FR7，纯 stdlib，安全加固沿用 web/server.py 既有模式）。

- 统一契约 {ok, data?, error?}（NFR8 可观测全端点）
- 安全：路径白名单（target_root 内相对路径，拒绝 ../ 与逃逸绝对路径）/
  body 限额 1MB(413) / CSRF 同源回显(跨源 403) / 可选 X-Token 常数时间鉴权(401) /
  /api/health 不泄露路径 / 密钥只进不出（脱敏）
- 事件：GET /api/events?since= 增量拉取（NFR5 事件驱动无死角）
- 每个端点都有真实前端入口（反向断链审计：无前端入口的端点不加）
"""
import json
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from events import get_bus
from lab_config import get_config

_MAX_BODY = 1 * 1024 * 1024
APP = None          # LabApp 实例（lab_app.py 启动时注入）
_APP_LOCK = threading.Lock()


def set_app(app):
    global APP
    with _APP_LOCK:
        APP = app


def get_app():
    return APP


class LabApp:
    """应用根：配置/事件总线/DB（HistoryDB 由 lab_app 注入以支持测试重置）。"""

    def __init__(self, cfg=None, db=None):
        self.cfg = cfg or get_config()
        self.bus = get_bus()
        self.db = db
        self.token = str(self.cfg.get("token") or "")

    # ── 安全门 ────────────────────────────────
    def allowed_origins(self, port):
        return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def _parse_body(self):
    try:
        ln = int(self.headers.get("Content-Length") or 0)
    except ValueError:
        ln = 0
    if ln > _MAX_BODY:
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
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 精简日志
        pass

    # ── 安全门 ───────────────────────────────
    def _origin_allowed(self):
        app = get_app()
        if app is None:
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin in app.allowed_origins(self.server.server_port)

    def _authed(self):
        app = get_app()
        if app is None or not app.token:
            return True
        import hmac
        return hmac.compare_digest(self.headers.get("X-Token", ""), app.token)

    def _guard(self):
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
        app = get_app()
        if app and origin and origin in app.allowed_origins(self.server.server_port):
            self.send_header("Access-Control-Allow-Origin", origin)
        # 事件轮询不缓存
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def _ok(self, data=None):
        self._json({"ok": True, "data": data if data is not None else {}})

    def _err(self, error, code=400):
        self._json({"ok": False, "error": error}, code)

    def do_OPTIONS(self):
        ok, err, status = self._guard()
        if not ok:
            self._json({"ok": False, "error": err}, status)
            return
        self.send_response(204)
        origin = self.headers.get("Origin")
        app = get_app()
        if app and origin and origin in app.allowed_origins(self.server.server_port):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── GET ──────────────────────────────────
    def do_GET(self):
        ok, err, status = self._guard()
        if not ok:
            self._json({"ok": False, "error": err}, status)
            return
        try:
            self._route_get()
        except Exception as e:  # noqa: BLE001 错误不吞
            self.bus_emit("api.error", {"method": "GET", "path": self.path,
                                        "error": f"{type(e).__name__}: {e}"})
            self._err(f"内部错误: {type(e).__name__}: {e}", 500)

    def _route_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        cfg = get_app().cfg
        # 静态
        if path == "/" or path == "/index.html":
            self._static("frontend/index.html", "text/html; charset=utf-8")
            return
        if path in ("/lab.css", "/lab.js"):
            self._static("frontend/" + path.lstrip("/"), "text/css; charset=utf-8"
                         if path.endswith(".css") else "text/javascript; charset=utf-8")
            return
        if path == "/api/health":
            app = get_app()
            self._ok({"service": "codeagent-lab", "atoms_ready": bool(app and app.db),
                      "config": cfg.public()})
            return
        if path == "/api/config":
            self._ok(cfg.public())
            return
        if path == "/api/tree":
            rel = qs.get("root", [""])[0]
            self._ok(self._tree(rel, qs.get("recursive", ["0"])[0] == "1",
                                qs.get("badges", ["0"])[0] == "1"))
            return
        if path == "/api/file":
            rel = qs.get("path", [""])[0]
            view = qs.get("view", ["code"])[0]
            r = self._file(rel, view)
            if not r.get("ok"):
                self._err(r.get("error") or "文件读取失败", 400)
                return
            self._ok(r)
            return
        if path == "/api/graph":
            kind = qs.get("kind", ["deps"])[0]
            rel = qs.get("path", [""])[0]
            sym = qs.get("symbol", [""])[0] or None
            self._ok(self._graph(kind, rel, sym))
            return
        if path == "/api/atoms":
            from atom_loader_ext import merged_registry
            self._ok(merged_registry(cfg.codeagent_root()))
            return
        if path == "/api/fs/roots":
            # 路径边界: 文件/目录选择器允许浏览的根白名单(默认=本机存在的盘符根 +
            # lab_config.json 里显式配置的 path_allowlist), 供前端目录选择器定位用。
            self._ok({"roots": _fs_roots(cfg)})
            return
        if path == "/api/atoms/selfcheck":
            from atom_extension import self_check
            self._ok(self_check(cfg))
            return
        if path == "/api/pipelines":
            self._ok({"runs": get_app().db.list_pipelines(30)})
            return
        if re.fullmatch(r"/api/pipeline/[^/]+", path):
            rid = path.rsplit("/", 1)[1]
            import pipeline as pl
            state = pl.get_run(rid)
            if state is None:
                self._err(f"管道运行不存在: {rid}", 404)
                return
            self._ok(state)
            return
        if path == "/api/debug/history":
            from debug_history import find_hits
            q = qs.get("q", [""])[0]
            db = get_app().db
            rows = db.list_debug(q, 50)
            self._ok({"hits": rows, "count": len(rows)})
            return
        if re.fullmatch(r"/api/debug/[^/]+", path):
            rid = path.rsplit("/", 1)[1]
            import debug_orchestrator as do
            state = do.get_state(rid)
            if state is None:
                self._err(f"调试运行不存在: {rid}", 404)
                return
            self._ok(state)
            return
        if re.fullmatch(r"/api/report/[^/]+\.(html|md)", path):
            rid, ext = path.rsplit("/", 1)[1].rsplit(".", 1)
            from report_gen import load_export
            content = load_export(rid, ext)
            if content is None:
                # 重启后兜底：按文件名扫描导出目录
                content = _load_export_disk(cfg, rid, ext)
            if content is None:
                self._err(f"报告导出不存在: {rid}.{ext}", 404)
                return
            self._send(200, content,
                       "text/html; charset=utf-8" if ext == "html"
                       else "text/markdown; charset=utf-8")
            return
        if re.fullmatch(r"/api/report/[^/]+", path):
            rid = path.rsplit("/", 1)[1]
            import report_gen as rg
            state = rg.get_report(rid)
            if state is None:
                self._err(f"报告不存在: {rid}", 404)
                return
            self._ok(state)
            return
        if path == "/api/reports":
            self._ok({"reports": get_app().db.list_reports(50)})
            return
        if path == "/api/models":
            from model_config import public
            self._ok(public())
            return
        if path == "/api/events":
            since = int(qs.get("since", ["0"])[0] or 0)
            evs = get_bus().since(since)
            last = evs[-1]["seq"] if evs else get_bus().last_seq()
            self._ok({"events": evs, "last_seq": last, "count": len(evs)})
            return
        if path == "/api/editlog":
            self._ok({"edits": get_app().db.list_edit_log(100)})
            return
        self._err("404 未找到", 404)

    # ── POST ─────────────────────────────────
    def do_POST(self):
        ok, err, status = self._guard()
        if not ok:
            self._json({"ok": False, "error": err}, status)
            return
        body = _parse_body(self)
        if body.get("_oversize"):
            try:
                self.rfile.read(min(int(body.get("_len", 0)), 4 * 1024 * 1024))
            except Exception:  # noqa: BLE001
                pass
            self._err(f"请求体过大(上限 {_MAX_BODY} 字节)", 413)
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            self._route_post(body, path)
        except Exception as e:  # noqa: BLE001
            self.bus_emit("api.error", {"method": "POST", "path": path,
                                        "error": f"{type(e).__name__}: {e}"})
            self._err(f"内部错误: {type(e).__name__}: {e}", 500)

    def _route_post(self, body, path):
        cfg = get_app().cfg
        if path == "/api/config/target":
            ok, real = cfg.set_target_root(body.get("path"))
            if not ok:
                self._err(real, 400)
                return
            self.bus_emit("config.target_changed", {"target_root": real})
            self._ok({"target_root": real})
            return
        if path == "/api/fs/locate":
            # 文件/目录选择器定位: 浏览器原生 <input type=file> 不暴露绝对路径(沙箱),
            # 只给 basename/目录名; 这里在后端边界内把它解析回真实路径。
            #  - mode="file": 在 当前目标仓库(target_root)内 递归查同名文件 → 返回相对路径
            #  - mode="dir" : 在 路径白名单根(盘符根+path_allowlist)内 有界查同名目录 → 返回绝对路径
            r = _locate(body, cfg)
            if not r.get("ok"):
                self._err(r.get("error"), 400)
                return
            self._ok(r)
            return
        if path == "/api/file/save":
            rel = body.get("path")
            content = body.get("content")
            import file_viz
            result = file_viz.save_file(cfg.target_root(), rel, content,
                                        db=get_app().db, events=get_bus())
            if not result.get("ok"):
                # 内容级拒绝（如 py_compile 语法校验不过）→ 200 + 顶层 ok=False，
                # 前端内联显示编译错误；路径/参数级非法 → 4xx。
                if "compile_ok" in result:
                    self._json(result, 200)
                    return
                self._err(result.get("error") or "保存失败", 400)
                return
            self._ok(result)
            return
        if path == "/api/atoms/scaffold":
            from atom_extension import scaffold
            r = scaffold(body.get("name"), body.get("domain"),
                         body.get("capability"), body.get("inputs"),
                         body.get("description"), cfg)
            if not r.get("ok"):
                self._err(r.get("error"), 400)
                return
            self.bus_emit("atom.scaffolded", {"name": r["name"]})
            self._ok(r)
            return
        if path == "/api/atoms/register":
            from atom_extension import register
            r = register(body.get("name"), cfg, events=get_bus())
            if not r.get("ok"):
                self._err("; ".join(r.get("errors") or [r.get("error", "")]), 400)
                return
            self._ok(r)
            return
        if path == "/api/atoms/unregister":
            from atom_extension import unregister
            r = unregister(body.get("name"), cfg, events=get_bus())
            if not r.get("ok"):
                self._err(r.get("error"), 400)
                return
            self._ok(r)
            return
        if path == "/api/atoms/enabled":
            # 扩展启停开关（P2-6）：写入注册表 enabled 映射，事件驱动即时过滤调色板
            from atom_extension import set_enabled
            r = set_enabled(body.get("name"), bool(body.get("enabled")),
                            cfg, events=get_bus())
            if not r.get("ok"):
                self._err(r.get("error") or "操作失败", 400)
                return
            self._ok(r)
            return
        if path == "/api/atoms/sync-core":
            from atom_extension import sync_core
            r = sync_core(body.get("name"), bool(body.get("confirm")), cfg,
                          events=get_bus())
            if not r.get("ok"):
                self._err(r.get("error"), 400)
                return
            self._ok(r)
            return
        if path == "/api/pipeline/run":
            import pipeline as pl
            r = pl.run_pipeline(body.get("name") or "未命名管道", body.get("graph") or {},
                                db=get_app().db)
            if not r.get("ok"):
                self._err(r.get("error") or "; ".join(r.get("errors") or []), 400)
                return
            self._ok(r)
            return
        if path == "/api/debug":
            import debug_orchestrator as do
            r = do.start_debug(body.get("failure") or "", body.get("file") or "",
                               body.get("test_cmd") or "", body.get("model"))
            if not r.get("ok"):
                self._err(r.get("error", "调试启动失败"), 400)
                return
            self._ok(r)
            return
        if path == "/api/debug/retry":
            did = body.get("id")
            row = get_app().db.get_debug(did)
            if not row:
                self._err(f"历史记录不存在: {did}", 404)
                return
            import debug_orchestrator as do
            r = do.start_debug(row["failure"] or "", row["file"] or "",
                               row["test_cmd"] or "", None)
            self._ok(r)
            return
        if path == "/api/report":
            from report_gen import start_report
            r = start_report(body.get("path") or ".")
            if not r.get("ok"):
                self._err(r.get("error", "报告启动失败"), 400)
                return
            self._ok(r)
            return
        if path == "/api/models":
            from model_config import save as mc_save
            r = mc_save(body.get("providers"), body.get("chain"), db=get_app().db)
            if not r.get("ok"):
                self._err(r.get("error", "配置保存失败"), 400)
                return
            self._ok(r)
            return
        if path == "/api/models/test":
            from model_config import test_provider
            self._ok(test_provider(body.get("provider_id")))
            return
        if path == "/api/chat":
            from chat_router import handle
            r = handle(body.get("message") or "", body.get("history") or [],
                       db=get_app().db, events=get_bus())
            self._ok(r)
            return
        if path == "/api/test":
            from atom_runner import run_capability
            from atom_loader_ext import merged_registry
            rel = body.get("path") or ""
            real, rel2, terr = cfg.resolve_target(rel)
            if terr:
                self._err(terr, 400)
                return
            env = run_capability("code-test", "test.run", {"path": real}, timeout=240,
                                 registry=merged_registry(cfg.codeagent_root()),
                                 codeagent_root=cfg.codeagent_root())
            self._ok(env)
            return
        self._err("404 未找到", 404)

    # ── 内部动作 ─────────────────────────────
    def bus_emit(self, etype, payload):
        get_bus().emit(etype, payload)

    def _tree(self, rel, recursive, badges):
        from file_viz import tree
        cfg = get_app().cfg
        return tree(cfg.target_root(), rel or "", recursive=recursive,
                    badges=badges, codeagent_root=cfg.codeagent_root())

    def _file(self, rel, view):
        from file_viz import read_file
        cfg = get_app().cfg
        real, rel2, terr = cfg.resolve_target(rel)
        if terr:
            return {"ok": False, "error": terr}
        r = read_file(cfg.target_root(), rel2, view,
                      codeagent_root=cfg.codeagent_root())
        r["rel"] = rel2
        return r

    def _graph(self, kind, rel, sym):
        import graph_viz as gv
        cfg = get_app().cfg
        if kind == "impact":
            return gv.impact_graph(cfg.target_root(), rel, sym,
                                   codeagent_root=cfg.codeagent_root())
        if kind == "layers":
            return gv.layers_graph(cfg.target_root(), rel,
                                   codeagent_root=cfg.codeagent_root())
        return gv.deps_graph(cfg.target_root(), rel or None)

    def _static(self, rel, ctype):
        fdir = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(fdir, rel)
        if not os.path.isfile(p):
            self._err("静态文件缺失", 500)
            return
        with open(p, encoding="utf-8") as f:
            self._send(200, f.read(), ctype)


def _load_export_disk(cfg, rid, ext):
    out_dir = cfg.get("report_dir")
    if not os.path.isdir(out_dir):
        return None
    p = os.path.join(out_dir, f"{rid}.{ext}")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


# ═══════════ 文件/目录选择器后端定位（路径边界核心）═══════════
# 浏览器原生 <input type=file[/webkitdirectory]> 受沙箱约束，不暴露绝对路径，
# 只给 basename（文件）或 目录名/相对结构（目录）。此处把选择结果在后端边界内
# 解析回真实路径，并把"可浏览范围"收紧到白名单根 + 当前目标仓库内，杜绝越权。
_FS_MAX_DEPTH = 3          # 目录查找最大递归深度（防在盘符根里无限扫描）
_FS_MAX_FNAME = 200        # 名称长度上限（输入校验）
_FS_MAX_CANDIDATES = 6     # 同名目录候选上限（歧义时列出供用户确认，不静默乱切）
_FS_SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "programdata", "$windows.~bt", "$windows.~ws",
    "recovery", "intel", "perflogs", "node_modules", "venv", ".venv", "env",
    "site-packages", "__pycache__", ".git", ".svn", ".hg", ".idea", ".vscode",
    "build", "dist", "target", "coverage", ".pytest_cache",
}


def _fs_roots(cfg):
    """路径边界白名单根：本机存在的盘符根 + lab_config 显式 path_allowlist。"""
    roots = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        d = letter + ":\\"
        if os.path.isdir(d):
            roots.append(d)
    allow = cfg.get("path_allowlist") or []
    if isinstance(allow, (list, tuple)):
        for r in allow:
            rr = os.path.realpath(os.path.expanduser(str(r)))
            if os.path.isdir(rr):
                roots.append(rr)
    seen, out = set(), []
    for r in roots:
        rr = os.path.realpath(r)
        if rr not in seen:
            seen.add(rr)
            out.append(rr)
    return out


def _find_dir(name, roots, max_depth=_FS_MAX_DEPTH, max_cands=_FS_MAX_CANDIDATES):
    """在路径白名单根内有界查找同名目录。返回 (匹配列表, 是否截断)。"""
    hits, truncated = [], False
    seen = set()
    for root in roots:
        base = os.path.realpath(root)
        if base in seen:
            continue
        seen.add(base)
        stack = [(base, 0)]
        while stack and len(hits) < max_cands:
            cur, depth = stack.pop()
            if depth > max_depth:
                continue
            try:
                with os.scandir(cur) as it:
                    entries = list(it)
            except (PermissionError, OSError):
                continue
            sub = []
            for e in entries:
                try:
                    is_dir = e.is_dir()
                except OSError:
                    continue
                if not is_dir:
                    continue
                low = e.name.lower()
                if e.name.startswith(".") or low in _FS_SKIP_DIRS:
                    continue
                if e.name == name:
                    hits.append(os.path.realpath(e.path))
                    if len(hits) >= max_cands:
                        truncated = True
                        break
                if depth < max_depth:
                    sub.append((e.path, depth + 1))
            stack.extend(reversed(sub))
    return hits, truncated


def _find_file_rel(name, target, max_depth=10):
    """在当前目标仓库(target_root)内有界递归查同名文件 → 相对路径（正斜杠）。"""
    target = os.path.realpath(target)
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d.lower() not in _FS_SKIP_DIRS]
        depth = dirpath[len(target):].count(os.sep)
        if depth > max_depth:
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn == name:
                rel = os.path.relpath(os.path.join(dirpath, fn), target).replace(os.sep, "/")
                return rel
    return None


def _locate(body, cfg):
    """POST /api/fs/locate 处理：文件(目标内)/目录(白名单根内) 有界定位。"""
    if not isinstance(body, dict):
        return {"ok": False, "error": "请求体须为 JSON 对象"}
    mode = body.get("mode")
    name = body.get("name")
    if mode not in ("file", "dir"):
        return {"ok": False, "error": "mode 须为 file 或 dir"}
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "error": "缺少名称"}
    name = name.strip()
    if len(name) > _FS_MAX_FNAME:
        return {"ok": False, "error": f"名称过长(上限 {_FS_MAX_FNAME})"}
    # 输入校验：名称禁止携带路径分隔符/.. / 绝对路径特征，杜绝路径注入式越权
    if "/" in name or "\\" in name or name in (".", "..") or ".." in name.split("/"):
        return {"ok": False, "error": "名称含非法路径成分，仅允许纯名称"}
    if mode == "file":
        rel = _find_file_rel(name, cfg.target_root())
        if not rel:
            return {"ok": False, "error": f"目标仓库内未找到同名文件: {name}"}
        return {"ok": True, "mode": "file", "name": name, "rel": rel,
                "abs": os.path.join(cfg.target_root(), rel)}
    hits, truncated = _find_dir(name, _fs_roots(cfg))
    if not hits:
        return {"ok": False, "error": f"路径白名单内未找到同名目录: {name}"}
    if len(hits) == 1:
        return {"ok": True, "mode": "dir", "name": name, "abs": hits[0], "candidates": hits}
    return {"ok": True, "mode": "dir", "name": name, "abs": None, "ambiguous": True,
            "candidates": hits, "error": "发现多个同名目录，请选择其一（不自动切换）"}


def serve(cfg=None, db=None, host=None, port=None, token=None):
    """启动 HTTP 服务（测试可传临时配置/DB）。返回 (server, port)。"""
    cfg = cfg or get_config()
    if token is not None:
        cfg.values["token"] = token
    app = LabApp(cfg, db=db)
    set_app(app)
    h = host or cfg.get("host") or "127.0.0.1"
    # 端口解析：显式 0 = 系统分配空闲端口（测试隔离用）。不能用 `p = port or 8087`
    # —— 那会把 0 当 falsy 恒落回 8087，导致测试绑定到已被占用的固定端口，
    # 请求打到错误实例(测试假失败)。
    if port is not None:
        p = port
    elif cfg.get("port") is not None:
        p = int(cfg.get("port"))
    else:
        p = 8087
    server = ThreadingHTTPServer((h, p), Handler)
    actual_port = server.server_address[1]
    return server, actual_port