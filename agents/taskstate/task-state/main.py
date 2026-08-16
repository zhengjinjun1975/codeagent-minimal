#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

任务状态原子：复用 task_state.py 外置状态续跑思路，但把状态逻辑**内联为纯函数**
（零依赖、可独立运行），不再以子进程耦合外部私有文件（修复 P1-3）。

能力域：taskstate。数据不出厂，可独立运行。
状态文件默认落在仓库内 `.taskstate/`；可用 env `TASK_STATE_DIR` 覆盖（可移植）。
"""

import os
import sys
import hashlib
import datetime
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent


def _ts_tid(task: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (task or "").lower()).strip("-")[:24] or "task"
    return f"{base}-{hashlib.md5((task or '').encode()).hexdigest()[:6]}"


def _state_dir() -> str:
    """状态目录：env 覆盖优先，默认仓库内 .taskstate/（可移植，无 E:/ 硬编码）。"""
    return os.environ.get("TASK_STATE_DIR", os.path.join(REPO_ROOT, ".taskstate"))


def _state_path(tid: str) -> str:
    return os.path.join(_state_dir(), f"{tid}.md")


def _apply(action, tid, state="", progress="", evidence="", gate=""):
    """纯函数：对状态文件执行 new/set/ev/gate，返回 (ok, file, stdout, stderr)。"""
    path = _state_path(tid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    lines = []
    if os.path.exists(path):
        lines = open(path, encoding="utf-8").read().splitlines()
    else:
        lines = [f"# task: {tid}", f"created: {now}", "status: running", "progress: "]
    out, err = [], []
    try:
        if action == "new":
            out.append(f"new task {tid} @ {path}")
        elif action == "set":
            updated = [f"status: {state}", f"progress: {progress}", f"updated: {now}"]
            for key in ("status:", "progress:", "updated:"):
                lines = [ln for ln in lines if not ln.startswith(key)]
            lines += updated
            out.append(f"set status={state} progress={progress}")
        elif action == "ev":
            lines.append(f"- {now} ev: {evidence}")
            out.append(f"append evidence")
        elif action == "gate":
            updated = [f"gate: {gate}", f"gated_at: {now}"]
            for key in ("gate:", "gated_at:"):
                lines = [ln for ln in lines if not ln.startswith(key)]
            lines += updated
            out.append(f"set gate={gate}")
        else:
            return False, path, "", f"未知 action: {action}"
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True, path, "\n".join(out), ""
    except Exception as e:
        return False, path, "", f"{type(e).__name__}: {e}"


class TaskStateAgent(AtomicAgent):
    name = "task-state"
    version = "0.1.0"
    domain = "taskstate"
    description = "任务状态: 内联纯函数续跑(零依赖, 状态文件 .taskstate/ 可 env 覆盖)"
    provides = ["taskstate.track"]
    depends_on = []
    inputs = ["task", "action", "tid", "state", "progress", "evidence", "gate", "status_file"]
    outputs = ["status", "task_id", "file", "progress"]

    def _register_defaults(self):
        self.register("taskstate.track", self._track)

    def _track(self, task, action="set", tid=None, state="", progress="",
               evidence="", gate="", status_file=None):
        """内联纯函数续跑（修复 P1-3：不再子进程调外部 E:/... 文件）。
        action: new/set/ev/gate。返回信封，成功带 evidence（真实文件路径），失败带 error。"""
        tid = tid or _ts_tid(task)
        ok, path, out, err = _apply(action, tid, state, progress, evidence, gate)
        data = {"task_id": tid, "action": action, "progress": progress,
                "file": path}
        if ok:
            data["status"] = "tracked"
            data["evidence"] = out or f"wrote {path}"
        else:
            data["status"] = "degraded"
            data["error"] = err
            return self._envelope(False, degraded=True, error=err or "task-state 写入失败",
                                  data=data)
        return self._envelope(True, data=data)


agent = TaskStateAgent()

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="task-state 原子自测入口")
    ap.add_argument("--task", default="实现加法函数 add(a,b)")
    ap.add_argument("--action", default="new", choices=["new", "set", "ev", "gate"])
    args = ap.parse_args()
    agent.load()
    print("══ task-state 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    r = agent.run(_capability="taskstate.track", task=args.task, action=args.action)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
