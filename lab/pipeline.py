#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — 拖拽式编排执行器（35 原子 + 扩展原子组装成管道，真实执行）。

- 拓扑校验：依赖环检测（Kahn）+ 原子 exists + 能力 ∈ provides + 边目标存在
- 依赖方向：原子 depends_on 的能力提供者必须先执行；边（数据流）先序约束同样入拓扑
- 执行：逐节点运行（atom_runner 子进程/进程内信封），每节点 done/error 发事件（无死角）
- 边数据流：上游输出按 map 注入下游 input_name（data|envelope|summary）
- 持久化：每次运行入 pipelines 表（FR7.3 数据不出私域）
"""
import threading
import time

from events import emit
from lab_config import get_config

_runs = {}        # run_id -> {status, graph, results, error, started}
_runs_lock = threading.Lock()
_run_seq = 0


def new_run_id():
    global _run_seq
    with _runs_lock:
        _run_seq += 1
        return f"pipe-{time.strftime('%Y%m%d%H%M%S')}-{_run_seq}"


# ── 拓扑校验 ──────────────────────────────────
def validate(graph, registry):
    """校验 {nodes, edges} → (ok, errors) + 执行顺序。"""
    errors = []
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    names = {a["name"] for a in registry["atoms"]}
    if not nodes:
        return False, ["画布为空：请拖入至少一个原子"], []
    for n in graph.get("nodes", []):
        if n.get("atom") not in names:
            errors.append(f"节点 {n.get('label', n.get('id'))}: 原子 {n.get('atom')} 不存在")
            continue
        atom = next(a for a in registry["atoms"] if a["name"] == n["atom"])
        cap = n.get("capability") or (atom["provides"][0] if atom["provides"] else "")
        if cap and cap not in atom["provides"]:
            errors.append(f"节点 {n.get('label', n.get('id'))}: 能力 {cap} 不在 "
                          f"{n['atom']} 的 provides 中")
    e_ids = set(nodes.keys())
    for e in edges:
        if e.get("from") not in e_ids or e.get("to") not in e_ids:
            errors.append(f"边 {e.get('from')}→{e.get('to')} 端点不存在")
    # Kahn 拓扑：边先序 + depends_on 先序（dep 提供者若在管道内必须先跑）
    indeg = {nid: 0 for nid in nodes}
    adj = {nid: [] for nid in nodes}
    for e in edges:
        indeg[e["to"]] += 1
        adj[e["from"]].append(e["to"])
    # depends_on：若依赖的能力由管道内某原子提供，则先跑提供者
    provided_here = {}
    for nid, n in nodes.items():
        atom = next((a for a in registry["atoms"] if a["name"] == n["atom"]), None)
        if atom:
            for cap in atom["provides"]:
                provided_here[cap] = nid
    for nid, n in nodes.items():
        atom = next((a for a in registry["atoms"] if a["name"] == n["atom"]), None)
        if atom:
            for dep in atom.get("depends_on", []):
                pid = provided_here.get(dep)
                if pid and pid != nid and (pid, nid) not in edges:
                    indeg[nid] += 1
                    adj[pid].append(nid)
    import collections
    q = collections.deque([nid for nid, d in indeg.items() if d == 0])
    order = []
    while q:
        cur = q.popleft()
        order.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)
    if len(order) != len(nodes):
        loop = [nid for nid, d in indeg.items() if d > 0]
        errors.append(f"存在依赖环，无法执行: {loop}")
    if errors:
        return False, errors, []
    return True, [], order


# ── 执行 ──────────────────────────────────────
def run_pipeline(name, graph, registry=None, db=None, timeout=180):
    """异步执行管道。返回 {run_id, status:running}；进度经事件流推送。"""
    from atom_loader_ext import merged_registry, find_atom
    from atom_runner import run_capability
    cfg = get_config()
    reg = registry or merged_registry(cfg.codeagent_root())
    ok, errors, order = validate(graph, reg)
    run_id = new_run_id()
    if not ok:
        with _runs_lock:
            _runs[run_id] = {"status": "failed", "graph": graph, "results": {},
                             "error": "; ".join(errors), "started": time.time(),
                             "name": name, "ok": False}
        emit("pipeline.failed", {"run_id": run_id, "errors": errors})
        if db:
            db.add_pipeline(name, graph, _runs[run_id], False, "; ".join(errors))
        return {"ok": False, "run_id": run_id, "status": "failed", "errors": errors}

    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    with _runs_lock:
        _runs[run_id] = {"status": "running", "graph": graph, "results": {},
                         "error": None, "started": time.time(), "name": name,
                         "order": order, "ok": None}
    emit("pipeline.started", {"run_id": run_id, "name": name, "nodes": len(nodes),
                              "order": order})

    def _worker():
        results, summary = {}, []
        all_ok = True
        try:
            for nid in order:
                n = nodes[nid]
                atom_v = find_atom(n["atom"], reg)
                cap = n.get("capability") or (atom_v["provides"][0]
                                              if atom_v and atom_v["provides"] else "")
                params = dict(n.get("params") or {})
                # 边数据流注入（P0-2）：只注入到目标原子「声明且 edge_ok」的输入，
                # 否则该边仅表达拓扑先后（数据被原子忽略属预期，不再静默丢弃/抛错）。
                target_atom_view = find_atom(n["atom"], reg) or {}
                schema_map = {s.get("name"): s for s in (target_atom_view.get("inputs_schema") or [])}
                for e in edges:
                    if e["to"] == nid:
                        src = results.get(e["from"])
                        if src is None:
                            continue
                        iname = e.get("input_name") or "input"
                        sch = schema_map.get(iname)
                        if sch is not None and not sch.get("edge_ok"):
                            continue  # 仅排序边：不注入该输入
                        val = _edge_value(src, e.get("map", "data"))
                        params[iname] = val
                emit("pipeline.node_started",
                     {"run_id": run_id, "node": nid, "atom": n["atom"], "cap": cap})
                env = run_capability(n["atom"], cap, params, timeout=timeout,
                                     registry=reg, codeagent_root=cfg.codeagent_root())
                env["node"] = nid
                env["atom"] = n["atom"]
                env["capability"] = cap
                results[nid] = env
                ok_node = bool(env.get("ok"))
                if not ok_node:
                    all_ok = False
                    emit("pipeline.node_failed",
                         {"run_id": run_id, "node": nid, "atom": n["atom"],
                          "error": env.get("error")})
                else:
                    emit("pipeline.node_done",
                         {"run_id": run_id, "node": nid, "atom": n["atom"],
                          "elapsed": env.get("elapsed")})
                summ = _node_summary(env)
                summary.append(f"{n.get('label', nid)}({n['atom']}): {summ}")
            status = "success" if all_ok else "partial"
            with _runs_lock:
                _runs[run_id].update(status=status, results=results, ok=all_ok)
            emit("pipeline.finished", {"run_id": run_id, "status": status,
                                       "ok": all_ok})
            if db:
                db.add_pipeline(name, graph, _runs[run_id], all_ok,
                                " | ".join(summary))
        except Exception as e:  # noqa: BLE001 绝不吞错
            with _runs_lock:
                _runs[run_id].update(status="failed", error=str(e), results=results,
                                     ok=False)
            emit("pipeline.failed", {"run_id": run_id, "error": str(e)})
            if db:
                db.add_pipeline(name, graph, _runs[run_id], False, str(e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"ok": True, "run_id": run_id, "status": "running"}


def _edge_value(src_env, map_kind):
    if map_kind == "envelope":
        return src_env
    if map_kind == "summary":
        return _node_summary(src_env)
    return src_env.get("data", src_env)


def _node_summary(env):
    if not env.get("ok"):
        return f"❌ {str(env.get('error'))[:120]}"
    d = env.get("data")
    if isinstance(d, dict):
        for k in ("summary", "verdict", "red_green", "count", "score"):
            if k in d:
                v = d[k]
                if isinstance(v, dict):
                    return f"✅ {k}={v.get('green', v)}"
                return f"✅ {k}={str(v)[:80]}"
        return "✅ ok"
    if isinstance(d, str) and d:
        return f"✅ {d[:80]}"
    return "✅ ok"


def get_run(run_id):
    with _runs_lock:
        r = _runs.get(run_id)
        return dict(r) if r else None