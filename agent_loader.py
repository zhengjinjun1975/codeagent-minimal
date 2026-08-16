#!/usr/bin/env python3
"""agent_loader.py — CodeAgent 原子加载器（开源侧，无第三方依赖）。

职责：
1. manifest.json schema 校验（name==目录 / version 存在 / entry 存在 / 依赖声明）
2. 依赖解析：Kahn 拓扑排序（按 depends_on 编排加载顺序）
3. 冲突检测：重复 name / 重复 provides / depends_on 指向不存在或闭源能力
4. 失败降级：任何错误 → 返回 {ok:false, error, degraded:true}，绝不抛给上层
5. registry.json 索引 + scan 自动重建

铁律：loader 只读 manifest + 入口模块，不复制核心算法；依赖方向只允许
开源原子 depends_on 开源能力（闭源 orchestrator 另在闭源侧实现）。
"""

import importlib.util
import json
import os
import sys
from collections import defaultdict, deque

# ── 常量 ────────────────────────────────────────
REQUIRED_KEYS = ("name", "version", "entry")
# 依赖方向铁律：depends_on 里的能力名，必须由某个开源原子 provide
PROVIDED_INDEX = None   # 惰性构建：{能力名: 原子名}，来自 registry/scan

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.join(REPO_ROOT, "agents")
REGISTRY_PATH = os.path.join(REPO_ROOT, "registry.json")


# ── 信封工具（与 atomic_base 一致）───────────────
def _ok(data):
    return {"ok": True, "data": data}


def _fail(error, degraded=True, data=None):
    return {"ok": False, "data": data or {}, "error": error, "degraded": degraded}


# ── manifest 校验 ───────────────────────────────
def _load_manifest(agent_dir: str) -> dict:
    """读取并 schema 校验单个 manifest.json。返回 (manifest) 或抛 ValueError。"""
    mpath = os.path.join(agent_dir, "manifest.json")
    if not os.path.exists(mpath):
        raise ValueError(f"缺 manifest.json: {agent_dir}")
    with open(mpath, encoding="utf-8") as f:
        m = json.load(f)
    if not isinstance(m, dict):
        raise ValueError(f"manifest 必须是 JSON 对象: {mpath}")

    # 1) 必需字段
    for k in REQUIRED_KEYS:
        if k not in m or not m[k]:
            raise ValueError(f"manifest 缺必需字段 '{k}': {mpath}")

    # 2) name == 目录名
    dirname = os.path.basename(os.path.normpath(agent_dir))
    if m["name"] != dirname:
        raise ValueError(f"name({m['name']}) != 目录名({dirname}): {mpath}")

    # 3) version 非空字符串
    if not isinstance(m["version"], str) or not m["version"].strip():
        raise ValueError(f"version 非法: {m.get('version')} @ {mpath}")

    # 4) entry 文件存在（相对 agent_dir）
    entry_path = os.path.join(agent_dir, m["entry"])
    if not os.path.isfile(entry_path):
        raise ValueError(f"entry 不存在: {m['entry']} @ {mpath}")

    # 5) provides / depends_on 结构
    provides = m.get("provides", [])
    if not isinstance(provides, list) or not provides:
        raise ValueError(f"provides 必须为非空 list: {mpath}")
    depends = m.get("depends_on", [])
    if not isinstance(depends, list):
        raise ValueError(f"depends_on 必须为 list: {mpath}")

    # 归一化
    m.setdefault("domain", "generic")
    m.setdefault("open_source", True)
    m.setdefault("description", "")
    m.setdefault("inputs", [])
    m.setdefault("outputs", [])
    return m


def _build_provided_index(manifests: dict) -> dict:
    """{能力名: 原子名}。能力名冲突时后者覆盖（同时记入冲突检测）。"""
    index = {}
    for name, m in manifests.items():
        for cap in m.get("provides", []):
            index[cap] = name
    return index


# ── 依赖解析（Kahn 拓扑排序）────────────────────
def _resolve_order(manifests: dict, provided_index: dict) -> dict:
    """Kahn 拓扑排序：按 depends_on 解析加载顺序。
    返回 (order_list, conflicts)。conflicts 为 list[str] 提示，不中断（失败降级）。"""
    names = list(manifests.keys())
    indeg = {n: 0 for n in names}
    adj = defaultdict(list)          # n → 依赖它的原子
    conflicts = []

    for n in names:
        for dep in manifests[n].get("depends_on", []):
            provider = provided_index.get(dep)
            if provider is None:
                conflicts.append(f"原子 '{n}' 依赖的能力 '{dep}' 无任何开源原子提供")
                continue
            # 自依赖 / 直接环检测（拓扑会兜底，这里先给提示）
            if provider == n:
                conflicts.append(f"原子 '{n}' 自依赖能力 '{dep}'")
                continue
            indeg[n] += 1
            adj[provider].append(n)

    q = deque([n for n in names if indeg[n] == 0])
    order = []
    while q:
        cur = q.popleft()
        order.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)

    # 剩余未入 order 的 = 环
    remaining = [n for n in names if n not in order]
    if remaining:
        conflicts.append(f"存在依赖环，无法确定顺序: {sorted(remaining)}")
        # 失败降级：环内原子仍按字典序补进队尾，保证尽量可加载
        order.extend(sorted(remaining))
    return order, conflicts


