#!/usr/bin/env python3
"""llm-router 原子壳（open_source:true）。

复用（零改动核心）：`config/model_config.json` 的模型路由结构 + env key 读取模式。
只加壳：把既有 REST 调用逻辑搬进 run() 包 {ok,data} 信封。

能力：
  llm.generate  — 云端 GLM 生成（openai 兼容）。`local_only=True` 时禁止（数据不出厂）。
  llm.review    — 本地 ornith 审查（ollama），数据不出厂。

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

# 默认模型配置候选路径（复用 model_config.json，改配置即可切换模型，零改代码）。
# 修复 P1-3：不再硬编码闭源/私有绝对路径 E:/...，优先 env 覆盖，其次仓库内配置。
_CONFIG_CANDIDATES = [
    os.environ.get("CODEAGENT_MODEL_CONFIG", ""),        # env 显式指定（可移植）
    os.path.join(REPO_ROOT, "config", "model_config.json"),
    os.path.join(REPO_ROOT, "agents", "llm", "llm-router", "config", "model_config.json"),
]
_CONFIG_CANDIDATES = [p for p in _CONFIG_CANDIDATES if p]


def _read_env(key):
    for p in [os.path.join(os.path.expanduser("~"), ".hermes", ".env"),
              os.path.join(os.path.expanduser("~"), "AppData/Local/hermes/.env")]:
        try:
            for line in open(p, encoding="utf-8"):
                # 修复 P2-8：精确匹配键名，避免 ZHIPU_API_KEY_OLD 误配前缀
                if line.split("=", 1)[0].strip() == key:
                    return line.split("=", 1)[1].strip()
        except Exception:
            continue
    return ""


def _load_model_config(config_path=None):
    """复用 model_config.json 结构：{generate:{...}, review:{...}}。"""
    paths = [config_path] if config_path else _CONFIG_CANDIDATES
    for p in paths:
        if p and os.path.isfile(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
    return {}


def _post_json(url, payload, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class LlmRouterAgent(AtomicAgent):
    name = "llm-router"
    version = "0.1.0"
    domain = "llm"
    description = "模型路由：复用 model_config，云端 GLM / 本地 ornith，local-only 数据不出厂"
    provides = ["llm.generate", "llm.review"]
    depends_on = []
    inputs = ["messages", "temp", "max_tokens", "local_only", "config_path"]
    outputs = ["content", "model", "provider"]

    def _register_defaults(self):
        self.register("llm.generate", self._generate)
        self.register("llm.review", self._review)

    # ── 配置加载（复用 model_config.json）────────────────
    def _cfg(self, config_path):
        return _load_model_config(config_path)

    def _generate(self, messages, temp=0.3, max_tokens=8192, local_only=True, config_path=None):
        """云端 GLM 生成。local_only 默认 True（数据不出厂为默认，显式 False 才出网）。
        local_only=True → 立即返回 degraded，不发任何请求。"""
        if local_only:
            return self._envelope(
                False, degraded=True,
                error="local-only 模式：禁止云端 GLM 生成（敏感数据不出厂）",
                data={"local_only": True, "provider": "blocked"})
        cfg = self._cfg(config_path).get("generate", {})
        api_key = os.environ.get("ZHIPU_API_KEY", "") or _read_env("ZHIPU_API_KEY")
        if not api_key:
            return self._envelope(
                False, degraded=True, error="ZHIPU_API_KEY 未配置，无法云端生成",
                data={"provider": "generate"})
        url = cfg.get("base_url", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
        model = cfg.get("model", "glm-4-flash")
        payload = {"model": model, "messages": messages, "temperature": temp, "max_tokens": max_tokens}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            msg = body["choices"][0]["message"]
            return self._envelope(True, data={"content": msg.get("content", ""),
                                              "model": model, "provider": "glm"})
        except Exception as e:
            return self._envelope(False, degraded=True,
                                  error=f"{type(e).__name__}: {e}", data={"model": model})

    def _review(self, messages, temp=0.4, config_path=None, local_only=True):
        """本地 ornith 审查（ollama）。天然 local，数据不出厂。"""
        cfg = self._cfg(config_path).get("review", {})
        url = cfg.get("base_url", "http://127.0.0.1:11434/api/chat")
        model = cfg.get("model", "ornith:latest")
        payload = {"model": model, "messages": messages, "stream": False,
                   "options": {"temperature": temp}}
        try:
            body = _post_json(url, payload, timeout=30)
            msg = body.get("message", {})
            return self._envelope(True, data={"content": msg.get("content", ""),
                                              "model": model, "provider": "ornith"})
        except Exception as e:
            return self._envelope(False, degraded=True,
                                  error=f"{type(e).__name__}: {e}",
                                  data={"model": model, "provider": "ornith"})


# 模块级实例
agent = LlmRouterAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="llm-router 原子独立自测入口")
    ap.add_argument("--capability", default="generate", choices=["generate", "review"])
    ap.add_argument("--prompt", default="用一句话说明 CodeAgent 原子化。")
    ap.add_argument("--local-only", action="store_true", help="local-only 开关（数据不出厂）")
    ap.add_argument("--timeout", type=int, default=30, help="本地审查超时秒")
    args = ap.parse_args()

    agent.load()
    print("══ llm-router 原子自测 ══")
    print("身份:", agent.describe()["name"], "v" + agent.describe()["version"], "status=" + agent.describe()["status"])
    msgs = [{"role": "user", "content": args.prompt}]
    if args.capability == "review":
        r = agent.run(_capability="llm.review", messages=msgs, temp=0.4)
    else:
        r = agent.run(_capability="llm.generate", messages=msgs, local_only=args.local_only)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        # 自测允许 degraded（配置/网络/密钥未就绪），不视为失败退出
        print("(degraded：配置/密钥/本地服务未就绪，属预期的失败降级路径)")
        sys.exit(0)
