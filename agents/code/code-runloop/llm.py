#!/usr/bin/env python3
"""llm.py — code-runloop 自包含 LLM 生成通道(纯 stdlib, 无第三方依赖)。

按 config/model_config.json 的 generate 段(openai 兼容 / ollama)做一次对话补全。
不 import 任何跨库活目录 / 闭源编排; 纯 urllib。设计 §8 要求的最小 vendor。

返回统一 dict: {"content","finish_reason","error","usage"}。
"""
import json
import os
import urllib.request

DEFAULT_CONFIG_NAMES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
                 "config", "model_config.json"),  # codeagent-minimal/config/model_config.json
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "model_config.json"),
]


def _env_file_hits():
    """同 code_agent_engine._read_env: 从 hermes .env 兜底读 key。"""
    hits = []
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, ".codeagent", ".env"),
                 os.path.join(home, "AppData", "Local", "hermes", ".env")):
        if os.path.isfile(cand):
            hits.append(cand)
    return hits


def resolve_key(cfg):
    """api_key 字段可指向环境变量名(如 DEEPSEEK_API_KEY) → 取 os.environ/.env 兜底。"""
    k = (cfg or {}).get("api_key", "")
    if not k:
        return ""
    if k in os.environ:
        return os.environ[k]
    for p in _env_file_hits():
        try:
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line.startswith(k + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:  # fail-open: resolve_key 里出错就跳过该条继续扫
            continue
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


# 宪法例外: 重试循环与请求构造合在一处，「只对可恢复错重试」的口径才看得见；拆开会让退避与判定分家
def _post_json(url, payload, headers, timeout=None):
    """纯 stdlib POST; 绕开系统代理(Clash 常未连节点), 与 code_agent_engine 一致。

    容错口径与 llm-router / engine_llm 对齐：**只对可恢复错重试**
    （超时 / 连接错 / 408 / 425 / 429 / 5xx），业务 4xx 直接返回、不重试。
    次数与退避走环境变量：CODEAGENT_HTTP_RETRIES（默认 2）/ CODEAGENT_HTTP_BACKOFF（默认 3 秒）。
    读超时默认 180 秒（大文件生成常超过 120 秒），CODEAGENT_LLM_TIMEOUT 可覆盖。

    历史：2026-10-17 前这里是单发零重试、timeout 硬编码 120，
    一次 read timeout 就让整块报废（L1d / L1d2 两块连续死在同一句）。
    """
    import os as _os
    import time as _time
    import urllib.error  # noqa: F401  (仅本函数用)

    if timeout is None:
        try:
            timeout = int(_os.environ.get("CODEAGENT_LLM_TIMEOUT", "180"))
        except ValueError:
            timeout = 180
    try:
        retries = max(0, int(_os.environ.get("CODEAGENT_HTTP_RETRIES", "2")))
    except ValueError:
        retries = 2
    try:
        backoff = max(0.0, float(_os.environ.get("CODEAGENT_HTTP_BACKOFF", "3")))
    except ValueError:
        backoff = 3.0

    # 显式空代理表 → 直连, 不复用系统代理
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    data = json.dumps(payload).encode("utf-8")

    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw)
        except urllib.error.HTTPError as e:  # noqa
            code = getattr(e, "code", 0) or 0
            retryable = code in (408, 425, 429) or 500 <= code < 600
            last = "HTTPError %s: %s" % (code, e)
            if not retryable or attempt >= retries:
                return {"error": last}
        except Exception as e:  # noqa
            # 超时 / 连接重置 / DNS 抖动都属可恢复
            last = "%s: %s" % (type(e).__name__, e)
            if attempt >= retries:
                return {"error": last}
        _time.sleep(backoff * (attempt + 1))
    return {"error": last or "未知错误"}


def _call_openai(gen, messages, temp, max_tokens):
    url = gen.get("base_url") or "https://api.deepseek.com/v1/chat/completions"
    model = gen.get("model", "deepseek-v4-flash")
    key = resolve_key(gen)
    if not key:
        return {"error": "generate API_KEY 未配置(需 env 或 ~/.codeagent/.env)"}
    payload = {"model": model, "messages": messages,
               "temperature": temp, "max_tokens": max_tokens,
               # 推理型模型（deepseek-v4-flash）在复杂任务上会一路思考到把预算耗光、
               # 正文只回空串或一行；代码生成必须显式关掉思考（实测关掉后 finish_reason=stop、正文完整）。
               "thinking": {"type": "disabled"}}
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
