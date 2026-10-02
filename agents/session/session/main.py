#!/usr/bin/env python3
"""session 原子壳（open_source:true）——会话化（P1）。

借鉴 Codex `CodexThread`/`ThreadManager`/SQLite thread-store 的线程生命周期（start/resume/fork），
用 JSON 持久化实现精简版：会话 = 一次任务的一串原子调用，记录每步输入摘要/输出摘要/状态，
支持从上次中断点 resume，落盘 `.codeagent/sessions/<id>.json`（纯文本可读，数据不出厂）。

能力（纯 stdlib，数据不出厂）：
  session.start  — 创建会话 {id, created, status}
  session.step   — 追加一步（capability + 输入/输出摘要 + verdict），自动维护状态
  session.list   — 列出会话（id/created/status/steps 数，不含大 payload）
  session.get    — 取某会话（含步骤摘要）
  session.resume — 从最后未完成步骤取回上下文，供续跑
  session.status — 会话/线程生命周期状态
"""
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

STATUSES = ("running", "waiting", "completed", "failed", "cancelled")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _summarize(payload, cap=200):
    """大 payload 摘要：只留 summary/verdict/score/decision + 截断长串，防上下文膨胀。"""
    if payload is None:
        return None
    if isinstance(payload, dict):
        out = {}
        for k in ("summary", "verdict", "score", "decision", "total", "ok",
                  "granted", "safe", "verdict_", "rc"):
            if k in payload:
                out[k] = payload[k]
        if not out and payload:
            out = {k: payload[k] for k in list(payload)[:5]}
        for k, v in list(out.items()):
            if isinstance(v, str) and len(v) > cap:
                out[k] = v[:cap] + "…"
        return out
    if isinstance(payload, (list, tuple)):
        return f"[{len(payload)} 项]"
    s = str(payload)
    return s[:cap] + ("…" if len(s) > cap else "")


class SessionAgent(AtomicAgent):
    name = "session"
    version = "0.1.0"
    domain = "session"
    description = ("会话化原子（P1，借鉴Codex CodexThread/ThreadManager）: 会话=一串原子调用, JSON持久化"
                   "记录每步输入/输出摘要+状态, 支持resume续跑。落盘.codeagent/sessions, 纯文本可读数据不出厂。")
    provides = ["session.start", "session.step", "session.list",
                "session.get", "session.resume", "session.status"]
    depends_on = []
    inputs = ["sid", "task", "step_capability", "inputs", "output", "status", "session_dir"]
    outputs = ["sid", "created", "updated", "steps", "status", "step", "next", "sessions", "summary"]

    def _register_defaults(self):
        self.register("session.start", self._start)
        self.register("session.step", self._step)
        self.register("session.list", self._list)
        self.register("session.get", self._get)
        self.register("session.resume", self._resume)
        self.register("session.status", self._session_status)

    def _dir(self, session_dir=None):
        return session_dir or os.path.join(REPO_ROOT, ".codeagent", "sessions")

    def _path(self, sid, session_dir=None):
        return os.path.join(self._dir(session_dir), sid + ".json")

    def _read(self, sid, session_dir=None):
        p = self._path(sid, session_dir)
        if not os.path.isfile(p):
            return None
        return json.load(open(p, encoding="utf-8"))

    def _write(self, sess, session_dir=None):
        d = self._dir(session_dir)
        os.makedirs(d, exist_ok=True)
        with open(self._path(sess["id"], session_dir), "w", encoding="utf-8") as f:
            json.dump(sess, f, ensure_ascii=False, indent=2)

    def _start(self, task=None, sid=None, session_dir=None):
        sid = sid or uuid.uuid4().hex[:12]
        sess = {"id": sid, "created": _now(), "updated": _now(),
                "status": "running", "task": _summarize(task),
                "steps": [], "resume_from": None}
        self._write(sess, session_dir)
        return {"sid": sid, "created": sess["created"], "status": sess["status"]}

    def _step(self, sid=None, step_capability=None, inputs=None, output=None,
              status=None, session_dir=None):
        if not sid or not step_capability:
            return self._envelope(False, degraded=True, error="缺 sid 或 step_capability 入参")
        sess = self._read(sid, session_dir)
        if sess is None:
            return self._envelope(False, degraded=True, error=f"会话不存在: {sid}")
        idx = len(sess["steps"]) + 1
        st = status or "ok"          # 归一化：记录与断点判据必须用同一个状态值
        step = {"idx": idx, "capability": step_capability,
                "inputs": _summarize(inputs), "output": _summarize(output),
                "ts": _now(), "status": st}
        sess["steps"].append(step)
        sess["updated"] = _now()
        if st == "ok":
            sess["resume_from"] = idx
        # 会话状态口径（2026-09-21 改）：以**最后一次明确结果**为准，中间失败留在 steps 里可查。
        # 旧口径是"只要有一步 failed 就把整会话钉成 failed 且改不回来"——于是"中间失败、但 loop
        # 重投后最终验收通过"的任务会被记成失败账。账本口径要和交付口径一致：结果算数、过程留痕。
        # 唯一终态是 cancelled：用户叫停就是叫停，之后不再被后续步骤改写。
        if st in ("completed", "failed", "cancelled") and sess["status"] != "cancelled":
            sess["status"] = st
        self._write(sess, session_dir)
        return {"sid": sid, "step": step, "total_steps": len(sess["steps"]),
                "status": sess["status"]}

    def _list(self, session_dir=None):
        d = self._dir(session_dir)
        if not os.path.isdir(d):
            return {"sessions": [], "count": 0}
        items = []
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                s = json.load(open(os.path.join(d, fn), encoding="utf-8"))
                items.append({"sid": s["id"], "created": s.get("created"),
                              "updated": s.get("updated"), "status": s.get("status"),
                              "steps": len(s.get("steps", [])),
                              "task": s.get("task")})
            except Exception:
                continue
        return {"sessions": items, "count": len(items)}

    def _get(self, sid=None, session_dir=None):
        if not sid:
            return self._envelope(False, degraded=True, error="缺 sid 入参")
        s = self._read(sid, session_dir)
        if s is None:
            return self._envelope(False, degraded=True, error=f"会话不存在: {sid}")
        s["steps"] = [{"idx": st["idx"], "capability": st["capability"],
                       "output": st.get("output"), "status": st.get("status")}
                      for st in s.get("steps", [])]
        return s

    def _resume(self, sid=None, session_dir=None):
        """从最后成功步骤之后取回上下文：返回 next_idx 与已汇总步骤。"""
        s = self._read(sid, session_dir)
        if s is None:
            return self._envelope(False, degraded=True, error=f"会话不存在: {sid}")
        steps = s.get("steps", [])
        last_ok = s.get("resume_from", 0) or 0
        return {"sid": sid, "status": s.get("status"), "task": s.get("task"),
                "next_idx": len(steps) + 1, "resume_from": last_ok,
                "completed_steps": len(steps),
                "summary": [{"idx": st["idx"], "capability": st["capability"],
                             "output": st.get("output"), "status": st.get("status")}
                            for st in steps]}

    def _session_status(self, sid=None, session_dir=None):
        if sid:
            s = self._read(sid, session_dir)
            return {"sid": sid, "status": s["status"] if s else "unknown"}
        return {"valid_statuses": list(STATUSES)}


agent = SessionAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(SessionAgent(), run_args={
        "capability": {"default": "session.start", "choices": list(SessionAgent.provides)},
        "task": {}, "sid": {}, "capability": {}, "inputs": {}, "output": {}, "status": {},
    }))
