#!/usr/bin/env python3
"""domain-review 原子壳（open_source:true）——P2 领域代码审查。

能力：
  domain.imports — 跨仓库 import 依赖拓扑断链：复用 chain_break.multi_repo_break_check(多仓库 import/路径断链)
  domain.valve   — 工业阀门领域规则审查：内置阀门领域规则表，校验阀门配置数据（压力/温度/失效位置/泄漏等级）
纯 stdlib 数据不出厂，加壳不改核心（imports 复用既有 chain_break 核心）。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import chain_break as cb

# 工业阀门领域规则表（默认）
_VALVE_RULES = [
    {"id": "valve.required", "severity": "P0",
     "desc": "必填字段: id/type/pressure_rating(MPa)/fail_position(open|close|hold)",
     "check": lambda v: not (v.get("id") and v.get("type") and v.get("pressure_rating") and v.get("fail_position"))},
    {"id": "valve.fail_safe", "severity": "P0",
     "desc": "失效安全位置必须在 {open, close, hold}",
     "check": lambda v: v.get("fail_position") not in (None, "open", "close", "hold")},
    {"id": "valve.pressure_margin", "severity": "P1",
     "desc": "操作压力 operating_pressure <= 额定 pressure_rating*0.8(80% 裕度)",
     "check": lambda v: (v.get("operating_pressure") is not None
                          and v.get("pressure_rating") is not None
                          and float(v["operating_pressure"]) > float(v["pressure_rating"]) * 0.8)},
    {"id": "valve.temp_rating", "severity": "P1",
     "desc": "操作温度 operating_temp 须在 [temp_min, temp_max] 内",
     "check": lambda v: (v.get("operating_temp") is not None and v.get("temp_max") is not None
                          and float(v["operating_temp"]) > float(v["temp_max"]))},
    {"id": "valve.leakage_class", "severity": "P2",
     "desc": "泄漏等级 leakage_class 如存在须为有效值(如 ISO5208 A/B/C 或 ANSI VI)",
     "check": lambda v: (v.get("leakage_class") is not None
                          and str(v["leakage_class"]).upper() not in
                          ("A", "B", "C", "VI", "V", "IV", "III", "II", "I"))},
    {"id": "valve.type_material", "severity": "P2",
     "desc": "阀体材质 material 如填写须非空(不锈钢316L/碳钢WCB等)",
     "check": lambda v: ("material" in v and not str(v.get("material") or "").strip())},
]


class DomainReviewAgent(AtomicAgent):
    name = "domain-review"
    version = "0.1.0"
    domain = "domain"
    description = ("领域代码审查原子（P2）: 跨仓库import依赖拓扑断链(复用chain_break) + "
                   "工业阀门领域规则审查。纯 stdlib 数据不出厂。")
    provides = ["domain.imports", "domain.valve"]
    depends_on = []
    inputs = ["repos", "target", "rules", "data"]
    outputs = ["ok", "broken", "topology", "violations", "rules", "verdict"]

    def _register_defaults(self):
        self.register("domain.imports", self._imports)
        self.register("domain.valve", self._valve)

    def _imports(self, repos=None, target=None):
        repos = repos or (target and [target]) or []
        if isinstance(repos, str):
            repos = [repos]
        r = cb.multi_repo_break_check(repos, report=False)
        return {"ok": r.get("ok", False), "broken": r.get("broken", []),
                "topology": r.get("checks", []), "repos": r.get("repos", []),
                "by_tier": r.get("by_tier", {}), "summary": r.get("summary", ""),
                "verdict": r.get("summary", "")}

    def _valve(self, data=None, rules=None, target=None):
        """工业阀门领域规则审查：data 为阀门记录 list[dict]；rules 可覆盖默认规则表。"""
        rules = rules or _VALVE_RULES
        records = data or []
        if isinstance(records, dict):
            records = [records]
        if target and isinstance(target, str) and target.endswith(".json"):
            import json
            try:
                with open(target, encoding="utf-8") as fh:
                    records = json.load(fh)
                    if isinstance(records, dict):
                        records = [records]
            except (OSError, json.JSONDecodeError):
                records = records
        if not records:
            return {"ok": True, "violations": [], "records": 0, "rules": len(rules),
                    "verdict": "无阀门数据输入；规则表就绪"}
        violations = []
        for i, rec in enumerate(records):
            for rule in rules:
                try:
                    if rule["check"](rec):
                        violations.append({"record": rec.get("id") or i,
                                           "rule": rule["id"], "severity": rule["severity"],
                                           "desc": rule["desc"]})
                except (TypeError, ValueError):
                    violations.append({"record": rec.get("id") or i,
                                       "rule": rule["id"], "severity": rule["severity"],
                                       "desc": f"{rule['desc']} (字段类型/缺值)"})
        by_sev = {"P0": 0, "P1": 0, "P2": 0}
        for v in violations:
            by_sev[v["severity"]] = by_sev.get(v["severity"], 0) + 1
        return {"ok": not violations, "violations": violations,
                "records": len(records), "rules": len(rules), "by_sev": by_sev,
                "verdict": f"阀门领域规则全合规" if not violations
                else f"{len(violations)} 处违规(P0={by_sev['P0']}/P1={by_sev['P1']}/P2={by_sev['P2']})"}


agent = DomainReviewAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(DomainReviewAgent(), run_args={
        "capability": {"default": "domain.valve", "choices": list(DomainReviewAgent.provides)},
        "repos": {}, "target": {}, "data": {},
    }))
