#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""model_config.py — 模型设置配置（FR4，本地/云端，纯 stdlib，密钥不出私域）。

- Provider: 本地（Ollama/llama.cpp OpenAI 兼容端点）/ 云端（OpenAI 兼容 base_url）
- 连通测试：真实 urllib 探测 {base_url}/v1/models（不读配置就报绿）
- 脱敏：api_key 只存后端；GET 回显 前4+***+后2；POST 空串/含 "*" 保留旧值
- 路由降级：active 排序 = 降级链（本地挂了自动切云端，反之亦然，模型调用按链查找）
- chat：OpenAI 兼容 /v1/chat/completions（调试修复/对话 LLM 兜底共用）
配置实时生效（不重启）。落盘 lab_data/models.json + 每次保存快照入 models_snapshot 表。
"""
import json
import os
import threading
import urllib.request
import urllib.error

from lab_config import get_config
from events import emit

_lock = threading.Lock()
_config = None


def _load():
    global _config
    cfg = get_config()
    path = cfg.get("models_path")
    if _config is not None:
        return _config
    default = {"providers": [], "chain": []}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                default = loaded
        except Exception:  # noqa: BLE001
            default["load_error"] = "模型配置损坏，已回退空配置"
    for p in default.setdefault("providers", []):
        p.setdefault("type", "local")
        p.setdefault("models", [])
        p.setdefault("active", False)
        p.setdefault("api_key", "")
    default["providers"] = [p for p in default["providers"] if p.get("id")]
    default.setdefault("chain", [])
    # 默认配置即最佳可用：未配置任何 provider 时，内置一个本地 Ollama 默认 provider
    # （本地优先、数据不出私域），使对话/调试的模型兜底开箱即用。
    if not default["providers"]:
        default["providers"] = [{
            "id": "local-ollama", "name": "本地 Ollama",
            "type": "local", "base_url": "http://127.0.0.1:11434",
            "models": ["qwen2.5:7b"], "active": True, "api_key": "",
        }]
        default["chain"] = ["local-ollama"]
    with _lock:
        _config = default
    return _config


def _save():
    global _config
    cfg = get_config()
    path = cfg.get("models_path")
    c = _load()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    try:
        from history_db import get_db
        get_db().add_models_snapshot(c)
    except Exception:  # noqa: BLE001
        pass


def reset_for_tests(config=None):
    """测试用：重置内存配置。"""
    global _config
    with _lock:
        _config = config


# ── 脱敏 ─────────────────────────────────────
def mask_key(key):
    if not key:
        return ""
    if len(key) <= 6:
        return "已配置"
    return key[:4] + "***" + key[-2:]


def public():
    """脱敏后的配置视图（GET /api/models）。"""
    c = _load()
    providers = []
    for p in c.get("providers", []):
        providers.append({
            "id": p.get("id"), "name": p.get("name") or p.get("id"),
            "type": p.get("type"), "base_url": p.get("base_url"),
            "api_key_status": mask_key(p.get("api_key") or ""),
            "has_key": bool(p.get("api_key")),
            "models": list(p.get("models", [])),
            "active": bool(p.get("active")),
        })
    return {"providers": providers, "chain": list(c.get("chain", [])),
            "load_error": c.get("load_error")}


def save(providers, chain, db=None):
    """脱敏保存：api_key 空串/含 '*' → 保留旧值；active 置位；快照入库。"""
    _load()  # 先确保 _config 已初始化（_lock 非重入；首次加载若在持锁内会自死锁）
    with _lock:
        old = _load()
        old_by_id = {p.get("id"): p for p in old.get("providers", [])}
        new_providers = []
        for p in providers or []:
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            old_p = old_by_id.get(pid, {})
            key = str(p.get("api_key") or "").strip()
            if key == "" or "*" in key:
                key = old_p.get("api_key", "")
            new_providers.append({
                "id": pid, "name": str(p.get("name") or pid),
                "type": str(p.get("type") or old_p.get("type") or "local"),
                "base_url": str(p.get("base_url") or old_p.get("base_url") or "").rstrip("/"),
                "api_key": key,
                "models": list(p.get("models") or old_p.get("models") or []),
                "active": bool(p.get("active", old_p.get("active", False))),
            })
        _config["providers"] = new_providers
        _config["chain"] = [str(c) for c in (chain or [])]
        _config.pop("load_error", None)
        _save()
        emit("models.saved", {"providers": [p["id"] for p in new_providers],
                              "chain": _config["chain"]})
    return {"ok": True, "saved": True}


def test_provider(provider_id, timeout=8):
    """真实连通探测：GET {base_url}/v1/models。返回 {ok, detail, models[]}。"""
    p = find_provider(provider_id)
    if not p:
        return {"ok": False, "error": "provider 不存在"}
    base = (p.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "缺 base_url"}
    url = base + "/v1/models"
    req = urllib.request.Request(url)
    if p.get("api_key"):
        req.add_header("Authorization", "Bearer " + p["api_key"])
    emit("models.testing", {"provider_id": provider_id, "url": url})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(200_000)
            data = json.loads(body.decode("utf-8", "replace"))
        models = []
        for m in (data.get("data") or []):
            models.append(m.get("id") or m.get("model") or m.get("name") or str(m)[:40])
        models = models[:50]
        emit("models.test_ok", {"provider_id": provider_id, "count": len(models)})
        return {"ok": True, "detail": f"连通成功，发现 {len(models)} 个模型",
                "models": models}
    except urllib.error.HTTPError as e:
        detail = f"HTTP {e.code}: {e.reason}"
        emit("models.test_failed", {"provider_id": provider_id, "detail": detail})
        return {"ok": False, "error": detail}
    except Exception as e:  # noqa: BLE001
        detail = f"{type(e).__name__}: {e}"
        emit("models.test_failed", {"provider_id": provider_id, "detail": detail})
        return {"ok": False, "error": detail}


def find_provider(provider_id):
    for p in _load().get("providers", []):
        if p.get("id") == provider_id:
            return p
    return None


def active_chain():
    """降级链：active 的 provider 顺序（chain 覆盖，空则按 active 声明序）。"""
    c = _load()
    by_id = {p["id"]: p for p in c.get("providers", [])}
    chain = []
    for pid in c.get("chain", []):
        if pid in by_id:
            chain.append(by_id[pid])
    for p in c.get("providers", []):
        if p.get("active") and p not in chain:
            chain.append(p)
    return chain


def models_active():
    return bool(active_chain())


def chat_completion(model=None, messages=None, timeout=60):
    """OpenAI 兼容 chat 调用（调试修复/对话意图兜底共用）。按链降级。"""
    chain = active_chain()
    if not chain:
        return {"ok": False, "error": "无可用模型（请在模型配置添加并激活 provider）"}
    msgs = messages or [{"role": "user", "content": ""}]
    errs = []
    for p in chain:
        if model and p.get("id") != model:
            continue
        base = (p.get("base_url") or "").rstrip("/")
        if not base:
            errs.append(f"{p.get('id')}: 缺 base_url")
            continue
        body = json.dumps({"model": (p.get("models") or ["default"])[0],
                           "messages": msgs, "temperature": 0.2,
                           "max_tokens": 2048}).encode("utf-8")
        req = urllib.request.Request(base + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        if p.get("api_key"):
            req.add_header("Authorization", "Bearer " + p["api_key"])
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read(400_000).decode("utf-8", "replace"))
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            if content is None:
                return {"ok": False, "error": "模型无回复内容"}
            # 修复 P1-8：model 恒空串（body[:0] 笔误）→ 返回实际模型名
            return {"ok": True, "content": str(content), "provider": p["id"],
                    "model": (p.get("models") or ["default"])[0]}
        except Exception as e:  # noqa: BLE001
            errs.append(f"{p.get('id')}: {type(e).__name__}: {e}")
    return {"ok": False, "error": "降级链全部失败: " + "; ".join(errs[-3:])}