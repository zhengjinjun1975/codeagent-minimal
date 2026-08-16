#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reg_guard.py — 回归快照守护（reg-guard）+ 依赖图影响测试选择（纯标准库零依赖）。

借鉴（不复制）pytest-regressions / approval-testing 的**思想**：
- 回归快照：跑测试时记录「函数的可观察输出」（返回值快照），下次跑时比对，
  不一致 → 回归告警（防「能跑通但不生效」的静默行为漂移）。
- 依赖图影响测试选择：改文件 X → 用 dep_audit 依赖图算出受影响的模块集合，
  只重跑受影响模块的测试（增量回归，省时）。

能力：
- snapshot(path, func, args) → {hash, value, file}：记录/比对函数输出快照
- save_snapshot / compare_snapshot / snapshot_store
- select_affected_tests(targets, project_root, test_map) → {affected_tests, rationale}

数据落盘 `.codeagent/snapshots/*.json`（可被 git 追踪作回归基线）。
"""

import ast
import hashlib
import json
import os
import sys
import tempfile

try:
    import dep_audit as _da
except Exception:
    _da = None

DEFAULT_SNAP_DIR = ".codeagent/snapshots"


def _snap_path(snap_dir, path, func):
    return os.path.join(snap_dir, f"{os.path.basename(path)}.{func}.snap.json")


def _value_hash(value):
    return hashlib.sha256(repr(value).encode("utf-8", "ignore")).hexdigest()[:16]


def _norm(value):
    """规范化快照值：可序列化 + 可比较。"""
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)


def snapshot(target, func, args, snapshot_dir=DEFAULT_SNAP_DIR, store=True) -> dict:
    """记录函数输出快照（首次）或比对（已存在）。

    返回 {func, args, value, hash, existing, match, changed, file, baseline}。
    changed=True 表示相对基线变化（回归信号）。"""
    mod = _load_safe(target)
    if mod is None:
        return {"func": func, "ok": False, "error": "模块加载失败"}
    fn = getattr(mod, func, None)
    if not callable(fn):
        return {"func": func, "ok": False, "error": "函数不可调用"}
    try:
        import inspect
        if inspect.iscoroutinefunction(fn):
            import asyncio
            value = asyncio.run(fn(*args))
        else:
            value = fn(*args)
    except Exception as e:
        return {"func": func, "args": args, "ok": False, "error": f"{type(e).__name__}: {e}"}
    value = _norm(value)
    vh = _value_hash(value)
    path = _snap_path(snapshot_dir, target, func)
    baseline = None
    existing = os.path.exists(path)
    match = None
    changed = False
    if existing:
        try:
            baseline = json.load(open(path, encoding="utf-8"))
            match = baseline.get("hash") == vh
            changed = not match
        except Exception:
            baseline = None
            changed = True
    if store:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"func": func, "args": _short(args), "value": value, "hash": vh,
                   "when": __import__("datetime").datetime.now().isoformat()},
                  open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"func": func, "args": _short(args), "value": value, "hash": vh,
            "file": path, "baseline": baseline, "existing": existing,
            "match": match, "changed": changed,
            "ok": (match is None) or match,
            "detail": ("新建基线" if not existing else
                       ("基线一致" if match else "⚠️ 回归：输出相对基线变化"))}


def _short(args):
    return [repr(a)[:30] for a in args]


def _load_safe(path):
    import importlib.util
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name + "_snap", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        sys.argv = [path]
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def snapshot_store(target, funcs, args_by_func=None, snapshot_dir=DEFAULT_SNAP_DIR) -> dict:
    """批量建/比快照。funcs: [函数名]; args_by_func: {func:[args]}; 缺省用 [] 无参。"""
    results = {}
    for f in funcs:
        args = (args_by_func or {}).get(f, [])
        results[f] = snapshot(target, f, args, snapshot_dir=snapshot_dir)
    return {"target": target, "results": results, "count": len(results),
            "changed": [f for f, r in results.items() if r.get("changed")],
            "summary": f"快照 {len(results)} 函数，回归变化 {sum(1 for r in results.values() if r.get('changed'))}"}


# ── 依赖图影响测试选择（增量回归）────────────
def select_affected_tests(changed_files, project_root=".", test_map=None,
                          transitive=True) -> dict:
    """用 dep_audit 依赖图，算出「改这些文件 → 哪些模块受影响 → 选哪些测试」。

    参数：
      changed_files: [改动的 .py 文件路径]
      project_root: 依赖图根
      test_map: {测试文件路径: [该测试覆盖的模块路径]}；缺省推断
                （测试名 test_X → 目标 X）
      transitive: 是否含传递影响（改 A → 影响 import A 的 B → 影响 B 的测试）
    返回 {affected_modules, affected_tests, rationale, all_tests}。
    """
    if _da is None:
        return {"affected_tests": [], "affected_modules": [],
                "rationale": "dep_audit 不可用，无法做影响选择（退化=全量）",
                "all_tests": True}
    changed = [os.path.abspath(c) for c in changed_files if c.endswith(".py")]
    if not changed:
        return {"affected_tests": [], "affected_modules": [],
                "rationale": "无改动 .py 文件", "all_tests": False}
    # 建依赖图
    try:
        graph = _da.build_graph([project_root] if os.path.isdir(project_root) else changed)
    except Exception:
        graph = {"entities": {}, "edges": []}
    entities = graph.get("entities", {})
    # 找受影响的模块：直接改 + 反向可达（谁 import 了被改模块）
    affected = set(os.path.abspath(c) for c in changed)
    if transitive:
        changed_keys = set(changed)
        for _ in range(10):  # 收敛
            new = set()
            for e, meta in entities.items():
                deps = meta.get("imports", []) if isinstance(meta, dict) else []
                if any(os.path.abspath(d) in changed_keys for d in deps) or \
                        any(d in affected for d in deps):
                    new.add(e)
            before = len(affected)
            affected |= new
            changed_keys |= new
            if len(affected) == before:
                break
    affected = {a for a in affected if a.endswith(".py")}

    # 测试映射推断：test_X.py 覆盖 X.py
    test_map = test_map or {}
    tests = []
    if os.path.isdir(project_root):
        for root, _d, files in os.walk(project_root):
            for f in files:
                if (f.startswith("test_") or f.endswith("_test.py")) and f.endswith(".py"):
                    p = os.path.abspath(os.path.join(root, f))
                    tests.append(p)
    elif os.path.isfile(project_root):
        tests = [os.path.abspath(project_root)] if "test" in os.path.basename(project_root) else []
    # 选测试：显式 test_map 命中受影响模块，或测试名含受影响模块名
    affected_bases = {os.path.splitext(os.path.basename(a))[0] for a in affected}
    affected_tests = []
    for t in tests:
        tb = os.path.splitext(os.path.basename(t))[0]
        covered = test_map.get(t, [tb.replace("test_", "").replace("_test", "")])
        covered_bases = {os.path.splitext(os.path.basename(c))[0] for c in covered}
        if affected_bases & covered_bases or (tb.replace("test_", "")) in affected_bases:
            affected_tests.append(t)
    return {"affected_modules": sorted(affected),
            "affected_tests": affected_tests,
            "rationale": f"改 {len(changed)} 文件 → 影响 {len(affected)} 模块 → 需重跑 {len(affected_tests)} 测试",
            "all_tests": not affected_tests,
            "total_tests": len(tests)}


# ── CLI ─────────────────────────────────────
def main():
    import argparse, json
    ap = argparse.ArgumentParser(description="reg_guard: 回归快照 + 依赖图影响测试选择(纯stdlib)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="函数输出快照(首次建基线, 再次比对)")
    s.add_argument("target"); s.add_argument("func")
    s.add_argument("--args", default="[]", help="调用参数 JSON")
    s.add_argument("--dir", default=DEFAULT_SNAP_DIR)
    i = sub.add_parser("impact", help="依赖图影响测试选择")
    i.add_argument("files", nargs="+")
    i.add_argument("--root", default=".")
    args = ap.parse_args()
    if args.cmd == "snapshot":
        import ast as _ast
        try:
            cargs = json.loads(args.args)
        except Exception:
            cargs = []
        print(json.dumps(snapshot(args.target, args.func, cargs, snapshot_dir=args.dir),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "impact":
        print(json.dumps(select_affected_tests(args.files, project_root=args.root),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
