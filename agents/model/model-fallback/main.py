#!/usr/bin/env python3
"""model-fallback 原子壳（open_source:true）——模型降级链（P2）。

借鉴 Codex `model-provider` 多后端抽象 + fallback（cloud→local 降级），复用 codeagent
llm-router 的 provider 注册表概念（local/cloud 标注 + local_only 数据不出厂红线）。
本原子实现**降级链编排**：按偏好顺序尝试候选 provider，失败自动降级到下一个，
全部失败 → degraded（绝不外泄）。实际 LLM 调用由注入的 `call_model` 回调执行
（外部 runtime/调用方提供），本原子只做路由与降级决策。

能力（纯 stdlib，数据不出厂）：
  model.chain      — 按候选链尝试 provider，失败自动降级，返回 {provider, result, fell_back}
  model.route      — 按偏好(local_first/cloud_first/local_only) 选出候选链
  model.candidates — 返回某用途的降级候选链（provider + 本地/云端标注）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

# 默认候选链（借鉴 llm-router provider 注册表概念：local/cloud + local_only 红线）
DEFAULT_CANDIDATES = [
    {"name": "glm", "type": "cloud", "local_only": True},
    {"name": "deepseek", "type": "cloud", "local_only": True},
    {"name": "ollama", "type": "local", "local_only": False},
    {"name": "ornith", "type": "local", "local_only": False},
]


def route(purpose="generic", preference="local_first", local_only=False, candidates=None):
    """按偏好排序候选链。local_only=True 时云端候选被剔除（数据不出厂铁律）。"""
    cands = list(candidates or DEFAULT_CANDIDATES)
    if local_only:
        cands = [c for c in cands if c.get("type") == "local" or not c.get("local_only")]
    if preference == "cloud_first":
        order = sorted(cands, key=lambda c: 0 if c.get("type") == "cloud" else 1)
    elif preference == "local_first":
        order = sorted(cands, key=lambda c: 0 if c.get("type") == "local" else 1)
    else:  # registry_order
        order = cands
    return {"purpose": purpose, "preference": preference, "local_only": local_only,
            "candidates": [{"name": c["name"], "type": c.get("type"),
                            "local_only": c.get("local_only", False)} for c in order]}


def chain(messages=None, call_model=None, purpose="generic",
          preference="local_first", local_only=False, candidates=None):
    """降级链：按候选顺序尝试，失败降级到下一个。call_model 注入实际调用。
    返回统一 {ok, data} 信封（补齐 data 键），供 AtomicAgent.call 透传，
    避免链结果被外层再包一层导致的 {ok,data} 双层包裹。"""
    cands = route(purpose=purpose, preference=preference, local_only=local_only,
                  candidates=candidates)["candidates"]
    if not cands:
        return {"ok": False, "degraded": True, "error": "无可用候选（local_only 剔除全部云端后为空）",
                "data": {"verdict": "degraded", "degraded": True, "candidates": [],
                         "error": "无可用候选（local_only 剔除全部云端后为空）"}}
    if call_model is None:
        return {"ok": False, "degraded": True, "error": "未注入 call_model 回调",
                "data": {"verdict": "degraded", "degraded": True, "candidates": cands,
                         "error": "未注入 call_model 回调"}}
    errors = []
    for i, c in enumerate(cands):
        try:
            result = call_model(messages=messages, provider=c["name"],
                                purpose=purpose, local_only=local_only)
            if result is None:
                raise RuntimeError("call_model 返回空")
            if isinstance(result, dict) and result.get("ok") is False:
                raise RuntimeError(result.get("error", "provider 调用失败"))
            return {"ok": True,
                    "data": {"verdict": "success", "provider": c["name"],
                             "type": c["type"], "result": result,
                             "fell_back": i > 0, "attempted": i + 1, "errors": errors}}
        except Exception as e:
            errors.append(f"{c['name']}: {type(e).__name__}: {e}")
            continue
    return {"ok": False, "degraded": True, "error": "候选链全部失败",
            "data": {"verdict": "degraded", "degraded": True,
                     "candidates": cands, "errors": errors}}


class ModelFallbackAgent(AtomicAgent):
    name = "model-fallback"
    version = "0.1.0"
    domain = "model"
    description = ("模型降级链原子（P2，借鉴Codex model-provider fallback）: 按候选链尝试provider, "
                   "失败自动降级(cloud→local), local_only剔除云端数据不出厂。复用llm-router provider注册表概念。")
    provides = ["model.chain", "model.route", "model.candidates"]
    depends_on = ["llm.list_models"]
    inputs = ["messages", "call_model", "purpose", "preference", "local_only", "candidates"]
    outputs = ["ok", "verdict", "provider", "type", "result", "fell_back", "candidates", "errors"]

    def _register_defaults(self):
        self.register("model.chain", self._chain)
        self.register("model.route", self._route)
        self.register("model.candidates", self._candidates)

    def _route(self, purpose="generic", preference="local_first", local_only=False):
        return route(purpose=purpose, preference=preference, local_only=local_only)

    def _candidates(self, purpose="generic", preference="local_first", local_only=False):
        return route(purpose=purpose, preference=preference, local_only=local_only)

    def _chain(self, messages=None, call_model=None, purpose="generic",
               preference="local_first", local_only=False):
        return chain(messages=messages, call_model=call_model, purpose=purpose,
                     preference=preference, local_only=local_only)


agent = ModelFallbackAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(ModelFallbackAgent(), run_args={
        "capability": {"default": "model.route", "choices": list(ModelFallbackAgent.provides)},
        "purpose": {}, "preference": {}, "local_only": {},
    }))
