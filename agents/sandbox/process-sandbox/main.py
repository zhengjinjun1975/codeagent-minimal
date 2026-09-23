#!/usr/bin/env python3
"""process-sandbox 原子壳（open_source:true）——进程沙箱（P0 安全边界）。

借鉴 Codex `sandboxing/`（SandboxType 多平台 + 权限 profile + exec 有界执行）与
`bug_deep.run_poc_sandbox` 既有沙箱核心（子进程隔离/超时/资源限制/输出封顶/
可执行白名单/环境净化/进程组强杀），只加壳不改核心，把沙箱能力原子化暴露。

能力（纯 stdlib，数据不出厂）：
  sandbox.poc        — 安全沙箱执行不可信 PoC（复用 run_poc_sandbox）
  sandbox.exec       — 有界执行任意命令(argv 列表, shell=False)：隔离 cwd + 超时 + 输出封顶 + env净化
  sandbox.validate   — 输入校验（非字符串/超长/非法 timeout → rejected）
  sandbox.interpreter— 可执行白名单校验（真实存在且可执行的 Python 解释器）
  sandbox.guard      — 路径约束守卫（pathguard: normpath+realpath+根白名单，防穿越）
"""
import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent
import bug_deep as bd          # 复用沙箱核心（run_poc_sandbox/_run_limited/_sanitize_env/...）
import pathguard as pg         # 复用路径穿越防护
import approval_policy as ap   # 执行期审批门（P0：高危命令默认拒）


