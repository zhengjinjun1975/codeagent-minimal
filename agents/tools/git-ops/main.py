#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
git-ops — 通用工具原子(省重复 P1-3): 常用 git 操作
提供: git.status / git.log / git.branch / git.remote / git.sync / git.commit
来源: E:/scripts/git/20260824-git-ops.sh (高频 git 动作脚本, 收敛为原子)
加壳: 用 subprocess 调 git CLI, 不改 git 行为; 纯 stdlib 数据不出厂。
"""
import os
import subprocess

try:
    from atomic_base import AtomicAgent
except Exception:
    AtomicAgent = None  # 独立运行(非 loader)时无基类, 仅 run() 可用


def _git(args, d=None):
    """跑 git 命令, 返回 {returncode, stdout, stderr}。"""
    try:
        p = subprocess.run(["git"] + args, cwd=d or os.getcwd(),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        return {"returncode": p.returncode, "stdout": p.stdout.strip(),
                "stderr": p.stderr.strip()}
    except FileNotFoundError:
        return {"error": "git 未安装或不在 PATH"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _sync(d):
    """pull --rebase --autostash 后 push; 返回两步结果。"""
    d = d or os.getcwd()
    steps = {}
    steps["pull"] = _git(["pull", "--rebase", "--autostash"], d=d)
    if steps["pull"].get("error"):
        return steps["pull"]
    steps["push"] = _git(["push"], d=d)
    return steps


ACTIONS = {
    "git.status": lambda d=None, **k: _git(["status", "--short", "--branch"], d=d),
    "git.log": lambda d=None, n=15, **k: _git(["log", "--oneline", "-%d" % int(n)], d=d),
    "git.branch": lambda d=None, **k: _git(["branch", "--show-current"], d=d),
    "git.remote": lambda d=None, **k: _git(["remote", "-v"], d=d),
    "git.sync": lambda d=None, **k: _sync(d),
    "git.commit": lambda d=None, msg="update", **k: _commit(d, msg),
}


def _commit(d, msg):
    """add -A + commit + push; 返回每步结果。"""
    d = d or os.getcwd()
    steps = {}
    steps["add"] = _git(["add", "-A"], d=d)
    steps["commit"] = _git(["commit", "-m", msg], d=d)
    steps["push"] = _git(["push"], d=d)
    return steps


def run(action, **kwargs):
    fn = ACTIONS.get(action)
    if not fn:
        return {"error": f"unknown action: {action}", "available": sorted(ACTIONS)}
    try:
        return {"action": action, "result": fn(**kwargs)}
    except Exception as e:
        return {"action": action, "error": str(e)}


# ---------- AtomicAgent 壳(loader 可加载复用) ----------
if AtomicAgent is not None:
    class GitOpsAgent(AtomicAgent):
        name = "git-ops"
        version = "0.1.0"
        domain = "tools"
        description = ("通用工具原子(省重复 P1-3): 常用 git 操作(status/log/branch/remote/sync/commit), "
                       "包装 E:/scripts/git。纯 stdlib 数据不出厂。")
        provides = ["git.status", "git.log", "git.branch", "git.remote", "git.sync", "git.commit"]
        depends_on = []
        inputs = ["action", "d", "n", "msg"]
        outputs = ["result", "error", "returncode", "stdout", "stderr"]

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
    print(_j.dumps(run(action="git.status", d=r"C:/"), default=str))
