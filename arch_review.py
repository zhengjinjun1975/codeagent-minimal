#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arch_review.py — CodeAgent 架构审查核心（纯 stdlib，数据不出厂）。

架构原子 arch-review 复用的核心模块：分层 / 依赖方向 / 边界 / 攻击面清单 / 设计意图比对
(声明 vs 实现)。零第三方依赖。

能力（供 archreview 原子 provides）：
  archreview.layers    — 分层审查：识别分层 + 依赖方向(允许向下/向外, 违规向上/横向)
  archreview.boundary  — 边界审查：信任边界/输入校验边界/网络边界
  archreview.surface   — 攻击面清单：外部入口点盘点
  archreview.intent    — 设计意图比对：声明(docs/manifest/注释) vs 实现(实际代码)
"""
import ast
import os
import re
from pathlib import Path
from collections import defaultdict

# 分层识别：目录名/文件名 → 层
LAYER_MAP = [
    ("web", "展示/入口层"), ("routes", "展示/入口层"), ("views", "展示/入口层"),
    ("controller", "控制器/调度层"), ("controllers", "控制器/调度层"), ("api", "接口层"),
    ("gateway", "接口层"), ("service", "服务层"), ("services", "服务层"),
    ("domain", "领域/业务层"), ("use_cases", "领域/业务层"), ("core", "领域/业务层"),
    ("model", "模型/数据层"), ("models", "模型/数据层"), ("repository", "数据访问层"),
    ("repositories", "数据访问层"), ("dao", "数据访问层"), ("db", "数据层"),
    ("dal", "数据层"), ("infra", "基础设施层"), ("infrastructure", "基础设施层"),
    ("utils", "工具/支撑层"), ("util", "工具/支撑层"), ("common", "工具/支撑层"),
]
LAYER_INDEX = {}   # 层名 → 序号(依赖方向基准)
for i, (pat, layer) in enumerate(LAYER_MAP):
    LAYER_INDEX.setdefault(layer, i)


def _layer_of(path: str) -> str:
    """由路径判层。未识别 → '未分层'。"""
    p = Path(path)
    segs = [s.lower() for s in p.parts] + [p.stem.lower()]
    for pat, layer in LAYER_MAP:
        if any(pat == s or s.startswith(pat + "_") for s in segs):
            return layer
    return "未分层"


def _imports_of(path: str) -> list:
    """解析文件 import 的模块名列表。"""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError):
        return []
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.append(node.module)
    return mods


def _collect_py(target: str) -> list:
    p = Path(target)
    if p.is_file():
        return [str(p)] if p.suffix == ".py" else []
    return [str(f) for f in sorted(p.rglob("*.py"))
            if ".venv" not in str(f) and "node_modules" not in str(f)]


# ── 分层 + 依赖方向 ──
def layered_analysis(target: str) -> dict:
    """分层审查：为每个文件判层，构建 import 依赖边，检测违规依赖方向。

    规则：依赖只允许 向下(高序号→低序号) 或 平级；同层内平级允许；向上(低→高)或
    跨层跳跃违反正向。'未分层'不参与方向判断(仅提示)。
    """
    files = _collect_py(target)
    file_layer = {f: _layer_of(f) for f in files}
    edges = []      # (from_file, to_module, from_layer, to_layer, ok, reason)
    violations = []
    by_layer = defaultdict(list)
    for f, layer in file_layer.items():
        by_layer[layer].append(f)
    for f in files:
        fl = file_layer[f]
        fi = LAYER_INDEX.get(fl, -1)
        for mod in _imports_of(f):
            to_file = _resolve_import(f, mod)
            if not to_file:
                continue
            tl = file_layer.get(to_file, "未分层")
            if tl == "未分层" or fl == "未分层":
                edges.append({"from": f, "to": to_file, "from_layer": fl, "to_layer": tl,
                              "ok": True, "reason": "含未分层模块,方向不判"})
                continue
            ti = LAYER_INDEX.get(tl, -1)
            # 允许：目标层序号 <= 源层序号（向下/同层）; 违规：目标层更深(向上) 
            if fl == tl:
                ok, reason = True, "同层依赖(允许)"
            elif ti < fi:
                ok, reason = True, f"向下依赖({tl}→{fl}, 允许)"
            else:
                ok, reason = False, f"向上依赖({fl}→{tl}, 违规: 依赖更深层)"
            e = {"from": f, "to": to_file, "from_layer": fl, "to_layer": tl,
                 "ok": ok, "reason": reason}
            edges.append(e)
            if not ok:
                violations.append(e)
    return {"files": len(files), "by_layer": dict(by_layer),
            "layers": sorted({v for v in LAYER_INDEX} & set(file_layer.values())),
            "edges": edges, "violations": violations, "violation_count": len(violations),
            "summary": f"分层审查 {len(files)} 文件, {len(edges)} 依赖边, "
                       f"违规依赖方向 {len(violations)} 条"}


def _resolve_import(from_file: str, mod: str) -> str:
    """把 import 模块名解析为同仓文件路径（仅同项目相对解析，尽力而为）。"""
    base = Path(from_file).parent
    parts = mod.split(".")
    # 在文件自身目录及其上级目录（最多3层）内尝试解析，覆盖同包/兄弟顶层包
    for root in [base] + [base.parents[i] for i in range(min(3, len(base.parents)))]:
        # 包: service/logic.py 或 service/logic/__init__.py
        for n in range(len(parts), 0, -1):
            pkg_mod = root / Path(*parts[:n])
            pkg_init = pkg_mod / "__init__.py"
            if pkg_init.exists():
                return str(pkg_init)
            if n == len(parts):
                f = root / Path(*parts).with_suffix(".py")
                if f.exists():
                    return str(f)
    return ""


# ── 边界审查 ──
def boundary_analysis(target: str) -> dict:
    """边界审查：识别信任边界（输入/网络/文件/命令）与边界防护（校验/净化/鉴权）。"""
    files = _collect_py(target)
    boundaries = []
    total_entries = 0
    for f in files:
        content = Path(f).read_text(encoding="utf-8", errors="ignore")
        b = _file_boundaries(content, f)
        total_entries += len(b.get("entries", []))
        if b["boundaries"]:
            boundaries.append(b)
    return {"files": len(files), "boundaries": boundaries, "entry_total": total_entries,
            "summary": f"边界审查 {len(files)} 文件, 边界 {len(boundaries)} 处, "
                       f"外部入口 {total_entries} 个"}


_INPUT_ENTRY = re.compile(r"input\(|request\.(args|form|json|cookies|headers)|"
                          r"sys\.argv|os\.environ|flask\.request|get_param\(")
_VALIDATION = re.compile(r"isinstance\(|sanitize|validate|escape|\bint\(|\bfloat\(|"
                         r"^\s*if\s+.*(None|==|len\(|"")|白名单|allowlist|whitelist|"
                         r"re\.(match|fullmatch|search)\(|Param\(")
_NETWORK = re.compile(r"requests\.|urlopen|httpx|socket\.|Flask\(|FastAPI\(|"
                      r"@app\.route|@app\.get|@app\.post|@router\.|handler\(")
_FILE = re.compile(r"\bopen\s*\(|Path\s*\(|read_text|write_text|os\.remove|os\.rename")
_CMD = re.compile(r"os\.system|os\.popen|subprocess\.|eval\(|exec\(|pickle\.loads|yaml\.load")


def _file_boundaries(content: str, file: str) -> dict:
    lines = content.split("\n")
    boundaries = []
    entries, validations = [], []
    for i, line in enumerate(lines):
        line_no = i + 1
        if _INPUT_ENTRY.search(line):
            entries.append({"line": line_no, "kind": "外部输入入口",
                            "text": line.strip()[:70]})
        if _VALIDATION.search(line):
            validations.append({"line": line_no, "kind": "校验/净化", "text": line.strip()[:70]})
    for kind, pat in (("网络边界", _NETWORK), ("文件边界", _FILE), ("命令/执行边界", _CMD)):
        if pat.search(content):
            boundaries.append({"kind": kind, "in_file": file,
                               "desc": f"检测到{kind}，属攻击面需防护"})
    if entries:
        boundaries.append({"kind": "输入信任边界", "in_file": file,
                           "entries": len(entries),
                           "validation_count": len(validations),
                           "desc": f"{len(entries)} 处外部输入, {len(validations)} 处校验/净化"})
    return {"file": file, "entries": entries, "validations": validations,
            "boundaries": boundaries}


# ── 攻击面清单 ──
def attack_surface_inventory(target: str) -> dict:
    """攻击面清单：盘点外部可达入口(网络端点/输入/命令/文件/反序列化)。"""
    files = _collect_py(target)
    inventory = []
    for f in files:
        content = Path(f).read_text(encoding="utf-8", errors="ignore")
        inv = _file_attack_surface(content, f)
        if inv["items"]:
            inventory.append(inv)
    total = sum(i["count"] for i in inventory)
    return {"files": len(files), "inventory": inventory, "total": total,
            "summary": f"攻击面清单: {total} 项入口/危险操作, 覆盖 {len(inventory)} 文件"}


def _file_attack_surface(content: str, file: str) -> dict:
    lines = content.split("\n")
    items = []
    for i, line in enumerate(lines):
        ln = i + 1
        if re.search(r"@(app|router)\.(route|get|post|put|delete|patch)\s*\(", line):
            items.append({"line": ln, "type": "网络端点", "surface": "外部可访问",
                          "text": line.strip()[:70]})
        elif _NETWORK.search(line) and "route" not in line:
            items.append({"line": ln, "type": "网络/请求", "surface": "外部可达",
                          "text": line.strip()[:70]})
        elif _INPUT_ENTRY.search(line):
            items.append({"line": ln, "type": "输入入口", "surface": "外部可控",
                          "text": line.strip()[:70]})
        elif re.search(r"\bdef\s+[a-z_]+\([^)]*(request|user_input|data|payload|content)[^)]*\)", line):
            items.append({"line": ln, "type": "输入处理函数", "surface": "外部调用",
                          "text": line.strip()[:70]})
    return {"file": file, "items": items, "count": len(items)}


# ── 设计意图比对（声明 vs 实现）──
def design_intent_compare(target: str) -> dict:
    """设计意图比对：从声明源(README/docs/注释/manifest)提取设计声明，与实际代码比对。

    检查项：
      - 声明的模块/能力是否在代码中存在（声明了却实现缺失 → 断链/空壳）
      - 代码中实际提供的函数/能力是否在声明中列出（实现超过声明 → 文档滞后）
    """
    p = Path(target)
    if p.is_file():
        files = [p]
        docs = []
    else:
        files = [f for f in p.rglob("*.py") if ".venv" not in str(f) and "node_modules" not in str(f)]
        docs = [f for f in p.rglob("*.md") if ".venv" not in str(f)]
    declared = []
    for doc in docs:
        try:
            declared += _extract_declared(doc.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    implemented = set()
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                implemented.add(node.name)
    declared_terms = set()
    for d in declared:
        for tok in re.split(r"[^A-Za-z0-9_\.\-]+", d["text"]):
            if re.match(r"^[a-z][a-z0-9_]{2,}$", tok):
                declared_terms.add(tok.lower())
    # 比对：声明术语是否在实现/文档中出现（实现中函数名、模块名命中即认为已实现）
    missing, present = [], []
    for d in declared:
        text = d["text"].lower()
        hit = any((term.lower() in text) or (term.lower() in {x.lower() for x in implemented})
                  for term in declared_terms)
        if hit:
            present.append(d)
        else:
            missing.append(d)
    return {"declared_count": len(declared), "implemented_count": len(implemented),
            "implemented": sorted(implemented),
            "declared": declared[:50], "present": present, "missing": missing,
            "missing_count": len(missing),
            "summary": f"设计意图比对: 声明 {len(declared)} 项, 未在实现/文档中命中 "
                       f"{len(missing)} 项(可能断链/空壳)"}


def _extract_declared(doc: str) -> list:
    """从文档提取设计声明项：功能点/能力名/模块名。启发式。"""
    items = []
    # 标题行、列表项、能力描述
    for line in doc.split("\n"):
        s = line.strip()
        if not s or s.startswith(("#", "<!--", "```")):
            continue
        if re.match(r"^[-*]\s+", s) and not s.lower().startswith(("- 待", "- todo", "- [ ]")):
            items.append({"type": "bullet", "text": s[1:].strip()[:80]})
        elif re.match(r"^[#]+\s", s):
            items.append({"type": "heading", "text": s.lstrip("#").strip()[:80]})
    return items


# ── CLI 自测 ──
def main(argv=None):
    import argparse, json
    ap = argparse.ArgumentParser(description="arch_review 核心自测")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--capability", default="layers",
                    choices=["layers", "boundary", "surface", "intent"])
    a = ap.parse_args(argv)
    r = {"layers": layered_analysis, "boundary": boundary_analysis,
         "surface": attack_surface_inventory,
         "intent": design_intent_compare}[a.capability](a.target)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
