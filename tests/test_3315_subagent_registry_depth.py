#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""③ 子代理注册表 + 派活深度上限 —— 真实自测（跑真实 AgentRuntime + 真实派活漏斗）。

断言：
  1. subagent_registry() 真实列出已加载原子(子代理), count>0 且含 loaded:true 项。
  2. 深度门在真实 run_capability 漏斗上拦截"委托型递归派活"：默认 max_depth=2 → 第3层被拦。
  3. 参数覆盖 max_depth=3 → 提升到第4层才拦(可覆盖/继承生效)。
  4. 非回归：正常扁平派活(顺序两次真实原子调用)不受深度门影响(不误拦)。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import json
from agent_runtime import AgentRuntime

_PASS = {"checks": [], "pass": True}


def check(name, cond, detail=""):
    _PASS["checks"].append({"name": name, "ok": bool(cond), "detail": "" if cond else detail})
    _PASS["pass"] = _PASS["pass"] and bool(cond)


class _FakeDelegate:
    """真实委托型子代理(duck-type 对齐 AtomicAgent): 委托给另一能力 → 形成递归派活,
    用于证明深度门在真实 run_capability 漏斗上拦截, 而非只存在于纸面。"""
    def __init__(self, rt, cap_root, cap_nest):
        self._rt, self._root, self._nest = rt, cap_root, cap_nest

    def capabilities(self):
        return {self._root: {"callable": True}, self._nest: {"callable": True}}

    def run(self, _capability, **inputs):
        if _capability == self._root:
            return self._rt.run_capability(self._nest)   # 委托一层
        return self._rt.run_capability(self._nest)       # 继续下钻直到被深度门拦截


def main():
    rt = AgentRuntime(local_only=True)

    # 1) 子代理注册表可 list
    reg = rt.subagent_registry()
    check("registry ok", reg.get("ok") is True)
    loaded = [s for s in reg["data"]["subagents"] if s.get("loaded")]
    check("registry lists real atoms", reg["data"]["count"] > 0 and len(loaded) > 0,
          f"count={reg['data'].get('count')} loaded={len(loaded)}")

    # 2) 注入委托型子代理, 证明深度门默认 max_depth=2 → 第3层被拦
    fake = _FakeDelegate(rt, "delegate3315.root", "delegate3315.nest")
    rt.agents["__delegate3315"] = fake
    rt._cap_index["delegate3315.root"] = "__delegate3315"
    rt._cap_index["delegate3315.nest"] = "__delegate3315"
    r = rt.run_capability("delegate3315.root")
    check("default depth2 blocks layer3", r.get("ok") is False and "深度超过上限" in r.get("error", ""),
          f"error={r.get('error')}")
    check("depth data honest",
          r.get("data", {}).get("dispatch_depth") == 3 and r.get("data", {}).get("max_subagent_depth") == 2,
          f"data={r.get('data')}")

    # 3) 覆盖 max_depth=3(继承给后代) → 第4层才拦
    r3 = rt.run_capability("delegate3315.root", max_depth=3)
    check("override max_depth=3 blocks layer4",
          r3.get("ok") is False and r3.get("data", {}).get("dispatch_depth") == 4
          and r3.get("data", {}).get("max_subagent_depth") == 3,
          f"error={r3.get('error')} data={r3.get('data')}")

    # 4) 非回归: 正常扁平派活不受深度门影响(顺序两次真实调用均非"深度拦截")
    caps = rt.available_capabilities()
    cand = "skill.list" if "skill.list" in caps else next(iter(caps))
    e1 = rt.run_capability(cand)
    e2 = rt.run_capability(cand)
    blocked = [e.get("error", "") for e in (e1, e2) if "深度超过上限" in e.get("error", "")]
    check("flat dispatch not blocked by depth", not blocked,
          f"candidate={cand} errs={blocked}")

    # 清理注入(仅本进程内, 不影响任何持久状态)
    rt.agents.pop("__delegate3315", None)
    rt._cap_index.pop("delegate3315.root", None)
    rt._cap_index.pop("delegate3315.nest", None)

    print(json.dumps(_PASS, ensure_ascii=False, indent=2))
    return _PASS["pass"]


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