# ── 冲突检测（与解析合并）───────────────────────
def _detect_conflicts(manifests: dict, provided_index: dict) -> list:
    """额外冲突：重复 name（dict 天然去重，但这里校验重复 provides + 闭源依赖方向）。"""
    conflicts = []
    # 重复能力提供者
    cap_provider = {}
    for name, m in manifests.items():
        for cap in m.get("provides", []):
            if cap in cap_provider and cap_provider[cap] != name:
                conflicts.append(
                    f"能力 '{cap}' 被多原子提供: {cap_provider[cap]} 与 {name}（需去重）")
            else:
                cap_provider[cap] = name
    # 依赖方向铁律：开源原子 depends_on 只能指向开源能力（provided_index 已限开源）
    for name, m in manifests.items():
        for dep in m.get("depends_on", []):
            if dep not in provided_index:
                conflicts.append(f"原子 '{name}' 依赖 '{dep}' → 非开源/未提供，违反依赖方向铁律")
    return conflicts


# ── 加载入口模块并实例化原子 ───────────────────
def _instantiate(agent_dir: str, manifest: dict):
    """动态 import 入口 main.py，实例化其中的 AtomicAgent 子类。
    约定：main.py 内以模块级名称 `agent` 暴露子类实例，或子类名为 `XxxAgent`。
    优先用 manifest 里声明的 agent_class（可选）。"""
    entry = os.path.join(agent_dir, manifest["entry"])
    mod_name = f"codeagent_agent_{manifest['name']}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, entry)
    module = importlib.util.module_from_spec(spec)
    # 让入口能 import 仓库根模块（dep_audit / test_harness / atomic_base）
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    spec.loader.exec_module(module)

    # 1) manifest 指定 agent_class
    cls_name = manifest.get("agent_class")
    if cls_name:
        cls = getattr(module, cls_name, None)
        if cls is None:
            raise ValueError(f"agent_class '{cls_name}' 在 {entry} 中不存在")
        return cls(manifest)

    # 2) 模块级 `agent` 实例
    inst = getattr(module, "agent", None)
    if inst is not None and hasattr(inst, "call") and hasattr(inst, "run"):
        return inst

    # 3) 找首个 AtomicAgent 子类
    for attr in dir(module):
        obj = getattr(module, attr)
        if isinstance(obj, type):
            from atomic_base import AtomicAgent
            if issubclass(obj, AtomicAgent) and obj is not AtomicAgent:
                return obj(manifest)
    raise ValueError(f"{entry} 未导出 AtomicAgent 子类/实例")


def load_agent(agent_dir: str) -> dict:
    """加载单个原子（校验→实例化→ready）。返回 {ok, data:{agent, describe}} 信封。"""
    try:
        manifest = _load_manifest(agent_dir)
        agent = _instantiate(agent_dir, manifest)
        agent.load()
        return _ok({"agent": agent, "describe": agent.describe()})
    except Exception as e:
        return _fail(f"加载原子失败 [{os.path.basename(agent_dir)}]: {type(e).__name__}: {e}")


# ── scan / registry ────────────────────────────
def scan(agents_dir: str = AGENTS_DIR) -> dict:
    """扫描 agents_dir 下所有 manifest.json，构建 {name: manifest}。
    返回 {ok, data:{manifests, errors}}；单个坏 manifest 记 errors 不中断。"""
    manifests, errors = {}, []
    if not os.path.isdir(agents_dir):
        return _fail(f"agents 目录不存在: {agents_dir}")
    for root, dirs, files in os.walk(agents_dir):
        if "manifest.json" in files:
            rel = os.path.relpath(root, agents_dir)
            domain = rel.split(os.sep)[0] if os.sep in rel else "generic"
            try:
                m = _load_manifest(root)
                # 跨盘(Windows C:/E:)时 relpath 会抛 ValueError → 失败降级用绝对路径
                try:
                    m["path"] = os.path.relpath(root, REPO_ROOT)
                except ValueError:
                    m["path"] = root
                m.setdefault("domain", domain)
                manifests[m["name"]] = m
            except ValueError as e:
                errors.append(str(e))
    return _ok({"manifests": manifests, "errors": errors})


