#!/usr/bin/env python3
"""localized 原子壳（open_source:true）——P1 本地化（数据不出厂 + 模型降级链本地主）。

针对用户「本地化」定位：审查代码是否数据不出厂、把模型调用路由到本地优先。
纯 stdlib 数据不出厂。

能力：
  local.audit  — 数据不出厂审计：AST 检网络/云端调用信号(requests/urllib/socket/云SDK/http URL)
  local.chain  — 模型降级链本地主：按 local→(可选)云端 顺序，本地可用则绝不外发；本地全挂且
                 local_only=True 时降级返回「本地不可用」而非偷偷上云
  local.route  — 本地路由审查：读 LLM 配置，检查是否本地模型优先
"""

import ast
import json
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

# 网络/云端调用信号
_NET_IMPORTS = ("requests", "urllib", "httpx", "aiohttp", "http.client",
                "socket", "websocket", "grpc", "smtplib", "ftplib",
                "boto3", "azure", "google.cloud", "openai", "anthropic",
                "zhipuai", "dashscope", "qianfan")
_NET_MODULES = ("requests", "urllib", "httpx", "aiohttp", "socket", "websocket", "openai")

_DEF_LOCAL = [
    ("Ollama", "127.0.0.1", 11434),
    ("ornith", "127.0.0.1", 8765),
]


class LocalizedAgent(AtomicAgent):
    name = "localized"
    version = "0.1.0"
    domain = "locality"
    description = ("本地化原子（P1）: 数据不出厂审计(网络/云端调用信号) + 模型降级链本地主 "
                   "(本地可用绝不外发) + 本地路由审查。纯 stdlib 数据不出厂。")
    provides = ["local.audit", "local.chain", "local.route"]
    depends_on = []
    inputs = ["target", "code", "local_only", "candidates", "config_path", "timeout"]
    outputs = ["ok", "signals", "route", "local_up", "verdict", "fallback"]

    def _register_defaults(self):
        self.register("local.audit", self._audit)
        self.register("local.chain", self._chain)
        self.register("local.route", self._route)

    def _sources(self, target=None, code=None):
        if code is not None:
            return [("<inline>", code)]
        if target and os.path.isdir(target):
            out = []
            for r, _d, files in os.walk(target):
                for f in files:
                    if f.endswith(".py"):
                        try:
                            with open(os.path.join(r, f), encoding="utf-8") as fh:
                                out.append((os.path.join(r, f), fh.read()))
                        except (OSError, UnicodeDecodeError):
                            pass
            return out
        if target and os.path.isfile(target) and target.endswith(".py"):
            with open(target, encoding="utf-8") as fh:
                return [(target, fh.read())]
        return []

    def _audit(self, target=None, code=None):
        srcs = self._sources(target, code)
        if not srcs:
            return self._envelope(False, degraded=True, error="无 .py 可审查")
        signals = []
        for name, src in srcs:
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        top = a.name.split(".")[0]
                        if top in _NET_IMPORTS:
                            signals.append({"file": name, "signal": f"网络/云端依赖 import {top}",
                                            "lineno": node.lineno})
                elif isinstance(node, ast.ImportFrom):
                    top = (node.module or "").split(".")[0]
                    if top in _NET_IMPORTS:
                        signals.append({"file": name, "signal": f"网络/云端依赖 from {top}",
                                        "lineno": node.lineno})
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    v = node.value.lower()
                    if v.startswith(("http://", "https://", "ws://", "wss://")):
                        signals.append({"file": name, "signal": f"外发 URL {node.value[:50]}",
                                        "lineno": getattr(node, "lineno", 0)})
        return {"ok": not signals, "signals": signals, "count": len(signals),
                "data_not_leak": not signals,
                "verdict": "数据不出厂（无网络/云端调用）" if not signals
                else f"{len(signals)} 处外发信号，需评估数据泄露风险"}

    def _probe(self, host, port, timeout):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _chain(self, local_only=True, candidates=None, timeout=1.0):
        """模型降级链本地主：本地端点可用 → 走本地；不可用 → 降级(不上云)。"""
        timeout = float(timeout or 1.0)
        local_endpoints = candidates or _DEF_LOCAL
        route, up = [], []
        for name, host, port in local_endpoints:
            ok = self._probe(host, port, timeout)
            route.append({"name": name, "endpoint": f"{host}:{port}", "up": ok, "tier": "local"})
            if ok:
                up.append(name)
        if up:
            verdict = f"本地模型可用: {', '.join(up)} → 主用本地，数据不出厂"
            return {"ok": True, "route": route, "local_up": up,
                    "model": up[0], "fallback": None, "local_only": local_only,
                    "verdict": verdict}
        # 本地全挂
        if local_only:
            return {"ok": False, "degraded": True, "route": route, "local_up": [],
                    "model": None, "fallback": "本地全不可用且 local_only=True → 拒绝上云(数据不出厂)",
                    "local_only": True, "verdict": "本地模型不可用，数据不出厂拒绝云端外发"}
        return {"ok": True, "route": route, "local_up": [],
                "model": None, "fallback": "本地不可用 → 允许云端降级(非 local_only)",
                "local_only": False, "verdict": "本地不可用，已降级云端(允许外发)"}

    def _route(self, config_path=None):
        """本地路由审查：读 LLM 配置，检查本地模型是否优先。"""
        cfg = config_path or os.path.join(REPO_ROOT, "llm_config.json")
        if not os.path.isfile(cfg):
            # 尝试扫描项目内 llm/model 相关 json
            for r, _d, files in os.walk(REPO_ROOT):
                for f in files:
                    if f in ("llm_config.json", "model_config.json") and not any(
                            x in r for x in ("__pycache__", ".git")):
                        cfg = os.path.join(r, f)
                        break
        if not os.path.isfile(cfg):
            return {"ok": True, "config": None, "local_first": True,
                    "verdict": "无显式 LLM 配置 → 默认本地优先(数据不出厂)"}
        try:
            with open(cfg, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            return self._envelope(False, degraded=True, error=f"配置读取失败: {e}")
        providers = data.get("providers") or data.get("models") or {}
        order = data.get("order") or list(providers)
        local_first = not order or ("local" in str(order[0]).lower()
                                    or "ollama" in str(order[0]).lower()
                                    or "ornith" in str(order[0]).lower())
        return {"ok": True, "config": cfg, "order": order, "local_first": local_first,
                "verdict": "本地优先" if local_first else "配置将云端置于本地前，有数据出厂商风险"}


agent = LocalizedAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(LocalizedAgent(), run_args={
        "capability": {"default": "local.audit", "choices": list(LocalizedAgent.provides)},
        "target": {}, "code": {}, "local_only": {}, "config_path": {},
    }))
