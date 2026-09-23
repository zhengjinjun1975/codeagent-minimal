#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atom_loader_ext.py — 原子注册表视图（只读核心 registry + lab 扩展 registry 合并）。

铁律（加壳不改核心）：核心 registry.json 只读；lab 扩展注册表 lab/extensions_registry.json
独立维护；能力冲突（扩展 provides 与核心重复）标记 conflict 提示不覆盖。

复用核心校验：sys.path 挂 codeagent_root 后直接调用 agent_loader._load_manifest / scan
（只读调用，零改动核心）。扩展原子用同一套 manifest schema 校验。
"""
import json
import os
import sys

from lab_config import get_config


def _insert_codeagent_root(root):
    if root and root not in sys.path:
        sys.path.insert(0, root)


def load_core_registry(codeagent_root):
    """读取核心 registry.json（只读）。失败降级 → scan(agents) 重建视图（仍只读）。"""
    reg_path = os.path.join(codeagent_root, "registry.json")
    manifests, order, conflicts = {}, [], []
    if os.path.isfile(reg_path):
        try:
            with open(reg_path, encoding="utf-8") as f:
                reg = json.load(f)
            manifests = reg.get("agents") or {}
            order = reg.get("order") or list(manifests.keys())
            conflicts = reg.get("conflicts") or []
        except Exception as e:  # noqa: BLE001
            conflicts.append(f"registry.json 读取失败: {e}")
    if not manifests:
        # 降级：直接扫描 agents 目录（只读）
        try:
            _insert_codeagent_root(codeagent_root)
            import agent_loader as al
            res = al.scan(os.path.join(codeagent_root, "agents"))
            if res["ok"]:
                manifests = res["data"]["manifests"]
                order = list(manifests.keys())
        except Exception as e:  # noqa: BLE001
            conflicts.append(f"扫描 agents 失败: {e}")
    for name, m in manifests.items():
        m["origin"] = "core"
    return manifests, order, conflicts


def load_extensions(extensions_dir, extensions_registry_path):
    """读取 lab 扩展原子。返回 (manifests, order, errors)。

    - extensions_dir 下每个 <name>/manifest.json 均可见（扫描，命名唯一）
    - extensions_registry.json 记录"已注册"顺序与 enable 标志（注册语义由 atom_extension 维护）
    """
    manifests, order, errors = {}, [], []
    # 注册顺序（若注册表损坏则按目录扫描兜底）
    registered = []
    enabled_map = {}
    if os.path.isfile(extensions_registry_path):
        try:
            with open(extensions_registry_path, encoding="utf-8") as f:
                rj = json.load(f)
            registered = rj.get("order") or []
            enabled_map = rj.get("enabled") or {}
        except Exception as e:  # noqa: BLE001
            errors.append(f"扩展注册表读取失败: {e}")
    # 扫描目录（只读）
    if os.path.isdir(extensions_dir):
        for entry in sorted(os.scandir(extensions_dir), key=lambda e: e.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            # lab-echo* 为测试/回显扩展(临时调试产物)，不进入生产原子清单，
            # 扫描阶段直接跳过目录(目录本身保留不删，仅不再加载为可见扩展原子)。
            if entry.name.startswith("lab-echo"):
                continue
            mpath = os.path.join(entry.path, "manifest.json")
            if not os.path.isfile(mpath):
                continue
            try:
                with open(mpath, encoding="utf-8") as f:
                    m = json.load(f)
                if not isinstance(m, dict):
                    errors.append(f"manifest 非对象: {entry.name}")
                    continue
                if m.get("name") != entry.name:
                    errors.append(f"name({m.get('name')}) != 目录名({entry.name})")
                    continue
                m["origin"] = "ext"
                try:
                    rel = os.path.relpath(entry.path, os.getcwd()).replace(os.sep, "/")
                except ValueError:
                    # 跨盘（扩展目录在 C: 而 cwd 在 E:）→ relpath 抛 ValueError，
                    # 降级为绝对路径展示（否则该扩展被误判为解析失败而跳过——断链根因）
                    rel = entry.path.replace(os.sep, "/")
                m["path"] = rel
                # enabled 语义：注册表 enabled 映射生效（注册时默认 true；可被 enable/disable 关闭）
                m["enabled"] = bool(enabled_map.get(m["name"], True))
                manifests[m["name"]] = m
            except Exception as e:  # noqa: BLE001
                errors.append(f"manifest 解析失败 {entry.name}: {e}")
    # 注册表里的顺序优先（未扫描到的只记错误）
    for name in registered:
        if name in manifests:
            order.append(name)
    for name in manifests:
        if name not in order:
            order.append(name)
    return manifests, order, errors


def merged_registry(codeagent_root=None, extensions_dir=None, extensions_registry_path=None):
    """合并核心 + 扩展 → {atoms:[{name,...}], count, conflicts, origins}。

    冲突检测：扩展 provides 与核心重复 → 记 conflict（原子仍可见，标注冲突）。
    """
    cfg = get_config()
    root = codeagent_root or cfg.codeagent_root()
    ext_dir = extensions_dir or cfg.get("extensions_dir")
    ext_reg = extensions_registry_path or cfg.get("extensions_registry")
    core_manifests, core_order, core_conflicts = load_core_registry(root)
    ext_manifests, ext_order, ext_errors = load_extensions(ext_dir, ext_reg)

    # 能力冲突（扩展 vs 核心 / 扩展 vs 扩展）
    cap_owner = {}
    conflicts = list(core_conflicts) + (ext_errors or [])
    for name, m in core_manifests.items():
        for cap in m.get("provides", []):
            cap_owner[cap] = name
    for name, m in ext_manifests.items():
        for cap in m.get("provides", []):
            if cap in cap_owner and cap_owner[cap] != name:
                conflicts.append(f"能力 '{cap}' 由核心[{cap_owner[cap]}]与扩展[{name}]同时提供"
                                 f"（冲突，扩展不覆盖核心）")
        for cap in m.get("provides", []):
            cap_owner[cap] = name if cap not in cap_owner else cap_owner[cap]

    atoms = []
    for name in core_order + [n for n in core_manifests if n not in core_order]:
        m = core_manifests.get(name)
        if not m:
            continue
        if not _atom_enabled(name, m):
            continue
        atoms.append(atom_view(name, m))
    for name in ext_order + [n for n in ext_manifests if n not in ext_order]:
        m = ext_manifests.get(name)
        if not m:
            continue
        if not _atom_enabled(name, m):
            continue
        atoms.append(atom_view(name, m))
    demo_count = sum(1 for a in atoms if a.get("demo"))
    # 扩展全量（含已停用，供前端「启停开关」展示与操作；调色板仍用过滤后的 atoms）
    ext_all = []
    for name in ext_order + [n for n in ext_manifests if n not in ext_order]:
        m = ext_manifests.get(name)
        if not m:
            continue
        ext_all.append(atom_view(name, m))
    return {"atoms": atoms, "count": len(atoms), "conflicts": conflicts,
            "core_count": len(core_manifests), "ext_count": len(ext_manifests),
            "demo_count": demo_count, "extensions_all": ext_all}


def atom_view(name, m):
    """对外原子视图（前端调色板数据）。"""
    inputs = list(m.get("inputs", []))
    # 兼容 echo 原子把 inputs 写成 [["input"]] 的嵌套形态
    if inputs and isinstance(inputs[0], (list, tuple)):
        inputs = [i for sub in inputs for i in (sub if isinstance(sub, (list, tuple)) else [sub])]
    return {
        "name": name,
        "domain": m.get("domain", "generic"),
        "description": m.get("description", ""),
        "version": m.get("version", ""),
        "provides": list(m.get("provides", [])),
        "depends_on": list(m.get("depends_on", [])),
        "inputs": inputs,
        "outputs": list(m.get("outputs", [])),
        "origin": m.get("origin", "core"),
        "path": m.get("path", ""),
        "ext_dir": m.get("ext_dir", ""),
        "enabled": bool(m.get("enabled", True)),
        "demo": bool(m.get("demo")) or str(name).startswith("lab-echo"),
        "heavy": bool(m.get("heavy")),
        # 结构化输入 schema（P0-2：必填/默认/类型/是否接受边注入），由 inputs 派生
        "inputs_schema": _derive_schema(inputs),
    }


# P0-2 边注入白名单：这些输入名允许承接上游边数据；其余（如 path/name/attempts 等
# 具体配置参数）视为「仅排序」连接，不把上游数据注入以免被静默丢弃/类型冲突。
_EDGE_OK_INPUTS = {
    "input", "evidence", "data", "content", "code", "task", "params", "item",
    "items", "steps", "findings", "message", "failure", "chain", "outputs",
    "condition", "outcome", "name", "tool", "list", "seed", "history", "text",
}
# 复杂参数类型 → 前端用结构化 JSON 编辑
_JSON_INPUTS = {"steps", "items", "chain", "params", "outputs", "evidence", "findings", "data"}
# 必填入参（缺了无法运行）
# 注：不含 steps —— lab-harness 的 steps 允许为空（只聚合上游 evidence），空 steps 是合法默认。
_REQUIRED_INPUTS = {"path", "chain", "task", "content", "code"}


def _derive_schema(inputs):
    """把 inputs 列表派生为 [{name, required, default, type, edge_ok}]。"""
    out = []
    for inp in inputs:
        if not isinstance(inp, str) or not inp:
            continue
        t = "json" if inp in _JSON_INPUTS else ("text" if inp in ("code", "content", "data", "failure") else "str")
        out.append({
            "name": inp,
            "required": inp in _REQUIRED_INPUTS,
            "default": "",
            "type": t,
            "edge_ok": inp in _EDGE_OK_INPUTS,
        })
    return out


def _atom_enabled(name, m):
    # 扩展原子按注册表 enabled 过滤；核心原子恒启用
    if m.get("origin") == "ext":
        return bool(m.get("enabled", True))
    return True


def find_atom(name, registry=None):
    """按名字查原子视图。"""
    reg = registry or merged_registry()
    for a in reg["atoms"]:
        if a["name"] == name:
            return a
    return None


def atom_abs_dir(atom_view, cfg=None):
    """原子的绝对目录：core → codeagent_root/agents/<domain>/<name>；ext → extensions_dir/<name>。"""
    cfg = cfg or get_config()
    if atom_view.get("origin") == "ext":
        return os.path.join(cfg.get("extensions_dir"), atom_view["name"])
    return os.path.join(cfg.codeagent_root(), "agents", atom_view.get("domain", "generic"),
                        atom_view["name"])