def load_registry(registry_path: str = REGISTRY_PATH) -> dict:
    """读取 registry.json。不存在 → 自动 scan 重建。返回 {ok, data:{agents, order, conflicts}}。"""
    if os.path.exists(registry_path):
        try:
            with open(registry_path, encoding="utf-8") as f:
                reg = json.load(f)
            manifests = reg.get("agents", {})
        except Exception:
            manifests = None
    else:
        manifests = None
    if manifests is None:
        res = scan()
        if not res["ok"]:
            return res
        manifests = res["data"]["manifests"]

    provided_index = _build_provided_index(manifests)
    conflicts = _detect_conflicts(manifests, provided_index)
    order, dep_conflicts = _resolve_order(manifests, provided_index)
    conflicts += dep_conflicts

    return _ok({"agents": manifests, "order": order, "conflicts": conflicts})


def build_registry(agents_dir: str = AGENTS_DIR,
                   registry_path: str = REGISTRY_PATH) -> dict:
    """扫描并重建 registry.json。返回 {ok, data:{registry, conflicts}}。"""
    res = scan(agents_dir)
    if not res["ok"]:
        return res
    manifests = res["data"]["manifests"]
    provided_index = _build_provided_index(manifests)
    conflicts = _detect_conflicts(manifests, provided_index)
    order, dep_conflicts = _resolve_order(manifests, provided_index)
    conflicts += dep_conflicts

    registry = {
        "schema": "codeagent-registry-v1",
        "agents_dir": os.path.relpath(agents_dir, REPO_ROOT),
        "agents": manifests,
        "order": order,
        "conflicts": conflicts,
    }
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    return _ok({"registry": registry, "conflicts": conflicts})


def load_agents(agents_dir: str = AGENTS_DIR, registry_path: str = REGISTRY_PATH) -> dict:
    """一键：读 registry → 按拓扑序加载全部原子。返回 {ok, data:{agents:{name:inst}, order, degraded:[name...]}}。"""
    res = load_registry(registry_path)
    if not res["ok"]:
        return res
    manifests = res["data"]["agents"]
    order = res["data"]["order"]
    loaded, degraded = {}, []
    for name in order:
        m = manifests.get(name)
        if not m:
            degraded.append(name)
            continue
        agent_dir = os.path.join(REPO_ROOT, m.get("path", os.path.join("agents", name)))
        r = load_agent(agent_dir)
        if r["ok"]:
            loaded[name] = r["data"]["agent"]
        else:
            degraded.append(name)
    return _ok({"agents": loaded, "order": order, "degraded": degraded,
                "conflicts": res["data"]["conflicts"]})


# ── CLI 自测入口 ────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="CodeAgent 原子加载器")
    ap.add_argument("--scan", action="store_true", help="扫描并打印发现的原子")
    ap.add_argument("--rebuild-registry", action="store_true", help="重建 registry.json")
    ap.add_argument("--load-all", action="store_true", help="加载 registry 全部原子并打印状态")
    ap.add_argument("--agent", help="指定原子目录加载单个原子")
    args = ap.parse_args()

    if args.rebuild_registry:
        r = build_registry()
        print("重建 registry:", "OK" if r["ok"] else r["error"])
        if r["ok"] and r["data"]["conflicts"]:
            print("  冲突提示:")
            for c in r["data"]["conflicts"]:
                print("   -", c)
    if args.scan:
        r = scan()
        print("扫描结果:", "OK" if r["ok"] else r["error"])
        if r["ok"]:
            for name, m in r["data"]["manifests"].items():
                print(f"  {name:<14} v{m['version']}  provides={m['provides']}  depends={m['depends_on']}")
            for e in r["data"]["errors"]:
                print("   !!", e)
    if args.agent:
        r = load_agent(args.agent)
        if r["ok"]:
            d = r["data"]["describe"]
            print(f"  加载成功: {d['name']} v{d['version']} status={d['status']}")
            print("  capabilities:", d["capabilities"])
        else:
            print("  加载失败:", r["error"])
    if args.load_all:
        r = load_agents()
        if r["ok"]:
            for name, a in r["data"]["agents"].items():
                d = a.describe()
                print(f"  [{d['status']}] {name} v{d['version']} provides={d['provides']}")
            if r["data"]["degraded"]:
                print("  降级(加载失败):", r["data"]["degraded"])
            for c in r["data"]["conflicts"]:
                print("  冲突提示:", c)
        else:
            print("  加载失败:", r["error"])
