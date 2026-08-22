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


def git_changed_py(repo_root=".") -> list:
    """用 git diff 找出改动/新增/删除的 .py 文件（智能测试选择的数据源）。

    回退链：git diff --name-only HEAD → 若失败（非 git 仓库）返回 []。
    只返回存在且 .py 结尾的文件（删除的不再需要测试）。
    返回 [绝对路径]。
    """
    import subprocess as sp
    changed = []
    try:
        base = os.path.abspath(repo_root)
        r = sp.run(["git", "-C", base, "diff", "--name-only", "HEAD"],
                   capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if not line.endswith(".py"):
                    continue
                p = os.path.join(base, line)
                if os.path.isfile(p):
                    changed.append(p)
        # 未跟踪的新文件（git diff --name-only 不含未跟踪）
        r2 = sp.run(["git", "-C", base, "ls-files", "--others", "--exclude-standard"],
                    capture_output=True, text=True, timeout=15)
        if r2.returncode == 0:
            for line in (r2.stdout or "").splitlines():
                line = line.strip()
                if line.endswith(".py"):
                    p = os.path.join(base, line)
                    if os.path.isfile(p):
                        changed.append(p)
    except Exception:
        return []
    # 去重保持顺序
    seen, out = set(), []
    for c in changed:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def select_affected_tests_git(project_root=".", test_map=None, transitive=True) -> dict:
    """git diff → 受影响测试选择（P0-3 智能测试选择）。

    自动用 git 分析改动文件，再走依赖图影响选择，只返回需要重跑的测试。
    无改动 → 返回全部(all_tests=True, 提示全量)；无 git → 退化全量。
    返回 select_affected_tests 的结构 + git 上下文。
    """
    changed = git_changed_py(project_root)
    if not changed:
        return {"affected_tests": [], "affected_modules": [],
                "rationale": "git 无改动 .py 文件（或非 git 仓库）→ 建议全量回归",
                "all_tests": True, "git_changed": [], "source": "git"}
    r = select_affected_tests(changed, project_root=project_root,
                              test_map=test_map, transitive=transitive)
    r["git_changed"] = [os.path.basename(c) for c in changed]
    r["source"] = "git"
    r["rationale"] = (f"git 改动 {len(changed)} 文件 → 影响 {len(r['affected_modules'])} 模块 "
                      f"→ 需重跑 {len(r['affected_tests'])} 测试（省时，非全量）")
    return r


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
    module_deps = graph.get("module_deps", {})  # mod(依赖方) → set(被依赖模块名)
    # 模块名(不含路径后缀) → 文件路径 映射（用于模块级传递影响解析）
    mod_to_file = {}
    for e, meta in entities.items():
        f = meta.get("file")
        m = meta.get("module")
        if f and m:
            mod_to_file.setdefault(m, f)
    # 受影响的模块：直接改 + 反向可达（谁 import 了被改模块）
    affected = set(os.path.abspath(c) for c in changed)
    changed_stems = {os.path.splitext(os.path.basename(c))[0] for c in changed}
    if transitive:
        # 模块级传递：被改模块 stem 出现在谁 module_deps → 该依赖方受影响
        for _ in range(10):  # 收敛
            new_stems = set()
            for depender, dep_on in module_deps.items():
                if dep_on & changed_stems:
                    new_stems.add(depender)
            added = False
            for stem in new_stems:
                fp = mod_to_file.get(stem)
                if fp and os.path.abspath(fp) not in affected:
                    affected.add(os.path.abspath(fp))
                    changed_stems.add(stem)
                    added = True
            if not added:
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
