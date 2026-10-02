"""event-log 原子：append-only 事件落盘 + 检查点 + 回放。"""
import json
import os
import time
import hashlib

from atomic_base import AtomicAgent, run_cli, exclusive_file_lock


def _default_store_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_store")


class EventLogAgent(AtomicAgent):
    name = "event-log"
    version = "0.1.0"
    domain = "memory"
    description = "事件流原子：append-only 事件落盘 + 检查点 + 按事件回放，用于长任务事后定位。"
    open_source = True
    provides = ["event.append", "event.checkpoint", "event.replay", "event.timeline"]
    depends_on = []
    inputs = ["session_key", "kind", "payload", "outcome", "state",
              "from_seq", "to_seq", "store_dir"]
    outputs = ["seq", "events", "timeline", "restored_state", "up_to_seq", "state_digest"]

    def _register_defaults(self):
        self.register("event.append", self._append)
        self.register("event.checkpoint", self._checkpoint)
        self.register("event.replay", self._replay)
        self.register("event.timeline", self._timeline)

    # ---- 内部工具 ----

    def _path(self, session_key, store_dir=None):
        d = store_dir or _default_store_dir()
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "%s.jsonl" % session_key)

    @staticmethod
    def _read_lines(path):
        if not os.path.exists(path):
            return []
        out = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    # 文件不存在时最后一条 seq 就是 0，这是本意。
    @staticmethod
    def _last_seq(path):
        # 只读文件尾部取最后一条的 seq（O(1) 量级）；解析不出才退回全量计数。
        try:
            size = os.path.getsize(path)
        except OSError:
            return 0
        if size <= 0:
            return 0
        with open(path, "rb") as handle:
            back = min(8192, size)
            handle.seek(size - back)
            data = handle.read(back)
        rows = [ln for ln in data.split(chr(10).encode("utf-8")) if ln.strip()]
        if not rows:
            return 0
        try:
            return int(json.loads(rows[-1].decode("utf-8", "replace")).get("seq") or 0)
        except (ValueError, TypeError, AttributeError):
            return len(EventLogAgent._read_lines(path))

    @staticmethod
    def _append_line(path, obj):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    @staticmethod
    def _digest(state):
        return hashlib.sha1(str(state).encode("utf-8")).hexdigest()[:12]

    def _session_files(self, session_key, store_dir=None):
        # 主文件 + 归档段（_archive/<session>-<from>-<to>.jsonl）；归档后历史仍要可读。
        files = [self._path(session_key, store_dir)]
        arch_dir = os.path.join(store_dir or _default_store_dir(), "_archive")
        if os.path.isdir(arch_dir):
            prefix = session_key + "-"
            for fn in sorted(os.listdir(arch_dir)):
                if fn.startswith(prefix) and fn.endswith(".jsonl"):
                    files.append(os.path.join(arch_dir, fn))
        return files

    def _read_session(self, session_key, store_dir=None):
        # 主文件与归档段合并后的完整事件视图（按 seq 升序）。
        rows = []
        for fp in self._session_files(session_key, store_dir):
            rows.extend(self._read_lines(fp))
        rows.sort(key=lambda r: r.get("seq", 0))
        return rows

    # ---- 能力实现 ----

    def _append(self, session_key=None, kind=None, payload=None, outcome=None, store_dir=None):
        if not session_key or not kind:
            return {"ok": False, "data": {}, "error": "session_key 与 kind 必填", "degraded": True}
        path = self._path(session_key, store_dir)
        with exclusive_file_lock(path):
            seq = self._last_seq(path) + 1
            row = {"seq": seq, "ts": time.time(), "kind": kind, "payload": payload, "outcome": outcome}
            self._append_line(path, row)
        return {"ok": True, "data": {"seq": seq}}

    def _checkpoint(self, session_key=None, state=None, store_dir=None):
        if not session_key:
            return {"ok": False, "data": {}, "error": "session_key 必填", "degraded": True}
        path = self._path(session_key, store_dir)
        with exclusive_file_lock(path):
            seq = self._last_seq(path) + 1
            digest = self._digest(state)
            row = {"seq": seq, "ts": time.time(), "kind": "__checkpoint__",
                   "state": state, "state_digest": digest}
            self._append_line(path, row)
        return {"ok": True, "data": {"seq": seq, "state_digest": digest}}

    def _timeline(self, session_key=None, from_seq=0, to_seq=None, store_dir=None):
        if not session_key:
            return {"ok": False, "data": {}, "error": "session_key 必填", "degraded": True}
        path = self._path(session_key, store_dir)
        events = []
        for row in self._read_session(session_key, store_dir):
            if row.get("kind") == "__checkpoint__":
                continue
            seq = row.get("seq", 0)
            if seq <= from_seq:
                continue
            if to_seq is not None and seq > to_seq:
                continue
            events.append(row)
        events.sort(key=lambda r: r.get("seq", 0))
        return {"ok": True, "data": {"events": events, "count": len(events)}}

    def _replay(self, session_key=None, to_seq=None, store_dir=None):
        if not session_key:
            return {"ok": False, "data": {}, "error": "session_key 必填", "degraded": True}
        path = self._path(session_key, store_dir)
        best = None
        for row in self._read_session(session_key, store_dir):
            if row.get("kind") != "__checkpoint__":
                continue
            seq = row.get("seq", 0)
            if to_seq is not None and seq > to_seq:
                continue
            if best is None or seq > best.get("seq", 0):
                best = row
        if best is None:
            return {"ok": True, "data": {"restored_state": None, "up_to_seq": 0}}
        return {"ok": True, "data": {"restored_state": best.get("state"),
                                     "up_to_seq": best.get("seq", 0)}}


if __name__ == "__main__":
    import sys
    sys.exit(run_cli(EventLogAgent(), run_args={
        "capability": {"default": EventLogAgent.provides[0], "choices": list(EventLogAgent.provides)},
        "session_key": {}, "kind": {}, "payload": {}, "state": {}, "store_dir": {},
    }, description="event-log 原子自测入口"))