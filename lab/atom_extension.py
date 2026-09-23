#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atom_extension.py — 新原子开发 + 挂接 + 注册（未来扩展机制，加壳不改核心）。

流程（manifest → 注册 → 挂接）：
  1. scaffold(name, domain, provides, inputs, description)
     → 生成 lab/extensions/<name>/ {manifest.json, main.py(原子壳模板, run_cli)}
  2. register(name)
     → 校验（复用核心算法规则的校验：name==目录/entry存在/provides非空/能力冲突检测）
     → 写入 lab/extensions_registry.json（lab 侧注册表，核心 registry.json 不动）
     → 发事件 atom.registered → 前端调色板即时可见（事件驱动无死角）
  3. hook 挂接 = 注册即挂接：runner 对 ext 原子统一 subprocess main.py --capability 执行
  4. unregister(name) → 从注册表移除（文件保留可重新注册；enable=false 语义）
  5. sync_core(name, confirm) → 显式确认后才写入核心库（agents/<domain>/<name> +
     registry.json 重建，走核心既有 agent_loader.build_registry 机制，默认关闭）

铁律：默认路径绝不改核心；写核心仅限用户显式 confirm；所有异常降级诚实报告。
"""
import json
import os
import subprocess
import sys

from lab_config import get_config

_MANIFEST_TEMPLATE = {
    "name": "{name}",
    "version": "0.1.0",
    "domain": "{domain}",
    "description": "{description}",
    "open_source": True,
    "agent_class": "{Class}Agent",
    "entry": "main.py",
    "provides": ["{cap}"],
    "depends_on": [],
    "inputs": ["{inputs}"],
    "outputs": ["result", "summary"],
}

_MAIN_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""{name} 原子壳（lab 扩展原子，加壳不改核心，纯 stdlib）。

能力:
  {cap} — {description}
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODEAGENT_ROOT = {codeagent_root_repr}
if CODEAGENT_ROOT and CODEAGENT_ROOT not in sys.path:
    sys.path.insert(0, CODEAGENT_ROOT)

from atomic_base import AtomicAgent


class {Class}Agent(AtomicAgent):
    name = "{name}"
    version = "0.1.0"
    domain = "{domain}"
    description = "{description}"
    provides = ["{cap}"]
    depends_on = []
    inputs = [{inputs_repr}]
    outputs = ["result", "summary"]

    def _register_defaults(self):
        self.register("{cap}", self._run)

    def _run(self, {inputs_kw}):
        """在此实现原子逻辑（纯 stdlib，数据不出私域）。"""
        return {{"result": {inputs_kw_first}, "summary": "收到 {{}} 个入参".format(len([x for x in [{inputs_kw_first}] if x is not None]))}}


agent = {Class}Agent()

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli({Class}Agent(), run_args={{
        "capability": {{"default": "{cap}", "choices": ["{cap}"]}},
        {inputs_run_args}
    }}))
'''


def _class_name(name):
    return "".join(p.capitalize() for p in name.replace("-", "_").split("_")) or "Lab"


def scaffold(name, domain=None, capability=None, inputs=None, description="",
             cfg=None):
    """生成扩展原子骨架。返回 {ok, dir, files} 或 {ok:false, error}。"""
    cfg = cfg or get_config()
    name = str(name or "").strip()
    cap = str(capability or "").strip()
    if not name or not cap:
        return {"ok": False, "error": "name 与 capability 必填"}
    if not re_safe(name) or not re_safe(cap):
        return {"ok": False, "error": "name/capability 仅允许字母数字中划线点"}
    a_dir = os.path.join(cfg.get("extensions_dir"), name)
    if os.path.exists(a_dir):
        return {"ok": False, "error": f"扩展目录已存在: {a_dir}"}
    inputs = [str(i).strip() for i in (inputs or []) if str(i).strip()] or ["input"]
    if not inputs[0].isidentifier():
        return {"ok": False, "error": f"首个入参名非法(需 Python 标识符): {inputs[0]}"}
    domain = str(domain or "generic").strip() or "generic"
    if not domain.isidentifier():
        return {"ok": False, "error": "domain 需为 Python 标识符"}
    cls = _class_name(name)
    m = dict(_MANIFEST_TEMPLATE)
    m["name"] = name
    m["domain"] = domain
    m["description"] = description or f"{cap} 能力扩展原子"
    m["provides"] = [cap]
    m["inputs"] = inputs
    entries = ", ".join(f'"{i}"' for i in m["inputs"])
    kwargs = ", ".join(f"{i}=None" for i in m["inputs"])
    first = m["inputs"][0]
    codeagent_root = cfg.codeagent_root()
    code = _MAIN_TEMPLATE.format(
        name=name, cap=cap, description=description or f"{cap} 能力扩展原子",
        domain=domain, Class=cls, inputs_repr="[" + entries + "]",
        inputs_kw=kwargs, inputs_kw_first=first,
        inputs_run_args=", ".join(f'"{i}": {{}}' for i in m["inputs"]),
        codeagent_root_repr=json.dumps(codeagent_root))
    os.makedirs(a_dir, exist_ok=True)
    with open(os.path.join(a_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    with open(os.path.join(a_dir, "main.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    return {"ok": True, "dir": a_dir, "name": name,
            "files": ["manifest.json", "main.py"], "capability": cap}


def re_safe(s):
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", s))


def validate(name, cfg=None):
    """复用核心 manifest 规则校验扩展原子 → (ok, errors)。"""
    cfg = cfg or get_config()
    a_dir = os.path.join(cfg.get("extensions_dir"), name)
    mpath = os.path.join(a_dir, "manifest.json")
    errors = []
    if not os.path.isfile(mpath):
        return False, ["缺 manifest.json"]
    try:
        with open(mpath, encoding="utf-8") as f:
            m = json.load(f)
    except Exception as e:  # noqa: BLE001
        return False, [f"manifest 解析失败: {e}"]
    if not isinstance(m, dict):
        return False, ["manifest 非对象"]
    if m.get("name") != name:
        errors.append(f"name({m.get('name')}) != 目录名({name})")
    if not m.get("entry") or not os.path.isfile(os.path.join(a_dir, m["entry"])):
        errors.append(f"entry 缺失或不存在: {m.get('entry')}")
    if not isinstance(m.get("provides"), list) or not m.get("provides"):
        errors.append("provides 必须为非空 list")
    if not isinstance(m.get("depends_on"), list):
        errors.append("depends_on 必须为 list")
    # 能力冲突
    from atom_loader_ext import merged_registry
    try:
        reg = merged_registry(cfg.codeagent_root()) if cfg else None
        if reg:
            for a in reg["atoms"]:
                if a["name"] == name:
                    continue
                clash = set(a["provides"]) & set(m.get("provides", []))
                if clash:
                    errors.append(f"能力冲突(与 {a['name']} 重复): {sorted(clash)}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"能力冲突检测失败: {e}")
    return (not errors), errors


def register(name, cfg=None, events=None, db=None):
    """注册（lab 侧）：校验通过 → 写入 extensions_registry.json → 事件。"""
    cfg = cfg or get_config()
    ok, errors = validate(name, cfg)
    if not ok:
        if events:
            events.emit("atom.register_failed", {"name": name, "errors": errors})
        return {"ok": False, "errors": errors}
    reg_path = cfg.get("extensions_registry")
    reg = {"order": [], "enabled": {}}
    if os.path.isfile(reg_path):
        try:
            with open(reg_path, encoding="utf-8") as f:
                reg = json.load(f)
        except Exception:  # noqa: BLE001
            reg = {"order": [], "enabled": {}}
    reg.setdefault("order", [])
    reg.setdefault("enabled", {})
    if name not in reg["order"]:
        reg["order"].append(name)
    reg["enabled"][name] = True
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    tmp = reg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, reg_path)
    if events:
        events.emit("atom.registered", {"name": name, "origin": "ext"})
    return {"ok": True, "name": name, "registered": True}


def set_enabled(name, enabled, cfg=None, events=None):
    """启用/停用扩展原子（P2-6）：写入注册表 enabled 映射，事件驱动即时过滤调色板。

    enabled=false 的扩展原子从 /api/atoms 视图中过滤（调色板不再显示，也不再可运行）。
    """
    cfg = cfg or get_config()
    reg_path = cfg.get("extensions_registry")
    reg = {"order": [], "enabled": {}}
    if os.path.isfile(reg_path):
        try:
            with open(reg_path, encoding="utf-8") as f:
                reg = json.load(f)
        except Exception:  # noqa: BLE001
            reg = {"order": [], "enabled": {}}
    reg.setdefault("order", [])
    reg.setdefault("enabled", {})
    if name not in reg["order"]:
        reg["order"].append(name)
    reg["enabled"][name] = bool(enabled)
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    tmp = reg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, reg_path)
    if events:
        events.emit("atom.enabled", {"name": name, "enabled": bool(enabled)})
    return {"ok": True, "name": name, "enabled": bool(enabled)}


def unregister(name, cfg=None, events=None):
    """从注册表移除（目录保留）。"""
    cfg = cfg or get_config()
    reg_path = cfg.get("extensions_registry")
    if not os.path.isfile(reg_path):
        return {"ok": False, "error": "扩展注册表不存在"}
    with open(reg_path, encoding="utf-8") as f:
        reg = json.load(f)
    reg.setdefault("order", [])
    reg.setdefault("enabled", {})
    if name in reg["order"]:
        reg["order"].remove(name)
    reg["enabled"].pop(name, None)
    tmp = reg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, reg_path)
    if events:
        events.emit("atom.unregistered", {"name": name})
    return {"ok": True, "name": name}


def sync_core(name, confirm=False, cfg=None, events=None):
    """显式确认后写入核心库（agents/<domain>/<name> + registry.json 重建）。

    这是「新增原子正式进入 36 原子库」的标准动作；核心代码零改动，
    仅新增目录 + 经 agent_loader.build_registry 重建索引（既有机制）。
    """
    cfg = cfg or get_config()
    if not confirm:
        return {"ok": False, "error": "需显式 confirm=true 才写入核心库（加壳不改核心铁律）"}
    a_dir = os.path.join(cfg.get("extensions_dir"), name)
    if not os.path.isfile(os.path.join(a_dir, "manifest.json")):
        return {"ok": False, "error": f"扩展原子不存在: {name}"}
    with open(os.path.join(a_dir, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    domain = m.get("domain", "generic")
    dest = os.path.join(cfg.codeagent_root(), "agents", domain, name)
    if os.path.exists(dest):
        return {"ok": False, "error": f"核心库已有同路径原子: {dest}"}
    import shutil
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copytree(a_dir, dest)
    # 重建 registry（核心既有机制，只读调用其算法）
    try:
        if cfg.codeagent_root() not in sys.path:
            sys.path.insert(0, cfg.codeagent_root())
        import agent_loader as al
        res = al.build_registry()
        if not res["ok"]:
            shutil.rmtree(dest, ignore_errors=True)
            return {"ok": False, "error": f"registry 重建失败: {res['error']}"}
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "error": f"registry 重建异常: {type(e).__name__}: {e}"}
    if events:
        events.emit("atom.synced_core", {"name": name, "dest": dest})
    return {"ok": True, "name": name, "dest": dest}


def self_check(cfg=None, events=None):
    """原子化自检：扩展原子区（挂接/注册/可执行三态），供原子库页展示。"""
    cfg = cfg or get_config()
    ext_dir = cfg.get("extensions_dir")
    out = []
    if os.path.isdir(ext_dir):
        for entry in sorted(os.scandir(ext_dir), key=lambda e: e.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            mpath = os.path.join(entry.path, "manifest.json")
            if not os.path.isfile(mpath):
                continue
            state = {"name": entry.name, "manifest": os.path.isfile(mpath),
                     "entry": os.path.isfile(os.path.join(entry.path, "main.py")),
                     "registered": False}
            reg_path = cfg.get("extensions_registry")
            if os.path.isfile(reg_path):
                try:
                    with open(reg_path, encoding="utf-8") as f:
                        reg = json.load(f)
                    state["registered"] = entry.name in (reg.get("order") or [])
                except Exception:  # noqa: BLE001
                    pass
            out.append(state)
    return {"ok": True, "extensions": out}