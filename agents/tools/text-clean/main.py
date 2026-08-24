#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text-clean — 通用工具原子: 中文文本清洗(包装 pycommon)
提供: clean.cn / clean.ai_talk / clean.ws / clean.is_chinese
"""
import sys, os

sys.path.insert(0, r"E:/scripts/lib")
from pycommon import clean_cn, strip_ai_talk, norm_ws, is_chinese

try:
    from atomic_base import AtomicAgent
except Exception:
    AtomicAgent = None  # 独立运行(非 loader)时无基类, 仅 run() 可用

ACTIONS = {
    "clean.cn": lambda s, remove_ai_talk=False, **k: clean_cn(s, remove_ai_talk=remove_ai_talk),
    "clean.ai_talk": lambda s, **k: strip_ai_talk(s),
    "clean.ws": lambda s, **k: norm_ws(s),
    "clean.is_chinese": lambda s, **k: is_chinese(s),
}


def run(action, **kwargs):
    fn = ACTIONS.get(action)
    if not fn:
        return {"error": f"unknown action: {action}", "available": sorted(ACTIONS)}
    return {"action": action, "result": fn(**kwargs)}


# ---------- AtomicAgent 壳(loader 可加载复用) ----------
if AtomicAgent is not None:
    class TextCleanAgent(AtomicAgent):
        name = "text-clean"
        version = "0.1.0"
        domain = "tools"
        description = ("通用工具原子(省重复 P1-3): 中文文本清洗(去AI套话/规范空白/中文检测), "
                       "包装 pycommon。收敛散落脚本中反复手写的中文清洗函数。")
        provides = ["clean.cn", "clean.ai_talk", "clean.ws", "clean.is_chinese"]
        depends_on = []
        inputs = ["action", "s", "remove_ai_talk"]
        outputs = ["result", "error"]

        def _exec(self, cap, **kw):
            r = run(action=cap, **kw)
            if "error" in r:
                return {"ok": False, "data": {}, "error": r["error"], "degraded": True}
            return {"ok": True, "data": r}

        def _register_defaults(self):
            for cap in self.provides:
                self.register(cap, (lambda c=cap: (lambda **kw: self._exec(c, **kw)))())


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(run(action="clean.cn", s="总的来说，  这 方案  不错 "), ensure_ascii=False))
