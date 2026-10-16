#!/usr/bin/env python3
"""运行时依赖口径门：扫描运行时路径的顶层 import，对照白名单判定。"""
import ast
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

RUNTIME_FILES = [
    "agent_runtime.py", "engine_harness.py", "engine_agent_impl.py",
    "engine_agent_project.py", "engine_agent_review.py", "engine_bridge.py",
    "engine_llm.py", "engine_env.py", "engine_deliverable_guard.py",
    "hooks.py", "verifiers.py", "reward_ledger.py", "netguard.py",
    "pathguard.py", "mcp_guard.py", "approval_policy.py", "budget_gate.py",
    "reg_guard.py", "laya_dispatch.py", "cost_ledger.py", "atom_spec.py",
    "agent_loader.py", "manifest_caps.py", "codeagent.py", "legacy_cli.py",
]


def collect_targets():
    targets = []
    for name in RUNTIME_FILES:
        p = ROOT / name
        if p.is_file():
            targets.append(p)
    for mf in sorted(ROOT.glob("agents/*/manifest.json")):
        m = mf.parent / "main.py"
        if m.is_file():
            targets.append(m)
    for mf in sorted(ROOT.glob("agents/*/*/manifest.json")):
        m = mf.parent / "main.py"
        if m.is_file():
            targets.append(m)
    seen, out = set(), []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def repo_module_names():
    names = set()
    for p in ROOT.rglob("*.py"):
        names.add(p.stem)
    return names


def load_extra_allowed():
    cfg = ROOT / "config" / "runtime_deps.json"
    if not cfg.is_file():
        return None, "config/runtime_deps.json 不存在，额外允许集视为空集"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"config/runtime_deps.json 解析失败({e})，额外允许集视为空集"
    if not isinstance(data, dict):
        return None, "config/runtime_deps.json 顶层不是对象，额外允许集视为空集"
    return data, None


def top_level_imports(source, filename="<src>"):
    """返回 [(module_name, lineno)]，只算模块级 Import/ImportFrom 且 level==0。"""
    tree = ast.parse(source, filename=filename)
    found = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.append((node.module.split(".")[0], node.lineno))
    return found


def main():
    targets = collect_targets()
    stdlib = set(sys.stdlib_module_names)
    repo_mods = repo_module_names()
    extra, extra_note = load_extra_allowed()
    if extra_note:
        print(f"[提示] {extra_note}")
    extra = extra or {}

    whitelist = stdlib | repo_mods | set(extra.keys())

    total_imports = 0
    hits = 0
    undeclared = []  # (module, file, lineno)

    for path in targets:
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            imports = top_level_imports(src, str(path))
        except SyntaxError as e:
            print(f"[警告] 语法错误无法解析: {path}: {e}")
            continue
        for mod, lineno in imports:
            total_imports += 1
            if mod in whitelist:
                hits += 1
            else:
                undeclared.append((mod, path, lineno))

    print(f"被扫文件数: {len(targets)}")
    print(f"顶层 import 总数: {total_imports}")
    print(f"命中白名单: {hits}")

    c1_ok = not undeclared
    if undeclared:
        print("未声明第三方顶层 import:")
        for mod, path, lineno in undeclared:
            rel = path.relative_to(ROOT) if ROOT in path.parents else path
            print(f"  - {mod}  ({rel}:{lineno})")
    else:
        print("未声明第三方顶层 import: 无")

    c2_ok = True
    if extra_note is None:
        bad = [k for k, v in extra.items() if not (isinstance(v, str) and v.strip())]
        if bad:
            c2_ok = False
            print("C2 额外允许集缺少理由:")
            for k in bad:
                print(f"  - {k}")

    # C3 反证：内存夹具
    fixture = "import requests\n"
    detected = [m for m, _ in top_level_imports(fixture, "<fixture>")]
    c3_ok = "requests" in detected
    print(f"C3 反证: 夹具检出 {detected} -> {'✓' if c3_ok else '✗'}")

    print(f"C1 运行时路径无未声明第三方顶层 import: {'✓' if c1_ok else '✗'}")
    print(f"C2 额外允许集理由完整: {'✓' if c2_ok else '✗'}")

    ok = c1_ok and c2_ok and c3_ok
    print(f"GATE_EXIT={0 if ok else 1}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()