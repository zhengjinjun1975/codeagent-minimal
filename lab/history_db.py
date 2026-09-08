#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""history_db.py — SQLite 持久化（纯 stdlib，线程安全，WAL，数据不出私域）。

表：
  sessions       对话会话（chat_router 上下文注入，context-compact 语义取最近 N）
  debug_history  黑箱调试历史（FR3.2: 失败输入/步骤/补丁/回归/是否成功）
  reports        审查报告（FR6.4 历史回看）
  edit_log       编辑审计（FR2.5 全部留存）
  pipelines      编排运行记录（拖拽管道历史）
  models_snapshot 模型配置快照（每次保存记一版）

铁律：壳层直接操作 sqlite3（锁内）；原子数据一律经信封。
"""
import json
import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL, content TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS debug_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created REAL NOT NULL, failure TEXT, file TEXT, test_cmd TEXT,
  errsig TEXT, symbols TEXT, steps_json TEXT, patch TEXT,
  ok INTEGER NOT NULL DEFAULT 0, evidence TEXT, rounds INTEGER DEFAULT 0,
  run_id TEXT
);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created REAL NOT NULL, target TEXT, title TEXT,
  sections_json TEXT, findings_json TEXT, structure_json TEXT,
  summary TEXT, ok INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS edit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created REAL NOT NULL, path TEXT NOT NULL, action TEXT NOT NULL,
  size INTEGER DEFAULT 0, ok INTEGER NOT NULL DEFAULT 0, detail TEXT
);
CREATE TABLE IF NOT EXISTS pipelines(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created REAL NOT NULL, name TEXT, graph_json TEXT,
  result_json TEXT, ok INTEGER NOT NULL DEFAULT 0, summary TEXT
);
CREATE TABLE IF NOT EXISTS models_snapshot(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created REAL NOT NULL, config_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_debug_errsig ON debug_history(errsig);
CREATE INDEX IF NOT EXISTS idx_debug_file ON debug_history(file);
"""


class HistoryDB:
    def __init__(self, db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _q(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            rows = [dict(r) for r in cur.fetchall()]
            self._conn.commit()
        return rows

    def _w(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid

    # ── sessions ─────────────────────────────
    def add_session(self, role, content):
        return self._w("INSERT INTO sessions(role, content, ts) VALUES(?,?,?)",
                       (role, content, time.time()))

    def recent_sessions(self, n=20):
        """最近 n 条（旧→新，供上下文注入）。"""
        rows = self._q("SELECT * FROM (SELECT * FROM sessions ORDER BY id DESC LIMIT ?) "
                       "ORDER BY id ASC", (n,))
        return rows

    def clear_sessions(self):
        return self._w("DELETE FROM sessions")

    # ── debug_history ────────────────────────
    def add_debug(self, failure, file, test_cmd, errsig, symbols, steps,
                  patch, ok, evidence, rounds, run_id):
        return self._w(
            "INSERT INTO debug_history(created, failure, file, test_cmd, errsig, "
            "symbols, steps_json, patch, ok, evidence, rounds, run_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), failure or "", file or "", test_cmd or "", errsig or "",
             json.dumps(symbols or [], ensure_ascii=False),
             json.dumps(steps or [], ensure_ascii=False, default=str),
             patch or "", 1 if ok else 0, evidence or "", rounds or 0, run_id or ""))

    def list_debug(self, q="", limit=50):
        if q:
            like = f"%{q}%"
            rows = self._q("SELECT * FROM debug_history WHERE failure LIKE ? OR file LIKE ? "
                           "OR errsig LIKE ? ORDER BY id DESC LIMIT ?",
                           (like, like, like, limit))
        else:
            rows = self._q("SELECT * FROM debug_history ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["symbols"] = json.loads(r["symbols"] or "[]")
            r["steps"] = json.loads(r["steps_json"] or "[]")
        return rows

    def get_debug(self, did):
        rows = self._q("SELECT * FROM debug_history WHERE id=?", (did,))
        if not rows:
            return None
        r = rows[0]
        r["symbols"] = json.loads(r["symbols"] or "[]")
        r["steps"] = json.loads(r["steps_json"] or "[]")
        return r

    # ── reports ──────────────────────────────
    def add_report(self, target, title, sections, findings, structure, summary, ok):
        return self._w(
            "INSERT INTO reports(created, target, title, sections_json, findings_json, "
            "structure_json, summary, ok) VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), target or "", title or "", json.dumps(sections, ensure_ascii=False,
                                                                default=str),
             json.dumps(findings, ensure_ascii=False, default=str),
             json.dumps(structure, ensure_ascii=False, default=str),
             summary or "", 1 if ok else 0))

    def list_reports(self, limit=50):
        return self._q("SELECT id, created, target, title, summary, ok "
                       "FROM reports ORDER BY id DESC LIMIT ?", (limit,))

    def get_report(self, rid):
        rows = self._q("SELECT * FROM reports WHERE id=?", (rid,))
        if not rows:
            return None
        r = rows[0]
        r["sections"] = json.loads(r["sections_json"] or "[]")
        r["findings"] = json.loads(r["findings_json"] or "[]")
        r["structure"] = json.loads(r["structure_json"] or "{}")
        return r

    # ── edit_log ─────────────────────────────
    def add_edit_log(self, path, action, size, ok, detail=""):
        return self._w("INSERT INTO edit_log(created, path, action, size, ok, detail) "
                       "VALUES(?,?,?,?,?,?)",
                       (time.time(), path, action, size or 0, 1 if ok else 0, detail or ""))

    def list_edit_log(self, limit=100):
        return self._q("SELECT * FROM edit_log ORDER BY id DESC LIMIT ?", (limit,))

    # ── pipelines ────────────────────────────
    def add_pipeline(self, name, graph, result, ok, summary):
        return self._w("INSERT INTO pipelines(created, name, graph_json, result_json, "
                       "ok, summary) VALUES(?,?,?,?,?,?)",
                       (time.time(), name or "", json.dumps(graph, ensure_ascii=False,
                                                            default=str),
                        json.dumps(result, ensure_ascii=False, default=str),
                        1 if ok else 0, summary or ""))

    def list_pipelines(self, limit=30):
        return self._q("SELECT id, created, name, ok, summary FROM pipelines "
                       "ORDER BY id DESC LIMIT ?", (limit,))

    def get_pipeline(self, pid):
        rows = self._q("SELECT * FROM pipelines WHERE id=?", (pid,))
        if not rows:
            return None
        r = rows[0]
        r["graph"] = json.loads(r["graph_json"] or "{}")
        r["result"] = json.loads(r["result_json"] or "{}")
        return r

    # ── models_snapshot ──────────────────────
    def add_models_snapshot(self, config):
        return self._w("INSERT INTO models_snapshot(created, config_json) VALUES(?,?)",
                       (time.time(), json.dumps(config, ensure_ascii=False)))


_db = None
_db_lock = threading.Lock()


def get_db():
    global _db
    with _db_lock:
        if _db is None:
            from lab_config import get_config
            cfg = get_config()
            _db = HistoryDB(cfg.get("db_path"))
        return _db


def reset_db_for_tests(path):
    global _db
    with _db_lock:
        _db = HistoryDB(path)
    return _db