class ProcessSandboxAgent(AtomicAgent):
    name = "process-sandbox"
    version = "0.2.0"
    domain = "sandbox"
    description = ("进程沙箱原子（P0 安全边界，借鉴 Codex sandboxing）: 安全执行不可信PoC/命令，"
                   "执行期审批门(高危默认拒)+子进程隔离+超时+资源限制+输出封顶+可执行白名单+"
                   "环境净化(数据不出厂)+路径穿越防护。")
    provides = ["sandbox.poc", "sandbox.exec", "sandbox.validate",
                "sandbox.interpreter", "sandbox.guard"]
    depends_on = []
    inputs = ["code", "cmd", "timeout", "max_output", "base_dir", "path", "base",
              "profile", "approval"]
    outputs = ["ran", "verdict", "rc", "wall_s", "evidence", "out_tail",
               "err_tail", "timed_out", "overflow", "rejected", "safe", "path",
               "decision", "refused", "pending"]

    def _register_defaults(self):
        self.register("sandbox.poc", self._poc)
        self.register("sandbox.exec", self._exec)
        self.register("sandbox.validate", self._validate)
        self.register("sandbox.interpreter", self._interpreter)
        self.register("sandbox.guard", self._guard)

    def _poc(self, code=None, timeout=8, max_output=None, base_dir=None):
        if not code:
            return self._envelope(False, degraded=True, error="缺 code 入参")
        r = bd.run_poc_sandbox(code, timeout=timeout, max_output=max_output,
                               base_dir=base_dir)
        return {"ran": r.get("ran"), "verdict": r.get("verdict"),
                "rc": r.get("rc"), "wall_s": r.get("wall_s"),
                "evidence": r.get("evidence", ""),
                "marker_hit": r.get("marker_hit"),
                "crash": r.get("crash"), "rejected": not r.get("ran", False),
                "stdout_tail": r.get("stdout_tail", ""),
                "stderr_tail": r.get("stderr_tail", "")}

    def _exec(self, cmd=None, timeout=8, max_output=None, cwd=None, env_clean=True,
              profile="restricted", approval=None, scope=None):
        """有界执行任意命令（argv 列表，shell=False）。

        **审批门（P0）**：默认档 restricted 先过审批策略再执行——
          deny（高危命令，含 Windows 侧）→ 直接拒，**根本不执行**；
          ask（中危/未知）→ 无人工裁决不执行，返回 pending 让调用方处理；
          allow → 执行。
        profile="approved_once" 表示已人工裁决放行（升级重试那一档），跳过审批门。
        approval 入参可传入调用方已取得的判定，省一次判定。

        **任务级作用域（P0-1）**：scope 传 capability_scope.CapabilityScope 时，审批
        与作用域**合判**（取更严一侧）。作用域不含 exec 权限 → 直接拒，且**不可被**
        字符串规则（哪怕显式 allow）翻盘——因为"这次任务有没有执行权"不是命令串能回答的。

        边界如实说明：这是**有界执行**（超时/净环境/输出封顶 + POSIX 资源上限），
        **不是文件系统牢笼**（Windows 无 rlimit，也不做受限令牌）。
        """
        if not cmd or not isinstance(cmd, list) or not all(isinstance(a, str) for a in cmd):
            return self._envelope(False, degraded=True,
                                  error="cmd 必须是非空 argv 字符串列表（shell=False）")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not (0 < timeout <= 60):
            return self._envelope(False, degraded=True, error=f"非法 timeout: {timeout}")
        if profile not in ("restricted", "approved_once"):
            return self._envelope(False, degraded=True,
                                  error=f"未知 profile: {profile}（只认 restricted / approved_once）")

        joined = " ".join(cmd)
        if profile == "restricted":
            dec = approval if isinstance(approval, dict) else ap.check_command(
                joined, resource_type="command")
            # 作用域是独立授权：即便调用方预先给了判定，也必须再过一次作用域门（防绕过）
            if scope is not None:
                dec = ap.apply_scope(dec, joined, scope, scope_action="exec")
            verdict = dec.get("decision")
            if verdict == "deny":
                return self._envelope(
                    False, degraded=True, error="审批拒绝（高危命令默认拒）: %s"
                    % dec.get("reason", ""),
                    data={"decision": "deny", "refused": True, "rule": dec.get("rule"),
                          "normalized": dec.get("normalized"), "cmd": joined[:200]})
            if verdict == "ask":
                return self._envelope(
                    False, degraded=True,
                    error="需人工审批（无人裁决不执行）: %s" % dec.get("reason", ""),
                    data={"decision": "ask", "refused": True, "pending": True,
                          "normalized": dec.get("normalized"), "cmd": joined[:200]})

        max_output = max_output or 512 * 1024
        sandbox_dir = cwd or tempfile.mkdtemp(prefix="codeagent_exec_")
        try:
            env = bd._sanitize_env() if env_clean else None
            r = bd._run_limited(cmd, timeout=timeout, max_output=max_output,
                                cwd=sandbox_dir, env=env)
            return {"rc": r["rc"], "timed_out": r["timed_out"],
                    "overflow": r["output_overflow"], "wall_s": r["wall"],
                    "out_tail": r["out_tail"], "err_tail": r["err_tail"],
                    "cwd": sandbox_dir}
        finally:
            if not cwd:
                shutil.rmtree(sandbox_dir, ignore_errors=True)

    def _validate(self, code=None, timeout=None):
        ok, err = bd.validate_poc(code or "", timeout=timeout)
        return {"ok": ok, "reason": err, "rejected": not ok}

    def _interpreter(self, py=None):
        try:
            p = bd._validate_interpreter(py or bd.sys_python())
            return {"allowed": True, "interpreter": p}
        except ValueError as e:
            return self._envelope(False, degraded=True, error=f"可执行白名单拒绝: {e}")

    def _guard(self, path=None, base=None):
        try:
            p = pg.safe_resolve(path, base=base)
            return {"safe": True, "path": str(p)}
        except ValueError as e:
            return self._envelope(False, degraded=True, error=f"路径穿越被拒绝: {e}")


agent = ProcessSandboxAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(ProcessSandboxAgent(), run_args={
        "capability": {"default": "sandbox.poc", "choices": list(ProcessSandboxAgent.provides)},
        "code": {}, "cmd": {}, "timeout": {}, "path": {},
    }))
