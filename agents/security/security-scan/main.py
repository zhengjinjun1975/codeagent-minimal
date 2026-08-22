#!/usr/bin/env python3
"""security-scan 原子壳（open_source:true）。

复用（零改动核心）：security_scan.scan_security / detect_secrets / govern_false_positives /
dimension_scan / scan_security_project。只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力（安全原子，纯 stdlib，数据不出厂）：
  security.scan    — 10 安全维度一站式扫描（注入/认证/授权/反序列化/文件/SSRF/加密/配置/业务/供应链）
  security.secret  — secret/硬编码密钥检测
  security.govern  — 误报治理（自指剔除 + 去重 + 危险函数库交叉验证 + 分级降噪）
  security.dim     — 单维度扫描
  security.project — 全项目扫描
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import security_scan as ss


class SecurityScanAgent(AtomicAgent):
    name = "security-scan"
    version = "0.1.0"
    domain = "security"
    description = ("安全扫描原子: 10安全维度(注入/认证/授权/反序列化/文件/SSRF/加密/配置/业务/供应链)"
                   " + 危险函数库 + secret检测 + 误报治理。纯 stdlib 数据不出厂")
    provides = ["security.scan", "security.secret", "security.govern",
                "security.dim", "security.project"]
    depends_on = []
    inputs = ["path", "code", "dimension", "rules_file"]
    outputs = ["issues", "by_dimension", "by_tier", "secrets", "total", "summary", "files"]

    def _register_defaults(self):
        self.register("security.scan", self._scan)
        self.register("security.secret", self._secret)
        self.register("security.govern", self._govern)
        self.register("security.dim", self._dim)
        self.register("security.project", self._project)

    def _get_content(self, path=None, code=None):
        if path:
            return open(str(path), encoding="utf-8", errors="ignore").read()
        if code and isinstance(code, dict):
            name = list(code.keys())[0]
            c = code[name]
            return c.get("content", c) if isinstance(c, dict) else c
        if isinstance(code, str):
            return code
        return None

    def _scan(self, path=None, code=None):
        content = self._get_content(path, code)
        if content is None:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")
        r = ss.scan_security(content)
        if path:
            r["file"] = str(path)
        return r

    def _secret(self, path=None, code=None):
        content = self._get_content(path, code)
        if content is None:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")
        found = ss.detect_secrets(content)
        return {"secrets": found, "total": len(found),
                "summary": f"secret 检测: 命中 {len(found)} 项"}

    def _govern(self, issues=None):
        if not issues:
            return self._envelope(False, degraded=True, error="缺 issues 入参")
        r = ss.govern_false_positives(issues)
        return {"issues": r, "total": len(r),
                "summary": f"误报治理后 {len(r)} 项"}

    def _dim(self, path=None, code=None, dimension="注入"):
        content = self._get_content(path, code)
        if content is None:
            return self._envelope(False, degraded=True, error="缺 path 或 code 入参")
        if dimension not in ss.SECURITY_DIMENSIONS:
            return self._envelope(False, degraded=True,
                                  error=f"维度 {dimension} 不在 {ss.SECURITY_DIMENSIONS}")
        issues = ss.dimension_scan(content, dimension)
        return {"dimension": dimension, "issues": issues, "total": len(issues),
                "summary": f"维度[{dimension}]扫描 {len(issues)} 项"}

    def _project(self, path):
        if not path:
            return self._envelope(False, degraded=True, error="缺 path 入参")
        return ss.scan_security_project(path)


agent = SecurityScanAgent()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="security-scan 原子自测入口")
    ap.add_argument("target", help="目标文件或目录")
    ap.add_argument("--capability", default="security.scan",
                    choices=["security.scan", "security.secret", "security.govern",
                             "security.dim", "security.project"])
    ap.add_argument("--dimension", default="注入")
    args = ap.parse_args()
    agent.load()
    print("══ security-scan 原子自测 ══", agent.describe()["name"],
          "status=" + agent.describe()["status"])
    kw = {}
    if args.capability == "security.dim":
        kw["path"] = args.target
        kw["dimension"] = args.dimension
    elif args.capability == "security.govern":
        # 先扫 target 得到 issues，再走误报治理，验证 govern 端到端
        kw["issues"] = ss.scan_security_file(args.target).get("issues", [])
    else:
        kw["path"] = args.target
    r = agent.run(_capability=args.capability, **kw)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
