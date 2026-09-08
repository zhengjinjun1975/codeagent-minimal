#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lab_config.py — Lab 全局配置（纯 stdlib）。

- 默认指向: 原子库 codeagent-minimal（只读）+ 目标仓库（可切换，受白名单约束）
- 配置落盘 lab_config.json（本目录），运行数据 lab_data/（db/事件/导出/备份）
- 所有路径解析: codeagent_root 与 target_root 为显式用户配置（绝对路径放行），
  其余一切文件端点只允许 target_root 内相对路径（路径穿越防护在此收紧）。
"""
import json
import os

LAB_DIR = os.path.dirname(os.path.abspath(__file__))
# 新项目自包含: 原子库 = 本仓库自身, 独立不依赖外部目录
DEFAULT_CODEAGENT_ROOT = os.path.dirname(LAB_DIR)
CONFIG_PATH = os.path.join(LAB_DIR, "lab_config.json")
DEFAULT_CONFIG = {
    "codeagent_root": DEFAULT_CODEAGENT_ROOT,
    "target_root": DEFAULT_CODEAGENT_ROOT,
    "data_dir": os.path.join(LAB_DIR, "lab_data"),
    "host": "127.0.0.1",
    "port": 8087,
    "token": "",
    "db_path": "lab_data/lab.db",
    "report_dir": "lab_data/reports",
    "backup_dir": "lab_data/backups",
    "models_path": "lab_data/models.json",
    "extensions_dir": "extensions",
    "extensions_registry": "extensions_registry.json",
    "known_defects_extra": "lab_data/known_defects_extra.json",
}


def _defaults():
    d = dict(DEFAULT_CONFIG)
    d["db_path"] = os.path.join(LAB_DIR, d["db_path"])
    d["report_dir"] = os.path.join(LAB_DIR, d["report_dir"])
    d["backup_dir"] = os.path.join(LAB_DIR, d["backup_dir"])
    d["models_path"] = os.path.join(LAB_DIR, d["models_path"])
    d["extensions_dir"] = os.path.join(LAB_DIR, d["extensions_dir"])
    d["extensions_registry"] = os.path.join(LAB_DIR, d["extensions_registry"])
    d["known_defects_extra"] = os.path.join(LAB_DIR, d["known_defects_extra"])
    return d


class LabConfig:
    """配置对象：读取/保存 JSON；路径解析（全部相对 target_root）。"""

    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.values = _defaults()
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.values.update(loaded)
            except Exception:  # noqa: BLE001 配置损坏→用默认值并标注
                self.values["load_error"] = "配置损坏，已回退默认"
        self._absolutize()

    def _absolutize(self):
        for k in ("db_path", "report_dir", "backup_dir", "models_path",
                  "extensions_dir", "extensions_registry", "known_defects_extra"):
            v = self.values.get(k)
            if v and not os.path.isabs(v):
                self.values[k] = os.path.join(LAB_DIR, v)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.values, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ── 取值 ────────────────────────────────
    def codeagent_root(self):
        return os.path.realpath(self.values.get("codeagent_root", DEFAULT_CODEAGENT_ROOT))

    def target_root(self):
        return os.path.realpath(self.values.get("target_root", DEFAULT_CODEAGENT_ROOT))

    def set_target_root(self, path):
        """切换目标仓库（用户显式配置，绝对路径放行；目录必须存在）。"""
        real = os.path.realpath(os.path.expanduser(path))
        if not os.path.isdir(real):
            return False, f"目录不存在: {path}"
        self.values["target_root"] = real
        self.save()
        return True, real

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        self.save()

    def public(self):
        """对外安全视图（不泄露 token）。"""
        return {
            "codeagent_root": self.values.get("codeagent_root"),
            "target_root": self.values.get("target_root"),
            "data_dir": self.values.get("data_dir"),
            "host": self.values.get("host"),
            "port": self.values.get("port"),
            "has_token": bool(self.values.get("token")),
            "load_error": self.values.get("load_error"),
            "atom_root_ok": os.path.isfile(os.path.join(self.codeagent_root(), "codeagent.py")),
        }

    # ── 白名单：target_root 内相对路径解析（路径穿越防护核心）──
    def resolve_target(self, relpath):
        """把用户提供的相对路径解析为 target_root 内真实路径。

        返回 (real_abs, rel, err)。拒绝: 空、绝对路径不在 root 语义约束外、../ 逃逸、
        非 .py（源码白名单，可扩展）。所有返回路径统一正斜杠。
        """
        if not relpath:
            return None, None, "缺少路径"
        if isinstance(relpath, (list, dict)):
            return None, None, "路径必须为字符串"
        p = str(relpath).strip().replace(os.sep, "/")
        if not p or p.startswith("/") or p.startswith("\\\\") or ".." in p.split("/"):
            return None, None, f"路径越界(拒绝 ../ 与绝对路径): {relpath}"
        p = p.lstrip("./")
        real = os.path.realpath(os.path.join(self.target_root(), p))
        root = self.target_root()
        try:
            inside = os.path.commonpath([real, root]) == root
        except ValueError:
            inside = False
        if not inside:
            return None, None, f"路径越界(仅允许目标仓库内): {relpath}"
        rel = os.path.relpath(real, root).replace(os.sep, "/")
        return real, rel, None

    def resolve_target_dir(self, relpath):
        """目录解析（树遍历用）：逃逸拒绝，允许目录或文件。"""
        if not relpath:
            return self.target_root(), "", None
        p = str(relpath).strip().replace(os.sep, "/")
        if not p or p.startswith("/") or p.startswith("\\\\") or ".." in p.split("/"):
            return None, None, f"路径越界(拒绝 ../ 与绝对路径): {relpath}"
        real = os.path.realpath(os.path.join(self.target_root(), p))
        root = self.target_root()
        try:
            inside = os.path.commonpath([real, root]) == root
        except ValueError:
            inside = False
        if not inside:
            return None, None, f"路径越界: {relpath}"
        rel = os.path.relpath(real, root).replace(os.sep, "/")
        return real, rel, None


_G = None


def get_config():
    global _G
    if _G is None:
        _G = LabConfig()
    return _G


def set_config_for_tests(cfg):
    """测试专用：把单例指向测试实例（数据目录/扩展目录全部隔离到临时目录）。

    否则 merged_registry / model_config / history_db / debug_orchestrator 内部的
    get_config() 会命中磁盘默认单例：测试 scaffold 的扩展写进临时目录，
    ／api/atoms 却从真实 lab/extensions 读取（单例缓存断链）。
    """
    global _G
    _G = cfg
    return cfg