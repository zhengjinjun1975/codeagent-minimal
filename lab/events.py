#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""events.py — 事件总线（纯 stdlib，线程安全，事件驱动无死角 NFR5）。

- 环形缓冲（默认 3000 条），每条 {seq, ts, type, payload}
- emit(): 原子完成/调试状态迁移/报告进度/错误，全部有事件；错误不吞
- since(): 按 seq 增量拉取（前端轮询）
"""
import threading
import time

MAX_EVENTS = 3000


class EventBus:
    def __init__(self, max_events=MAX_EVENTS):
        self._items = []          # [{seq, ts, type, payload}]
        self._seq = 0
        self._lock = threading.Lock()
        self._max = max_events

    def emit(self, etype, payload=None):
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": time.time(),
                  "type": etype, "payload": payload or {}}
            self._items.append(ev)
            if len(self._items) > self._max:
                self._items = self._items[-self._max:]
            return ev

    def since(self, seq=0, limit=500):
        with self._lock:
            items = [e for e in self._items if e["seq"] > seq]
        return items[-limit:]

    def last_seq(self):
        with self._lock:
            return self._items[-1]["seq"] if self._items else 0


_bus = None
_bus_lock = threading.Lock()


def get_bus():
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = EventBus()
        return _bus


def emit(etype, payload=None):
    return get_bus().emit(etype, payload)