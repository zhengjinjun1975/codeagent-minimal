"""三级分流核心：规则 → 缓存 → 模型。函数返回普通 dict，不抛异常。"""
import json
import os
import re
import time
import hashlib

DEFAULT_RULES = [
    {"intent": "query_time",
     "keywords": ["几点了", "什么时间", "现在时间", "日期", "星期几", "今天几号"],
     "handler": "builtin:time", "confidence": 0.95},
    {"intent": "query_knowledge",
     "keywords": ["什么是", "怎么", "如何", "为什么", "解释", "说明", "介绍"],
     "handler": "llm", "confidence": 0.60},
]

MAX_ENTRIES = 1000


def _default_store_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")


def _cache_path(store_dir):
    return os.path.join(store_dir or _default_store_dir(), "intent_cache.json")


def _load_cache(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def _make_key(text):
    return hashlib.md5(str(text).strip().lower().encode("utf-8")).hexdigest()


def rule_match(text, rules=None):
    """从上往下扫规则，任一 keyword 子串命中即返回。"""
    try:
        rules = DEFAULT_RULES if rules is None else rules
        text = "" if text is None else str(text)
        for rule in rules:
            for kw in rule.get("keywords", []):
                if kw and kw in text:
                    return {
                        "matched": True,
                        "intent": rule.get("intent", ""),
                        "handler": rule.get("handler", "llm"),
                        "confidence": float(rule.get("confidence", 0.0)),
                    }
    except Exception:
        pass
    return {"matched": False, "intent": "", "handler": "llm", "confidence": 0.0}


def cache_lookup(text, ttl_sec=604800, store_dir=None):
    """命中则 hits+1 并写回；未命中/过期/损坏均视为未命中。"""
    key = _make_key(text)
    try:
        path = _cache_path(store_dir)
        data = _load_cache(path)
        entry = data.get(key)
        if isinstance(entry, dict):
            ts = entry.get("ts", 0)
            if time.time() - ts <= ttl_sec:
                entry["hits"] = int(entry.get("hits", 0)) + 1
                data[key] = entry
                _save_cache(path, data)
                return {"hit": True, "intent": entry.get("intent", ""), "key": key}
    except Exception:
        pass
    return {"hit": False, "intent": "", "key": key}


def cache_put(text, intent, store_dir=None):
    """写回缓存，超容量删 ts 最旧的。"""
    key = _make_key(text)
    try:
        path = _cache_path(store_dir)
        data = _load_cache(path)
        data[key] = {"intent": intent, "ts": time.time(), "hits": 0}
        if len(data) > MAX_ENTRIES:
            oldest = sorted(data.items(), key=lambda kv: kv[1].get("ts", 0))
            for k, _ in oldest[: len(data) - MAX_ENTRIES]:
                data.pop(k, None)
        _save_cache(path, data)
        return {"key": key, "size": len(data)}
    except Exception:
        return {"key": key, "size": 0}


def cascade(text, rules=None, ttl_sec=604800, store_dir=None):
    """规则 → 缓存 → 模型，本函数不调模型。"""
    try:
        m = rule_match(text, rules)
        if m.get("matched"):
            return {"layer": "rule", "intent": m.get("intent", ""), "shortcut": True}
        c = cache_lookup(text, ttl_sec, store_dir)
        if c.get("hit"):
            return {"layer": "cache", "intent": c.get("intent", ""), "shortcut": True}
    except Exception:
        pass
    return {"layer": "llm", "intent": "", "shortcut": False}