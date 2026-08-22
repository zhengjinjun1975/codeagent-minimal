#!/usr/bin/env python3
"""atomicity-audit 原子壳（open_source:true）——P0 原子化审查（覆盖你项目 codeagent-minimal）。

针对本地代码原子壳库自身的审查原子：复用 agent_loader（scan/load_registry/build_registry），
只加壳不改核心，把「原子壳库是否健壮」审计能力暴露。

能力：
  atomicity.manifest — 扫描 agents/ 全部 manifest：name==目录 / 必需字段 / entry 存在 / 依赖声明
  atomicity.registry — registry.json 与 agents/ 实测是否一致（新增未注册 / 已删仍注册 / 顺序冲突）
  atomicity.breaks   — 断链：provides/depends_on 是否在 registry 内可解析（未提供能力 / 重复提供 / 依赖环）
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import agent_loader as al


class AtomicityAuditAgent(AtomicAgent):
    name = "atomicity-audit"
    version = "0.1.0"
    domain = "atomic"
    description = ("原子化审查原子（P0，覆盖 codeagent-minimal 自身）: 复用 agent_loader 审查 "
                   "manifest(name==目录/字段/entry)/registry 一致性/能力断链。纯 stdlib 数据不出厂。")
    provides = ["atomicity.manifest", "atomicity.registry", "atomicity.breaks"]
    depends_on = []
    inputs = ["agents_dir", "registry_path"]
    outputs = ["ok", "issues", "agents", "registry_mismatch", "breaks", "verdict"]

    def _register_defaults(self):
        self.register("atomicity.manifest", self._check_manifest)
        self.register("atomicity.registry", self._registry)
        self.register("atomicity.breaks", self._breaks)

    def _scan(self, agents_dir=None):
        ad = agents_dir or al.AGENTS_DIR
        r = al.scan(ad)
        if not r["ok"]:
            return None, [f"扫描失败: {r.get('error')}"], []
        return r["data"]["manifests"], list(r["data"].get("errors", [])), None

    def _check_manifest(self, agents_dir=None, registry_path=None):
        manifests, errors, _ = self._scan(agents_dir)
        if manifests is None:
            return self._envelope(False, degraded=True, error=errors[0] if errors else "扫描失败")
        issues = [{"file": e, "issue": "manifest 校验失败"} for e in errors]
        for name, m in manifests.items():
            if m["name"] != name:
                issues.append({"file": name, "issue": f"name({m['name']}) != 目录名({name})"})
            for k in ("version", "entry", "provides", "inputs", "outputs"):
                if k not in m:
                    issues.append({"file": name, "issue": f"缺字段 {k}"})
        return {"ok": not issues, "agents": sorted(manifests), "issues": issues,
                "count": len(manifests), "verdict": f"{len(manifests)} 原子 manifest 全合规" if not issues
                else f"{len(issues)} 处 manifest 问题"}

    def _registry(self, agents_dir=None, registry_path=None):
        rp = registry_path or al.REGISTRY_PATH
        manifests, _err, _ = self._scan(agents_dir)
        if manifests is None:
            return self._envelope(False, degraded=True, error="agents 扫描失败")
        reg = al.load_registry(rp)
        if not reg["ok"]:
            return self._envelope(False, degraded=True, error=reg.get("error", "registry 加载失败"))
        reg_agents = reg["data"]["agents"]
        mismatch = []
        missing_in_registry = sorted(set(manifests) - set(reg_agents))
        stale_in_registry = sorted(set(reg_agents) - set(manifests))
        if missing_in_registry:
            mismatch.append(f"磁盘已有但未注册 registry: {missing_in_registry}")
        if stale_in_registry:
            mismatch.append(f"registry 残留但磁盘已删: {stale_in_registry}")
        return {"ok": not mismatch, "registered": sorted(reg_agents),
                "on_disk": sorted(manifests), "mismatch": mismatch,
                "verdict": "registry 与磁盘一致" if not mismatch else "; ".join(mismatch)}

    def _breaks(self, agents_dir=None, registry_path=None):
        rp = registry_path or al.REGISTRY_PATH
        reg = al.load_registry(rp)
        if not reg["ok"]:
            return self._envelope(False, degraded=True, error=reg.get("error", "registry 加载失败"))
        conflicts = reg["data"]["conflicts"]
        return {"ok": not conflicts, "breaks": conflicts,
                "count": len(conflicts),
                "verdict": "能力依赖全可解析, 无断链" if not conflicts
                else f"{len(conflicts)} 处断链/冲突"}


agent = AtomicityAuditAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(AtomicityAuditAgent(), run_args={
        "capability": {"default": "atomicity.breaks", "choices": list(AtomicityAuditAgent.provides)},
        "agents_dir": {},
    }))
