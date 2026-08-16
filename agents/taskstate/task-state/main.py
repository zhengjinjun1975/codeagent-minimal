#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：E:/optmem/taskstate/task_state.py 外置状态(复用 _ts 思路)
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：taskstate。数据不出厂，可独立运行。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

import subprocess
import sys as _sys
import datetime

class TaskStateAgent(AtomicAgent):
    name = "task-state"
    version = "0.1.0"
    domain = "taskstate"
    description = "任务状态: 复用 task_state.py 外置状态续跑"
    provides = ["taskstate.track"]
    depends_on = []
    inputs = ["task", "action", "tid", "state", "progress", "evidence", "gate", "status_file"]
    outputs = ["status", "task_id", "file", "progress"]

    _TS = r"E:/optmem/taskstate/task_state.py"

    def _register_defaults(self):
        self.register("taskstate.track", self._track)

    def _track(self, task, action="set", tid=None, state="", progress="",
               evidence="", gate="", status_file=None):
        """复用 task_state.py 外置状态。action: new/set/ev/gate。"""
        tid = tid or _ts_tid(task)
        args = []
        if action == "new":
            args = ["new", task, "--id", tid]
        elif action == "set":
            args = ["set", tid, state, progress]
        elif action == "ev":
            args = ["ev", tid, evidence]
        elif action == "gate":
            args = ["gate", tid, gate]
        try:
            subprocess.run([_sys.executable, self._TS] + args,
                           capture_output=True, timeout=10, text=True, errors="ignore")
            ok = True
        except Exception:
            ok = False
        file = status_file or os.path.join("_task_state.md")
        return {"status": "tracked" if ok else "degraded", "task_id": tid,
                "file": file, "action": action, "progress": progress}


def _ts_tid(task: str) -> str:
    import hashlib, re
    base = re.sub(r"[^a-z0-9]+", "-", (task or "").lower()).strip("-")[:24] or "task"
    return f"{base}-{hashlib.md5((task or '').encode()).hexdigest()[:6]}"


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
