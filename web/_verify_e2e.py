#!/usr/bin/env python3
# web 前端可执行验证：用 UTF-8 Python 客户端真实调用 HTTP API 跑 chain/evolve/status
import json, urllib.request

BASE = "http://127.0.0.1:8099"

def post(cmd, payload):
    req = urllib.request.Request(
        BASE + "/api/run",
        data=json.dumps({"cmd": cmd, "payload": payload}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))

# chain 组装链（真实执行 think→gen→review→test→evolve）
d = post("chain", {"task": "实现一个计算平均值的函数",
                   "code": {"avg.py": "def avg(xs):\n    return sum(xs)/len(xs)\n"}})
print("CHAIN cmd:", d.get("cmd"), "| ok:", d.get("result", {}).get("chain", {}).get("ok"))
c = d.get("result", {}).get("chain", {})
flow = list(c.get("data", {}).get("results", {}).keys()) if c.get("data") else []
print("CHAIN flow steps:", flow)
for st, res in (c.get("data", {}).get("results", {}) or {}).items():
    print(f"  step {st}: ok={res.get('ok')}")

# evolve 自进化
d = post("evolve", {"task": "审查样本任务", "outcome": {"score": 80, "issues": []}})
e = d.get("result", {}).get("evolve", {})
print("EVOLVE cmd:", d.get("cmd"), "| ok:", e.get("ok"), "| keys:", list(e.get("data", {}).keys())[:6])

# status
d = post("status", {})
s = d.get("result", {}).get("status", {})
print("STATUS cmd:", d.get("cmd"), "| ok:", d.get("result", {}).get("ok"),
      "| count:", s.get("count"), "| degraded:", s.get("degraded"))

# files
with urllib.request.urlopen(BASE + "/api/files", timeout=15) as r:
    fl = json.loads(r.read().decode("utf-8"))
print("FILES:", len(fl.get("data", [])), "targets")
