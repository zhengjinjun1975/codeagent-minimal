#!/usr/bin/env python3
"""llm.py — code-runloop 自包含 LLM 生成通道(纯 stdlib, 无第三方依赖)。

按 config/model_config.json 的 generate 段(openai 兼容 / ollama)做一次对话补全。
不 import 任何跨库活目录 / 闭源编排; 纯 urllib。设计 §8 要求的最小 vendor。

返回统一 dict: {"content","finish_reason","error","usage"}。
"""
import json
import os
import sys
import urllib.request

DEFAULT_CONFIG_NAMES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
                 "config", "model_config.json"),  # 仓库 config/model_config.json
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "model_config.json"),
]


def resolve_key(cfg):
    """api_key 字段可指向环境变量名(如 DEEPSEEK_API_KEY) → 取 os.environ; 未设置返回空。"""
    k = (cfg or {}).get("api_key", "")
    if not k:
        return ""
    if k in os.environ:
        return os.environ[k]
    return ""


def load_config(config_path=None):
    """读取 model_config.json 的 generate 段。缺省按若干候选路径定位。"""
    paths = []
    if config_path:
        paths.append(config_path)
    paths += DEFAULT_CONFIG_NAMES
    for p in paths:
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    cfg = json.load(f)
                gen = cfg.get("generate", {}) if isinstance(cfg, dict) else {}
                if gen:
                    return {"ok": True, "path": p, "config": gen}
            except Exception as e:  # noqa
                return {"ok": False, "error": f"读取 config 失败 {p}: {type(e).__name__}: {e}"}
    return {"ok": False,
            "error": "找不到 model_config.json(generate 段); 请检查 config 路径或 --model-config"}


def _post_json(url, payload, headers, timeout=120):
    """纯 stdlib POST; 绕开系统代理直连(不读环境代理), 与 code_agent_engine 一致。"""
    # 显式空代理表 → 直连, 不复用系统代理
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)
    except Exception as e:  # noqa
        return {"error": f"{type(e).__name__}: {e}"}


def _call_openai(gen, messages, temp, max_tokens):
    url = gen.get("base_url") or "https://api.deepseek.com/v1/chat/completions"
    model = gen.get("model", "deepseek-v4-flash")
    key = resolve_key(gen)
    if not key:
        return {"error": "generate API_KEY 未配置(需 env 设置对应 API_KEY)"}
    payload = {"model": model, "messages": messages,
               "temperature": temp, "max_tokens": max_tokens}
    # DeepSeek reasoner: 显式关闭 thinking(兼容 generate 配置带 type 的场景)
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    body = _post_json(url, payload, headers)
    if "error" in body and isinstance(body, dict) and "choices" not in body:
        return {"error": "HTTP 响应错误: " + str(body.get("error"))}
    try:
        msg = body["choices"][0]["message"]
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        return {"content": content,
                "finish_reason": body["choices"][0].get("finish_reason"),
                "error": None,
                "usage": body.get("usage", {})}
    except Exception as e:  # noqa
        return {"error": f"解析响应失败 {type(e).__name__}: {e} (raw={str(body)[:200]})"}


def _call_ollama(gen, messages, temp, max_tokens):
    url = gen.get("base_url") or "http://127.0.0.1:11434/api/chat"
    model = gen.get("model", "ornith:latest")
    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"temperature": temp, "num_predict": max_tokens}}
    headers = {"Content-Type": "application/json"}
    body = _post_json(url, payload, headers)
    if isinstance(body, dict) and "message" in body:
        return {"content": body["message"].get("content", ""),
                "finish_reason": None, "error": None, "usage": {}}
    if isinstance(body, dict) and "error" in body:
        return {"error": str(body["error"])}
    return {"error": "Ollama 响应异常: " + str(body)[:200]}


def generate(messages, temp=0.3, max_tokens=32000, config_path=None, gen=None):
    """一次对话补全。messages: list[{role, content}]。返回统一 dict。"""
    if gen is None:
        res = load_config(config_path)
        if not res["ok"]:
            return {"content": "", "error": res["error"],
                    "finish_reason": None, "usage": {}}
        gen = res["config"]
    gtype = str(gen.get("type", "openai")).lower()
    if gtype == "ollama":
        return _call_ollama(gen, messages, temp, max_tokens)
    return _call_openai(gen, messages, temp, max_tokens)


if __name__ == "__main__":  # 自测: 不连网, 只验证 config 定位与 key 解析
    res = load_config()
    print("config:", "OK " + res["path"] if res["ok"] else "FAIL " + res["error"])
    if res["ok"]:
        c = res["config"]
        print("  type=", c.get("type"), "model=", c.get("model"))
        key = resolve_key(c)
        print("  api_key 字段=", c.get("api_key"), "→ 已解析:", "yes" if key else "no")
