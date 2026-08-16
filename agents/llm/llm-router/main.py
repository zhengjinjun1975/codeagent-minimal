#!/usr/bin/env python3
"""llm-router 原子壳（open_source:true）——多模型 provider 注册表升级。

对齐 OpenCode P0-2「多模型 provider 注册表」（AI SDK + Models.dev 思路）：
把「GLM + ornith 双后端」升级为**可配置 provider 注册表**（本地 ornith/Ollama +
云端 DeepSeek/GLM 等），模型**热扩展**（改 config/env 即加新模型，零改代码），
per-agent 可声明走哪个 provider / 哪个 model。local_only 默认 True（数据不出厂）。

能力：
  llm.list_models — 列出 provider 注册表 + 模型目录（热扩展，local/cloud 标注）
  llm.generate   — 云端生成（GLM/DeepSeek...），按 provider 路由。local_only=True 禁止出网
  llm.review     — 本地 ornith/Ollama 审查，数据不出厂
  llm.registry   — 返回完整 provider 注册表（含 base_url/模型/local_or_cloud）

敏感数据红线：`local_only` 开关透传，true 时 generate 立即返回 degraded 错误，
绝不发任何网络请求，保证甲方数据不出厂。
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

# 默认 provider 注册表候选路径（复用 model_config.json，改配置即热扩展，零改代码）。
# 不再硬编码闭源/私有绝对路径 E:/...，优先 env 覆盖，其次仓库内配置。
_CONFIG_CANDIDATES = [
    os.environ.get("CODEAGENT_MODEL_CONFIG", ""),        # env 显式指定（可移植）
    os.path.join(REPO_ROOT, "config", "model_config.json"),
    os.path.join(REPO_ROOT, "agents", "llm", "llm-router", "config", "model_config.json"),
]
_CONFIG_CANDIDATES = [p for p in _CONFIG_CANDIDATES if p]

# 内嵌默认 provider 注册表（零配置文件也可列出；可用 config 覆盖/热扩展）。
DEFAULT_PROVIDERS = {
    "providers": {
        "glm":    {"type": "cloud", "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                   "models": ["glm-4-flash"], "local_only": True, "note": "云端 GLM（默认 local_only 封锁）"},
        "deepseek": {"type": "cloud", "base_url": "https://api.deepseek.com/v1/chat/completions",
                     "models": ["deepseek-chat", "deepseek-reasoner"], "local_only": True,
                     "note": "云端 DeepSeek（默认 local_only 封锁）"},
        "ornith": {"type": "local", "base_url": "http://127.0.0.1:11434/api/chat",
                   "models": ["ornith:latest"], "local_only": False, "note": "本地 ornith（ollama，不出厂）"},
        "ollama": {"type": "local", "base_url": "http://127.0.0.1:11434/api/chat",
                   "models": ["llama3.1:8b", "qwen2.5:7b"], "local_only": False,
                   "note": "本地 ollama（不出厂）"},
    },
}


def _read_env(key):
    for p in [os.path.join(os.path.expanduser("~"), ".hermes", ".env"),
              os.path.join(os.path.expanduser("~"), "AppData/Local/hermes/.env")]:
        try:
            for line in open(p, encoding="utf-8"):
                if line.split("=", 1)[0].strip() == key:
                    return line.split("=", 1)[1].strip()
        except Exception:
            continue
    return ""


def _load_provider_registry(config_path=None):
    """读取 provider 注册表：合并内嵌默认 + config/model_config.json + env 覆盖。
    返回 {providers:{name:{...}}}。热扩展：config 里新增 provider 即自动生效。"""
    cfg = {}
    paths = [config_path] if config_path else _CONFIG_CANDIDATES
    for p in paths:
        if p and os.path.isfile(p):
            try:
                cfg = json.load(open(p, encoding="utf-8"))
                break
            except Exception:
                continue
    # 兼容旧结构：{generate:{...}, review:{...}} → 转 provider 注册表
    if isinstance(cfg, dict) and "providers" not in cfg:
        merged = dict(DEFAULT_PROVIDERS)
        if cfg.get("generate"):
            merged["providers"]["glm"] = {
                "type": "cloud",
                "base_url": cfg["generate"].get("base_url",
                            merged["providers"]["glm"]["base_url"]),
                "models": [cfg["generate"].get("model", "glm-4-flash")],
                "local_only": True, "note": "云端 GLM（旧 config.generate 迁移）"}
        if cfg.get("review"):
            merged["providers"]["ornith"] = {
                "type": "local",
                "base_url": cfg["review"].get("base_url",
                            merged["providers"]["ornith"]["base_url"]),
                "models": [cfg["review"].get("model", "ornith:latest")],
                "local_only": False, "note": "本地 ornith（旧 config.review 迁移）"}
        return merged
    # 新结构：合并内嵌默认（缺的补默认，有的用 config 覆盖 → 热扩展）
    merged = dict(DEFAULT_PROVIDERS)
    if isinstance(cfg, dict) and isinstance(cfg.get("providers"), dict):
        for name, prov in cfg["providers"].items():
            merged["providers"][name] = prov
    return merged


def _post_json(url, payload, timeout=120, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers or {"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class LlmRouterAgent(AtomicAgent):
    name = "llm-router"
    version = "0.2.0"
    domain = "llm"
    description = "多模型 provider 注册表: 本地 ornith/Ollama + 云端 DeepSeek/GLM 等热扩展, local-only 数据不出厂"
    provides = ["llm.generate", "llm.review", "llm.list_models", "llm.registry"]
    depends_on = []
    inputs = ["messages", "temp", "max_tokens", "local_only", "config_path",
              "provider", "model", "api_key"]
    outputs = ["content", "model", "provider", "type", "models", "registry"]

    def _register_defaults(self):
        self.register("llm.generate", self._generate)
        self.register("llm.review", self._review)
        self.register("llm.list_models", self._list_models)
        self.register("llm.registry", self._registry)

    def _registry(self, config_path=None, local_only=True):
        """返回完整 provider 注册表（含 base_url/模型/local_or_cloud）。"""
        reg = _load_provider_registry(config_path)
        providers = reg.get("providers", {})
        return {"registry": providers,
                "count": len(providers),
                "local_providers": [n for n, p in providers.items() if p.get("type") == "local"],
                "cloud_providers": [n for n, p in providers.items() if p.get("type") == "cloud"],
                "note": "local_only 模式下云端 provider 一律封锁不出网"}

    def _list_models(self, config_path=None, local_only=True):
        """列出模型目录（热扩展）：每个 provider 的模型 + local/cloud 标注。"""
        providers = _load_provider_registry(config_path).get("providers", {})
        models = []
        for name, p in providers.items():
            for m in p.get("models", []):
                models.append({"provider": name, "model": m,
                               "type": p.get("type", "cloud")})
        return {"models": models, "count": len(models),
                "providers": list(providers.keys()),
                "local_only_gate": local_only}

    # ── llm.generate：云端生成，按 provider 路由 ────────────────
    def _generate(self, messages, temp=0.3, max_tokens=8192, local_only=True,
                  config_path=None, provider=None, model=None, api_key=None):
        """云端生成。local_only 默认 True（数据不出厂为默认，显式 False 才出网）。
        支持 provider 参数路由到 GLM/DeepSeek 等云端 provider；model 可选覆盖。
        local_only=True → 立即返回 degraded，不发任何请求。"""
        if local_only:
            return self._envelope(
                False, degraded=True,
                error="local-only 模式：禁止云端 LLM 生成（敏感数据不出厂）",
                data={"local_only": True, "provider": provider or "blocked"})
        providers = _load_provider_registry(config_path).get("providers", {})
        # 选 provider：显式 provider → 默认第一个 cloud
        if provider:
            p = providers.get(provider)
            if not p:
                return self._envelope(False, degraded=True,
                                      error=f"未知 provider: {provider}（注册表含 {list(providers)}）",
                                      data={"provider": provider})
            if p.get("type") != "cloud":
                return self._envelope(False, degraded=True,
                                      error=f"provider '{provider}' 是本地类型，请走 llm.review",
                                      data={"provider": provider, "type": p.get("type")})
        else:
            cloud = [n for n, pp in providers.items() if pp.get("type") == "cloud"]
            if not cloud:
                return self._envelope(False, degraded=True,
                                      error="无可用云端 provider", data={})
            provider = cloud[0]
            p = providers[provider]
        # API key：显式传 → env → .hermes 凭据文件
        key_env = {"glm": "ZHIPU_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}.get(provider, provider.upper() + "_API_KEY")
        key = api_key or os.environ.get(key_env, "") or _read_env(key_env)
        if not key:
            return self._envelope(
                False, degraded=True, error=f"{key_env} 未配置，无法云端生成",
                data={"provider": provider})
        url = p.get("base_url", "")
        mdl = model or (p.get("models") or [""])[0]
        payload = {"model": mdl, "messages": messages, "temperature": temp, "max_tokens": max_tokens}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            msg = body["choices"][0]["message"]
            return self._envelope(True, data={"content": msg.get("content", ""),
                                              "model": mdl, "provider": provider,
                                              "type": "cloud"})
        except Exception as e:
            return self._envelope(False, degraded=True,
                                  error=f"{type(e).__name__}: {e}",
                                  data={"model": mdl, "provider": provider})

    # ── llm.review：本地 ornith/Ollama 审查，数据不出厂 ───────────
    def _review(self, messages, temp=0.4, config_path=None, local_only=True,
                provider="ornith", model=None):
        """本地审查（ollama 兼容 /api/chat）。天然 local，数据不出厂。
        provider 可切 ornith/ollama；model 可选覆盖。"""
        providers = _load_provider_registry(config_path).get("providers", {})
        p = providers.get(provider) or {"base_url": "http://127.0.0.1:11434/api/chat",
                                        "models": ["ornith:latest"]}
        url = p.get("base_url", "http://127.0.0.1:11434/api/chat")
        mdl = model or (p.get("models") or ["ornith:latest"])[0]
        payload = {"model": mdl, "messages": messages, "stream": False,
                   "options": {"temperature": temp}}
        try:
            body = _post_json(url, payload, timeout=30)
            msg = body.get("message", {})
            return self._envelope(True, data={"content": msg.get("content", ""),
                                              "model": mdl, "provider": provider,
                                              "type": "local"})
        except Exception as e:
            return self._envelope(False, degraded=True,
                                  error=f"{type(e).__name__}: {e}",
                                  data={"model": mdl, "provider": provider})


# 模块级实例
agent = LlmRouterAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="llm-router 原子独立自测入口")
    ap.add_argument("--capability", default="list_models", choices=["generate", "review", "list_models", "registry"])
    ap.add_argument("--prompt", default="用一句话说明 CodeAgent 原子化。")
    ap.add_argument("--local-only", action="store_true", help="local-only 开关（数据不出厂）")
    ap.add_argument("--provider", default=None, help="指定 provider")
    ap.add_argument("--timeout", type=int, default=30, help="本地审查超时秒")
    args = ap.parse_args()

    agent.load()
    print("══ llm-router 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    msgs = [{"role": "user", "content": args.prompt}]
    if args.capability == "review":
        r = agent.run(_capability="llm.review", messages=msgs, temp=0.4, provider=args.provider or "ornith")
    elif args.capability == "list_models":
        r = agent.run(_capability="llm.list_models", local_only=args.local_only)
    elif args.capability == "registry":
        r = agent.run(_capability="llm.registry", local_only=args.local_only)
    else:
        r = agent.run(_capability="llm.generate", messages=msgs, local_only=args.local_only,
                      provider=args.provider)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        print("(degraded：配置/密钥/本地服务未就绪，属预期的失败降级路径)")
        sys.exit(0)
