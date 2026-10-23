#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atoms_graph_core.py — 原子能力图谱内核（四层抽取 + 十三条规则 + 基线台账 + 渲染）

为什么要有这件：codeagent 的能力声明（registry.json）、实现（各原子 main.py）、
调用（run_capability）和组合（chain_templates.json / run_chain）是**四份各自为政的真相**。
四份之间没有任何东西对齐，于是「编排写错」只能靠运行时踩坑发现，而踩了坑也不一定看得见
—— `AtomicAgent.call` 的 `except Exception` 会把 TypeError 吞成 `{ok:false, degraded:true}`，
上层只看到"降级"，根因在日志里消失。

四层数据源：
  L0 声明层  registry.json                      provides / depends_on / inputs / outputs
  L1 实现层  agents/*/*/main.py                 AST：register() 真实挂载 + 方法签名
  L2 调用层  全仓 *.py                          AST：run_capability("x.y", ...) 调用点与实参名
  L3 组合层  chain_templates.json + 代码内链    组合 = 有序能力序列，逐步可解析

三条设计约束：
  1 **单一定义**：CI 门、`codeagent.py graph`、单测、文档生成四处都 import 本件，
    不各写一份抽取逻辑（各写一份，本地一个说法、CI 另一个说法，等于没有门）。
  2 **可追踪**：能力有 `trace_id`（原子#能力）、调用点有 `site_id`（文件:行）、
    组合有 `chain_id`。改一个能力，能反查出「哪些组合会断、哪些调用点要跟着改」。
  3 **判不了就说判不了**：取不到基线 ref 时明确返回"未判"，不假装通过。

本件只读代码、不写仓库；写盘由 scripts/verify_atoms_graph.py 决定。
"""

import ast
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

SCHEMA = "codeagent-atoms-graph-v2"
BASELINE_SCHEMA = "atoms-graph-baseline-v1"
ENTRIES_SCHEMA = "codeagent-atoms-entries-v1"
SIGNATURE_SCHEMA = "codeagent-atoms-signatures-v1"

# 盲区阈值（Q4 自知）：≤黄 → 绿；黄~红 → 黄；>红 → 红。
# 两个阈值故意都取在"当前值附近"：一上来就红等于逼人第一件事就是加台账（那是更坏的老毛病），
# 一上来就绿等于自欺。当前口径下（含 sealed）落在黄档，正是"盯着但不阻断"。
BLIND_YELLOW = 0.20
BLIND_RED = 0.35

# 域 → 六大类（纯展示分组，不参与校验）
GROUP_OF = {
    "approval": "安全治理", "guard": "安全治理", "security": "安全治理",
    "secrets": "安全治理", "sandbox": "安全治理", "locality": "安全治理",
    "codereview": "质量验证", "test": "质量验证", "fuzz": "质量验证",
    "bugdeep": "质量验证", "depscan": "质量验证", "archreview": "质量验证",
    "domain": "质量验证", "ontology": "质量验证", "impact": "质量验证", "atomic": "质量验证",
    "code": "生产编排", "plan": "生产编排", "dispatch": "生产编排",
    "project": "生产编排", "reuse": "生产编排", "skill": "生产编排",
    "evolve": "生产编排", "model": "生产编排", "llm": "生产编排", "subagent": "生产编排",
    "memory": "记忆上下文", "context": "记忆上下文", "session": "记忆上下文",
    "taskstate": "记忆上下文", "docs": "记忆上下文",
    "deliver": "交付运行", "mcp": "交付运行", "web": "交付运行", "tools": "交付运行",
}
GROUP_ORDER = ["安全治理", "质量验证", "生产编排", "记忆上下文", "交付运行", "其他"]

# 能力名前缀与 domain 不一致的**白名单**：domain → 允许出现的异域前缀。
# 建白名单而不改名，是因为改名要连带改全部调用点与对外文档，收益只是整齐（方案 §15 决策 2）。
PREFIX_ALLOWED = {
    "atomic": {"atomicity"},
    "codereview": {"minimal"},
    "docs": {"doc"},
    "impact": {"deadcode"},
    "locality": {"local"},
    "memory": {"event"},
    "tools": {"git"},
    "web": {"browsersmoke", "ui"},
}

# 组合模板静态入参里允许出现的占位符。二期扩了两种引用形态（组合收口到模板表时要用）：
#   $task / $upstream_files           一期就有
#   $seed.<key>[.<key>]               种子数据（$seed.code / $seed.language）
#   $step.<步骤名>[.<字段>...]         前面某一步的产出字段（$step.review.score）
# 认不出的形态一律当字面量传给原子（G11 在提交时报），不做猜测性求解。
PLACEHOLDER_FORMS = (
    r"^\$(?:task|upstream_files)$",
    r"^\$seed\.[A-Za-z_][\w]*(\.[\w]+)*$",
    r"^\$step\.[A-Za-z_][\w]*(\.[\w]+)*$",
)
_PLACEHOLDER_RE = re.compile("|".join("(?:%s)" % p for p in PLACEHOLDER_FORMS))
# 字面量里出现的 `$...` 是否算占位符：`$expr` 是抽取层内部标记（表达式，静态取不到值），不参与校验
_EXPR_MARK = "$expr"

# 密封分派声明：包装器在自己（或调用点上方）声明「我只转发这个前缀的能力」。
# 为什么用注释而不是集合：一行、零运行时开销、代码评审时肉眼可见（方案 §12 决策 3）。
SEAL_RE = re.compile(r"#\s*dispatch-sealed:\s*(\S+)")

# 改这些文件等于改所有原子/所有组合的契约 → 增量判定直接升级为全仓
PUBLIC_LAYER = {
    "atomic_base.py", "agent_runtime.py", "agent_loader.py",
    "registry.json", "capability_scope.py",
}

SKIP_DIR_PARTS = {"node_modules", ".git", "vendor", "__pycache__", ".mypy_cache", ".pytest_cache"}

RULES = {
    "G1_PHANTOM_CALL": ("P0", "调用了 registry 未声明的能力（运行时必然失败）"),
    "G2_UNWIRED_CAPABILITY": ("P2", "未接线：已挂载可调用但全仓零调用（编排覆盖度，非原子缺陷）"),
    "G3_DEP_NOT_EXERCISED": ("P2", "depends_on 声明了，源码里找不到对应调用"),
    "G4_UNDECLARED_DEP": ("P1", "原子实际跨原子调用，但 depends_on 未声明"),
    "G5_CONTRACT_GAP": ("P1", "调用点实参名/必填项与目标签名对不上（TypeError 被吞成 degraded）"),
    "G6_NAMING_DRIFT": ("P3", "能力前缀与 domain 不一致（白名单外）"),
    "G7_CYCLE": ("P0", "depends_on 成环"),
    "G8_ORDER_CONFLICT": ("P3", "registry.order 与依赖先序冲突"),
    "G9_CHAIN_PHANTOM": ("P0", "组合引用了不存在的能力（走到那一步必断）"),
    "G10_CHAIN_INPUT_MISMATCH": ("P1", "组合的静态入参签名接不住（TypeError 被吞成 degraded）"),
    "G11_CHAIN_PLACEHOLDER": ("P2", "组合入参用了未登记的占位符或占位符写错形态"),
    "G12_CAP_NOT_MOUNTED": ("P0", "provides 里声明了，但 main.py 没有 register（调了必失败）"),
    "G13_CAP_UNDECLARED": ("P1", "main.py 挂了能力，但 provides 没声明（对外不可见）"),
    # ── 二期新增（见 docs/ATOMS_GRAPH_V2_PLAN.md）──
    "G14_UNREACHABLE_CHAIN": ("P1", "组合没有任何入口可达（配了忘接，或入口声明被删）"),
    "G15_IMPLICIT_CHAIN": ("P1", "代码里写死了组合（capability 键的 list），没走模板表也没登记"),
    "G16_BLIND_RATIO": ("P1", "看不见的调用点比例超阈值（盲区在无声长大）"),
    "G17_SIGNATURE_BREAKING": ("P1", "能力签名发生破坏性变更（删参数/改必填/改名）且未认领"),
    "G18_DISPATCH_UNSEALED": ("P2", "转发型包装器没声明 dispatch-sealed 边界（黑盒不可审计）"),
    "G19_ENTRY_STALE": ("P1", "入口清单与代码对不上（evidence 找不到/ref 指向不存在的组合）"),
}

RULE_GROUP = {
    "G1_PHANTOM_CALL": "A 调用正确性", "G5_CONTRACT_GAP": "A 调用正确性",
    "G12_CAP_NOT_MOUNTED": "A 调用正确性", "G13_CAP_UNDECLARED": "A 调用正确性",
    "G3_DEP_NOT_EXERCISED": "B 依赖一致性", "G4_UNDECLARED_DEP": "B 依赖一致性",
    "G7_CYCLE": "B 依赖一致性", "G8_ORDER_CONFLICT": "B 依赖一致性",
    "G17_SIGNATURE_BREAKING": "B 依赖一致性",
    "G9_CHAIN_PHANTOM": "C 组合可追踪", "G10_CHAIN_INPUT_MISMATCH": "C 组合可追踪",
    "G11_CHAIN_PLACEHOLDER": "C 组合可追踪",
    "G14_UNREACHABLE_CHAIN": "C 组合可追踪", "G15_IMPLICIT_CHAIN": "C 组合可追踪",
    "G19_ENTRY_STALE": "C 组合可追踪",
    "G2_UNWIRED_CAPABILITY": "D 覆盖与命名", "G6_NAMING_DRIFT": "D 覆盖与命名",
    "G16_BLIND_RATIO": "D 覆盖与自知", "G18_DISPATCH_UNSEALED": "D 覆盖与自知",
}

# 四问归属：门的输出不再是单个布尔，而是四个互相独立的判定（失败模式不同）
QUESTIONS = {
    "Q1": ("契约", "名字、参数、占位符、挂载、依赖，对得上吗"),
    "Q2": ("通路", "声明过的组合，从哪个入口进得来"),
    "Q3": ("波及", "这次改动伤到谁（签名变了、组合被改了）"),
    "Q4": ("自知", "有多少调用点是门看不见的"),
}
QUESTION_OF = {
    "G1_PHANTOM_CALL": "Q1", "G3_DEP_NOT_EXERCISED": "Q1", "G4_UNDECLARED_DEP": "Q1",
    "G5_CONTRACT_GAP": "Q1", "G6_NAMING_DRIFT": "Q1", "G7_CYCLE": "Q1",
    "G8_ORDER_CONFLICT": "Q1", "G9_CHAIN_PHANTOM": "Q1", "G10_CHAIN_INPUT_MISMATCH": "Q1",
    "G11_CHAIN_PLACEHOLDER": "Q1", "G12_CAP_NOT_MOUNTED": "Q1", "G13_CAP_UNDECLARED": "Q1",
    "G2_UNWIRED_CAPABILITY": "Q1",
    "G14_UNREACHABLE_CHAIN": "Q2", "G19_ENTRY_STALE": "Q2",
    "G15_IMPLICIT_CHAIN": "Q3", "G17_SIGNATURE_BREAKING": "Q3",
    "G16_BLIND_RATIO": "Q4", "G18_DISPATCH_UNSEALED": "Q4",
}


# ────────────────────────── 通用小工具 ──────────────────────────
def _p(*parts):
    return Path(*[str(x) for x in parts])


def load_json(path, default=None):
    """读 JSON；读不到返回 default 并**不**静默（调用方决定要不要报）。
    注意用内置 open：早前那版 scripts/constitution_gate_changed.py 用 io.open 却没 import io，
    NameError 被 except 吞掉，台账永远读不到 → 存量全被当成新增。这里不再踩。"""
    try:
        with open(str(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def classify_group(domain):
    return GROUP_OF.get(domain, "其他")


def _rel(path, repo):
    try:
        return str(Path(path).resolve().relative_to(Path(repo).resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def _iter_py(repo, skip_tests=False):
    repo = Path(repo)
    for p in sorted(repo.rglob("*.py")):
        if any(part in SKIP_DIR_PARTS for part in p.parts):
            continue
        if skip_tests and (p.parent.name == "tests" or str(p).replace("\\", "/").find("/tests/") >= 0):
            continue
        yield p


# ────────────────────────── L0 声明层 ──────────────────────────
def load_registry(repo):
    """registry.json 是 agent_loader.scan() 的**产物**，不是声明源。
    声明源在各自原子 manifest.json —— 所以这里只读不写，dormant 之类要落 manifest。"""
    d = load_json(_p(repo, "registry.json"))
    if not isinstance(d, dict) or not isinstance(d.get("agents"), dict):
        raise SystemExit("做不到读 registry.json（不存在或结构不对）：%s" % _p(repo, "registry.json"))
    return d


def build_owner_index(atoms):
    """返回 atom_of(path) -> 原子名。用最长前缀匹配，避免 code 与 code-review 互相吃掉。"""
    own = sorted(((str(a["path"]).replace("\\", "/").strip("/"), n) for n, a in atoms.items()),
                 key=lambda x: -len(x[0]))

    def atom_of(path):
        s = str(path).replace("\\", "/")
        for prefix, name in own:
            if s == prefix or s.startswith(prefix + "/") or s.startswith(prefix):
                return name
        return None

    return atom_of, own


# ────────────────────────── L1 实现层 ──────────────────────────
def _fn_params(fn):
    """返回 (可接受参数名集合, 是否 **kwargs, 必填位置参数集合)。"""
    a = fn.args
    pos = list(a.posonlyargs) + list(a.args)
    names = {x.arg for x in pos + list(a.kwonlyargs)}
    names.discard("self")
    ndef = len(a.defaults or [])
    required = {x.arg for x in (pos[:len(pos) - ndef] if ndef else pos) if x.arg != "self"}
    return names, (a.kwarg is not None), required


def _methods_of(tree):
    """类里定义的方法：名字 → FunctionDef。register() 的目标必须是其中之一才核得到签名。"""
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[sub.name] = sub
    return out


def _registered_cap(node):
    """认出 `X.register("cap", self.fn)` → 能力名；不是这个形态返回 None。"""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    if node.func.attr != "register" or len(node.args) < 2:
        return None
    cap = node.args[0]
    if isinstance(cap, ast.Constant) and isinstance(cap.value, str):
        return cap.value
    return None


def _cap_info(node, methods):
    """一条 register 的能力信息。目标不是本类方法就标 opaque（Q4 会把它算进看不见）。"""
    target = node.args[1]
    fn = methods.get(target.attr) if isinstance(target, ast.Attribute) else None
    if fn is None:
        return {"opaque": True, "line": node.lineno}
    params, has_kw, required = _fn_params(fn)
    return {"opaque": False, "line": node.lineno, "method": target.attr,
            "params": sorted(params), "has_kwargs": has_kw, "required": sorted(required)}


def scan_impl(atom, repo):
    """解析原子入口：能力 → {method, params, required, has_kwargs, opaque}。"""
    entry = _p(repo, atom.get("path", ""), atom.get("entry", "main.py"))
    out = {"capabilities": {}, "error": None}
    if not entry.exists():
        out["error"] = "入口不存在: %s" % entry
        return out
    try:
        tree = ast.parse(entry.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as e:
        out["error"] = "AST 解析失败: %s" % e
        return out
    methods = _methods_of(tree)
    for node in ast.walk(tree):
        cap = _registered_cap(node)
        if cap:
            out["capabilities"][cap] = _cap_info(node, methods)
    return out


# ────────────────────────── L2 调用层 ──────────────────────────
# 证据等级（Q4 自知的基础）：门能对每个调用点"看清多少"，决定它的话有多硬。
#   verified   能力名是字面量 + 参数名可枚举 → 名字、参数、占位符全核
#   name-only  能力名是字面量，但 **kwargs 摊开 → 只能核名字，参数不可核
#   sealed     能力名来自变量，但所在函数声明了 dispatch-sealed 边界 → 边界已知，目标不可核
#   blind      能力名来自变量，且没有声明边界 → 什么都不知道
EVIDENCE_LEVELS = ("verified", "name-only", "sealed", "blind")


def _fn_spans(tree):
    """返回 [(start, end, name)]：函数体行号区间，用来判断某个调用点落在哪个函数里，
    以及那个函数有没有声明密封边界。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((n.lineno, max(getattr(n, "end_lineno", n.lineno) or n.lineno, n.lineno), n.name))
    out.sort()
    return out


def _sealed_prefixes(lines, spans):
    """函数级密封边界：{(start, end): 前缀}。声明写在函数体内任意一行（通常紧贴 def）。"""
    out = {}
    for start, end, _name in spans:
        seg = "\n".join(lines[start - 1:end])
        m = SEAL_RE.search(seg)
        if m:
            out[(start, end)] = m.group(1)
    return out


def _seal_at_line(lines, lineno, sealed, spans):
    """某行所属的密封前缀：先看所在函数，再看紧邻上方 3 行（模块级调用点用这种写法）。"""
    host = None
    for start, end, _n in spans:
        if start <= lineno <= end:
            host = (start, end)
            break
    if host and host in sealed:
        return sealed[host], "函数"
    for i in range(max(1, lineno - 3), lineno):
        m = SEAL_RE.search(lines[i - 1] or "")
        if m:
            return m.group(1), "上行"
    return None, None


def _is_run_capability(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_capability" and bool(node.args))


def _call_kwargs(node):
    """(关键字实参名集合, 是否用了 **splat)。splat=True 时实参名不可枚举，核不了。"""
    kw, splat = set(), False
    for k in node.keywords:
        if k.arg is None:
            splat = True
        else:
            kw.add(k.arg)
    return kw, splat


def _evidence_of(literal, splat, seal):
    """证据等级：先看能力名是不是字面量，再看转发边界有没有声明。"""
    if not literal:
        return "sealed" if seal else "blind"
    return "name-only" if splat else "verified"


def _site_of(node, rel, caller, scan):
    """一个 run_capability 调用点 → 一条记录。scan 是 (lines, sealed, spans) 三件套。"""
    lines, sealed, spans = scan
    a0 = node.args[0]
    literal = isinstance(a0, ast.Constant) and isinstance(a0.value, str)
    kw, splat = _call_kwargs(node)
    seal, seal_how = _seal_at_line(lines, node.lineno, sealed, spans)
    return {
        "site_id": "%s:%d" % (rel, node.lineno),
        "caller": caller,
        "capability": a0.value if literal else None,
        "cap_expr": None if literal else ast.dump(a0, annotate_fields=False)[:80],
        "file": rel, "line": node.lineno,
        "kwargs": None if splat else sorted(kw),
        "evidence": _evidence_of(literal, splat, seal), "seal": seal, "seal_at": seal_how,
    }


def scan_calls(repo, atom_of):
    """返回调用点列表。用 AST 而不是正则：只有 AST 能拿到调用点实际传了哪些关键字参数
    —— 这正是「编排传错参数 → TypeError → 被信封降级」的根因位置。

    二期每个调用点都带 `evidence`（门看得清多少）与 `seal`（包装器声明的转发边界）。
    `blind` 与 `sealed` 也**照样进列表**：看不见的东西必须被计数，否则门会假装全覆盖。
    """
    rows = []
    for p in _iter_py(repo, skip_tests=True):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "run_capability(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        spans = _fn_spans(tree)
        scan = (lines, _sealed_prefixes(lines, spans), spans)
        caller = atom_of(p) or "<编排层>"
        rel = _rel(p, repo)
        for node in ast.walk(tree):
            if _is_run_capability(node):
                rows.append(_site_of(node, rel, caller, scan))
    return rows


def count_raw_sites(repo):
    """原始 `run_capability(` 出现次数（含动态能力名，AST 抽不出来的那些）。

    读不到的文件**不能默默跳过**：跳过会让"门看不见多少"这个数字偏小，而偏小的方向
    恰好是"门看起来覆盖得更好" —— 最难发现的那种错。所以把跳过的文件一并返回写进指标。
    """
    n, skipped = 0, []
    for p in _iter_py(repo, skip_tests=True):
        try:
            n += p.read_text(encoding="utf-8", errors="ignore").count("run_capability(")
        except OSError as e:
            skipped.append("%s: %s" % (_rel(p, repo), e))
    return n, skipped


# ────────────────────────── L3 组合层 ──────────────────────────
def _literal_value(v):
    """把 AST 字面量化成 Python 值；嵌套 dict 递归。取不到值的返回 _EXPR_MARK。"""
    if isinstance(v, ast.Constant):
        return v.value
    if not isinstance(v, ast.Dict):
        return _EXPR_MARK
    out = {}
    for k, val in zip(v.keys, v.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out[k.value] = _literal_value(val)
    return out


def _dict_pairs(node):
    """dict 节点的字符串键摊平成普通 dict（值仍是 AST，留给调用方决定要不要求解）。"""
    out = {}
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out[k.value] = v
    return out


def _unresolved_step():
    return {"capability": None, "step": None, "inputs": None, "inputs_kind": "unresolved"}


def _step_inputs(ins):
    """步骤入参：dict 字面量=静态（可核签名）、表达式=λ 动态、没写=无。"""
    if isinstance(ins, ast.Dict):
        return {k: _literal_value(v) for k, v in _dict_pairs(ins).items()}, "static"
    if ins is None:
        return {}, "none"
    return {}, "callable"


def _step_of_element(el):
    """组合里的一步：要么是裸能力名字符串，要么是带 capability/inputs 的 dict。"""
    if isinstance(el, ast.Constant) and isinstance(el.value, str):
        return {"capability": el.value, "step": el.value,
                "inputs": None, "inputs_kind": "none"}
    if not isinstance(el, ast.Dict):
        return _unresolved_step()
    d = _dict_pairs(el)
    cap = d.get("capability")
    if not (isinstance(cap, ast.Constant) and isinstance(cap.value, str)):
        return _unresolved_step()
    ins, kind = _step_inputs(d.get("inputs"))
    name = d.get("step")
    named = name.value if isinstance(name, ast.Constant) and isinstance(name.value, str) else None
    return {"capability": cap.value, "step": named or cap.value,
            "inputs": ins, "inputs_kind": kind}


def _steps_of_list(node):
    """从一个 List 字面量里抽步骤；不是组合（没有 capability 键）返回 []。

    入参值可以是常量、嵌套 dict（如 evolve.refine 的 outcome），也可以是表达式。
    嵌套 dict 要递归抽，否则取不到值会被当成"表达式"，在 G11 里误报成非法占位符。
    """
    steps = [_step_of_element(el) for el in node.elts]
    return steps if any(s["capability"] for s in steps) else []


def _name_refs(node):
    """把 `chain or default` 这类表达式化成候选名集合。"""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.BoolOp):
        out = []
        for v in node.values:
            out.extend(_name_refs(v))
        return out
    return []


def _local_lists(fn):
    """函数内 `x = [{capability: ...}, ...]` 形态的局部组合：名字 → (步骤, 定义行)。"""
    out = {}
    for st in ast.walk(fn):
        if not (isinstance(st, ast.Assign) and isinstance(st.value, ast.List)):
            continue
        steps = _steps_of_list(st.value)
        if not steps:
            continue
        for tgt in st.targets:
            if isinstance(tgt, ast.Name):
                out[tgt.id] = (steps, st.lineno)
    return out


CHAIN_KW = "chain"


def _chain_arg(call):
    """取 run_chain 的链参数：先看位置参数，再看 `chain=` 关键字。

    为什么非要看关键字：`run_chain(chain=steps)` 与 `run_chain(steps)` 语义完全一样，
    但 AST 上前者的 args 是空的。只认 args[0] 的话，把参数改成关键字写法（重构时
    很常见的动作）就能让「代码里写死的组合」从图上整条消失 —— 而 G15 查的正是这个，
    消失即等于放行。变异测试当场把这条漏掉了（写死一条 2 步链，门仍 pass）。
    """
    if call.args:
        return call.args[0]
    for k in call.keywords:
        if k.arg == CHAIN_KW:
            return k.value
    return None


def _is_run_chain(node):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    return node.func.attr == "run_chain" and _chain_arg(node) is not None


def _chain_row(cid, rel, fn, call, owner):
    """代码内组合的一条记录（骨架）。行号分两处：定义行 line 与调用行 call_line。"""
    return {"chain_id": cid, "kind": "code", "name": "%s.%s" % (rel, fn.name),
            "file": rel, "call_line": call.lineno, "steps": [], "dynamic": False,
            "reason": None, "owner": owner}


def _code_chains_in_fn(fn, rel, owner):
    """一个函数里的组合。

    chain_id 与行号解耦，行号只作定位提示（line/call_line）。把行号编进 ID 的话，
    挪一次代码 entries 里的 refs 就全部静默失效，而失效方向恰好是"误报不可达"
    —— 最难查的那种（原型踩过）。
    """
    locals_ = _local_lists(fn)
    cid = "code:%s#%s" % (rel, fn.name)
    out = []
    for call in ast.walk(fn):
        if not _is_run_chain(call):
            continue
        arg = _chain_arg(call)
        row = _chain_row(cid, rel, fn, call, owner)
        hit = next((r for r in _name_refs(arg) if r in locals_), None)
        if hit:
            steps, def_line = locals_[hit]
            row.update(steps=steps, line=def_line)
            out.append(row)
        elif isinstance(arg, ast.Constant) and arg.value is None:
            continue  # run_chain(None) / run_chain(chain=None) = 自动选链，交给模板表
        else:
            row.update(line=call.lineno, dynamic=True, passthrough=True,
                       reason="组合由运行时给出（参数透传），不是代码里的组合定义")
            out.append(row)
    return out


def scan_code_chains(repo, atom_of):
    """抽取代码内写死的组合（局部 list 字面量 + run_chain 引用）。"""
    chains = []
    for p in _iter_py(repo, skip_tests=True):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "run_chain(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = _rel(p, repo)
        owner = atom_of(p) or "<编排层>"
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chains.extend(_code_chains_in_fn(fn, rel, owner))
    return chains


def find_template_file(repo):
    for p in sorted(_p(repo, "agents").rglob("chain_templates.json")):
        if "__pycache__" not in str(p):
            return p
    return None


def _tpl_step(st):
    """模板里的一步：裸字符串=只要能力名；dict=带静态 inputs 的完整步骤。"""
    if isinstance(st, str):
        return {"capability": st, "step": st, "inputs": {}, "inputs_kind": "none"}
    if not isinstance(st, dict):
        return {"capability": None, "step": None, "inputs": {}, "inputs_kind": "unresolved"}
    ins = st.get("inputs")
    return {"capability": st.get("capability"),
            "step": st.get("step") or st.get("capability"),
            "inputs": dict(ins) if isinstance(ins, dict) else {},
            "inputs_kind": "static" if isinstance(ins, dict) and ins else "none"}


def load_template_chains(repo):
    """组合模板表：声明式组合的**唯一**来源。每条模板 = 一条可追踪的组合。

    chain_id 用模板名而不是下标：下标一重排，entries 里的 refs 就全部指错。
    名字是这张表里唯一的稳定身份，改名字是语义变更，理应触发重新登记。
    """
    f = find_template_file(repo)
    if f is None:
        return [], None
    d = load_json(f, {}) or {}
    rel = _rel(f, repo)
    chains = []
    for i, t in enumerate(d.get("templates") or []):
        name = t.get("name") or ("模板 %d" % (i + 1))
        chains.append({
            "chain_id": "tpl:%s#%s" % (rel, name), "kind": "template",
            "name": name, "index": i, "default": bool(t.get("default")),
            "file": rel, "line": 0, "call_line": 0,
            "steps": [_tpl_step(st) for st in (t.get("chain") or [])],
            "dynamic": False, "reason": None,
            "keywords": list(t.get("keywords") or []),
            "note": t.get("note") or "",
        })
    return chains, rel


# ────────────────────── 入口清单（Q2：通路）──────────────────────
def entries_path(repo):
    return _p(repo, "config", "atoms_entries.json")


def load_entries(repo):
    d = load_json(entries_path(repo))
    if not isinstance(d, dict) or not isinstance(d.get("entries"), list):
        return {"schema": ENTRIES_SCHEMA, "entries": [], "_missing": True}
    d.setdefault("_missing", False)
    return d


def _ref_matches(ref, chain_id):
    """refs 支持精确 ID 与末尾通配（`tpl:*` 表示模板表全部组合）。"""
    ref = str(ref or "")
    if not ref:
        return False
    if ref.endswith("*"):
        return chain_id.startswith(ref[:-1])
    return ref == chain_id


def _symbol_source(repo, rel, symbol):
    """取某个函数（含类方法）的源码片段，用于校验入口声明的证据还在。"""
    p = _p(repo, rel)
    if not p.exists():
        return None, "文件不存在: %s" % rel
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as e:
        return None, "AST 解析失败: %s" % e
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == symbol:
            end = max(getattr(n, "end_lineno", n.lineno) or n.lineno, n.lineno)
            return "\n".join(lines[n.lineno - 1:end]), None
    return None, "找不到符号 %s（改了函数名？入口清单要跟着改）" % symbol


def _entry_evidence_problem(e, repo):
    """入口声明的证据还站得住吗：没有 evidence、文件/符号找不到、token 不在源码里。"""
    ev = e.get("evidence") or {}
    if not ev:
        return "入口没有 evidence（声明的证据在哪一行）"
    src, err = _symbol_source(repo, ev.get("file") or "", ev.get("symbol") or "")
    if src is None:
        return "evidence 失效：%s" % err
    token = ev.get("token")
    if token and token not in src:
        return ("evidence 里找不到 %r —— 入口声明的接线已经不在代码里了（%s#%s）"
                % (token, ev.get("file"), ev.get("symbol")))
    return None


def _entry_problems_of(ents, chains, repo):
    """逐条核对入口清单。返回 (问题列表, 失效入口 ID 集合)。"""
    problems, bad = [], set()
    for e in ents:
        refs = list(e.get("refs") or [])
        why = _entry_evidence_problem(e, repo)
        if not why and refs and not any(_ref_matches(r, c["chain_id"])
                                       for r in refs for c in chains):
            why = ("refs 指向的组合一个都不存在：%s"
                   "（组合 ID 要从 --emit 的输出里挑，不许手敲）" % refs)
        if why:
            problems.append(_entry_problem(e, why))
            bad.add(e.get("id"))
    return problems, bad


def _reachers(ents, bad_ids, chain_id):
    """这条组合从哪些入口进得来。

    声明失效的入口**不算通路**：evidence 都找不到，凭什么说走得通。不这样处理，
    "入口抄错"只会报一条 G19，而组合看起来仍然可达 —— 那正是假的绿。
    """
    by = []
    for e in ents:
        if e.get("id") in bad_ids:
            continue
        if any(_ref_matches(r, chain_id) for r in (e.get("refs") or [])):
            by.append(e.get("id") or "?")
    return by


def _verdict(by):
    if any(k.startswith("cli:") for k in by):
        return "cli-reachable"
    return "api-only" if by else "declared-only"


def chain_reachability(chains, entries, repo):
    """每条组合从哪些入口进得来。**三档，不压成布尔**（压成布尔就答不出"配了忘接"）。

    cli-reachable  敲命令能走到
    api-only       只有库调用能走到 —— 允许，但必须显式承认（entry 写了 note/when）
    declared-only  没有任何入口 —— 红（G14）：要么接入，要么从表里删
    """
    ents = [e for e in (entries.get("entries") or []) if isinstance(e, dict)]
    problems, bad = _entry_problems_of(ents, chains, repo)
    verdicts = {}
    for ch in chains:
        if ch.get("dynamic"):
            continue                       # 透传链不是"声明过的组合"，不参与通达判定
        by = _reachers(ents, bad, ch["chain_id"])
        verdicts[ch["chain_id"]] = {"verdict": _verdict(by), "by": by}
    return verdicts, problems


def _entry_problem(entry, detail):
    eid = entry.get("id") or "?"
    return {"rule": "G19_ENTRY_STALE", "severity": RULES["G19_ENTRY_STALE"][0],
            "group": RULE_GROUP["G19_ENTRY_STALE"], "subject": "入口 %s" % eid,
            "detail": detail, "key": "G19_ENTRY_STALE|入口 %s" % eid,
            "file": (entry.get("evidence") or {}).get("file"), "line": None,
            "chain_id": None, "atom": None}


# ────────────────────── 签名快照（Q3：波及）──────────────────────
def sig_path(repo):
    return _p(repo, "config", "atoms_signatures.json")


def load_signatures(repo):
    d = load_json(sig_path(repo))
    if not isinstance(d, dict) or not isinstance(d.get("capabilities"), dict):
        return {"schema": SIGNATURE_SCHEMA, "capabilities": {}, "_missing": True}
    d.setdefault("_missing", False)
    return d


def build_signatures(impls, prov):
    """把 166 条能力的签名 dump 成可比对的快照（--update-signatures 写盘，禁手写）。"""
    caps = {}
    for cap, atom in sorted(prov.items()):
        info = (impls.get(atom) or {}).get("capabilities", {}).get(cap) or {}
        caps[cap] = {"atom": atom, "params": list(info.get("params") or []),
                     "required": list(info.get("required") or []),
                     "has_kwargs": bool(info.get("has_kwargs")),
                     "opaque": bool(info.get("opaque"))}
    return {"schema": SIGNATURE_SCHEMA, "capabilities": caps}


def _params_of(d):
    return list(d.get("params") or [])


def _required_of(d):
    return list(d.get("required") or [])


def _minus(xs, ys):
    return [x for x in xs if x not in ys]


def _param_change(removed, added):
    """参数被删/改名 —— 都是 P1：按名调用的调用点全部失效。"""
    if removed and added and len(removed) == len(added):
        return [("param_renamed", "P1",
                 "参数改名：%s → %s（按名调用处全部失效）" % (removed, added))]
    if removed:
        return [("param_removed", "P1", "删了参数 %s" % removed)]
    return []


def _added_change(added, required):
    """新增参数：落在 required 里是破坏性变更，否则只是向后兼容。"""
    req_now = [p for p in added if p in required]
    if req_now:
        return [("added_required", "P1", "新增必填参数 %s（所有调用点都要改）" % req_now)]
    if added:
        return [("added_optional", "P3", "新增可选参数 %s（向后兼容）" % added)]
    return []


def _diff_signature_one(a, b):
    """一条能力的快照差异 → [(kind, severity, detail)]。破坏性变更一律 P1。"""
    ap, bp = _params_of(a), _params_of(b)
    ar, br = _required_of(a), _required_of(b)
    removed, added = _minus(ap, bp), _minus(bp, ap)
    out = _param_change(removed, added) + _added_change(added, br)
    became = _minus([p for p in br if p in ap], ar)
    if became:
        out.append(("gained_required", "P1",
                    "参数由可选变必填 %s（老调用点会 TypeError）" % became))
    if a.get("has_kwargs") and not b.get("has_kwargs"):
        out.append(("kwargs_narrowed", "P1", "去掉了 **kwargs（原来能透传的键现在接不住）"))
    return out


# 能力整体消失/新增：只报不算破坏（调用点还在会由 G1 抓）
_PRESENCE = {
    "removed": ("cap_removed", "快照里有、现在没有（能力被删了；调用点还在会由 G1 报）"),
    "added": ("cap_added", "新增能力（快照待更新）"),
}


def _sig_changes_for(cap, old, new):
    """一条能力的差异记录（含"整体消失/新增"两种情况）。"""
    if cap not in new:
        kind, detail = _PRESENCE["removed"]
    elif cap not in old:
        kind, detail = _PRESENCE["added"]
    else:
        return [{"cap": cap, "kind": k, "severity": s, "detail": d}
                for k, s, d in _diff_signature_one(old[cap], new[cap])]
    return [{"cap": cap, "kind": kind, "severity": "P3", "detail": detail}]


def diff_signatures(snapshot, current):
    """快照 vs 现状。**破坏性变更**（删参数/改必填/改名/收窄 **kwargs）→ P1；新增可选 → 只报。

    为什么要有这一条：G5 只看"调用点传的实参在不在签名里"，看不到"签名自己变了"。
    给某个能力加了必填参数，它自己不会红，红的是下游调用点 —— 而下游可能根本没有
    调用点（盲区），于是整个改动静默通过。快照把这个方向也堵上。
    """
    if snapshot.get("_missing"):
        return [{"cap": "*", "kind": "snapshot_missing", "severity": "P1",
                 "detail": "签名快照不存在：跑 `python scripts/verify_atoms_graph.py "
                           "--update-signatures --reason \"...\"` 建立基线（否则签名变了没人知道）"}]
    old = snapshot.get("capabilities") or {}
    new = current.get("capabilities") or {}
    out = []
    for cap in sorted(set(old) | set(new)):
        out.extend(_sig_changes_for(cap, old, new))
    return out


# ── 建图 + 体检 ──────────────────────────
def _walk_inputs(d, prefix=""):
    """递归展开静态入参，返回 (扁平键名, 值)。嵌套 dict 也要检查占位符，
    否则 `{"outcome": {"score": "$step.reviewx"}}` 里的错拼会被漏掉。"""
    for k, v in (d or {}).items():
        key = ("%s.%s" % (prefix, k)) if prefix else str(k)
        if isinstance(v, dict):
            for kv in _walk_inputs(v, key):
                yield kv
        else:
            yield key, v


def _step_info(st, impls):
    """这一步能力的签名信息；原子不明或没挂载时返回 None。"""
    atom = st.get("atom")
    return impls.get(atom, {}).get("capabilities", {}).get(st.get("capability")) if atom else None


def _sig_bad(inputs, info):
    """静态入参里有哪些键不在方法签名里。核不了的（opaque / **kwargs 摊开）返回空，不猜。"""
    if not info or info.get("opaque") or info.get("has_kwargs"):
        return []
    accepted = set(info["params"])
    return sorted(k for k in inputs if k not in accepted)


def _resolve_step(st, prov, impls):
    """把组合里的一步落到真实能力：归属原子 + 签名核对 + 静态入参能不能接住。"""
    cap = st.get("capability")
    st["atom"] = prov.get(cap)
    st["resolved"] = bool(cap) and cap in prov
    st["trace_id"] = "cap:%s" % cap if st["resolved"] else None
    info = _step_info(st, impls)
    st["sig_checked"] = bool(info) and not info.get("opaque") and not info.get("has_kwargs")
    static = st["inputs_kind"] == "static"
    st["sig_bad"] = _sig_bad(st["inputs"], info) if static and st["sig_checked"] else []


def _attach_resolution(chains, prov, impls):
    """组合逐步解析：能力 → 归属原子 → 签名核对。这是「组合可追踪」的核心一步。"""
    for ch in chains:
        for i, st in enumerate(ch["steps"], 1):
            st["order"] = i
            _resolve_step(st, prov, impls)


def _attach_reach(chains, verdicts):
    for ch in chains:
        v = verdicts.get(ch["chain_id"])
        ch["reach"] = (v["verdict"] if v
                       else ("passthrough" if ch.get("dynamic") else "declared-only"))
        ch["reach_by"] = v["by"] if v else []


def _chain_usage(chains):
    """能力 → 用到它的组合。**只经组合可达**也算被调到，否则主干能力会被误判成未接线。"""
    used = {}
    for ch in chains:
        for st in ch["steps"]:
            if st.get("resolved") and not ch["dynamic"]:
                used.setdefault(st["capability"], []).append(ch["chain_id"])
    return used


def _scan_repo(repo):
    """四层抽取 + 索引，结果装进一个 ctx 供各规则函数共读（只读，不再改）。"""
    repo = Path(repo).resolve()
    reg = load_registry(repo)
    atoms = reg["agents"]
    atom_of, _own = build_owner_index(atoms)
    prov = {c: n for n, a in atoms.items() for c in a["provides"]}
    impls = {n: scan_impl(a, repo) for n, a in atoms.items()}

    calls = scan_calls(repo, atom_of)
    for c in calls:
        # 能力名来自变量的调用点：名字不知道，**不算幻影**（那会天天误报）。
        # 它们只贡献"看不见多少"这个事实，不参与名字核对。
        c["resolved"] = bool(c["capability"]) and c["capability"] in prov
        c["atom"] = prov.get(c["capability"]) if c["capability"] else None

    tpl_chains, tpl_rel = load_template_chains(repo)
    chains = tpl_chains + scan_code_chains(repo, atom_of)
    _attach_resolution(chains, prov, impls)

    entries = load_entries(repo)
    verdicts, entry_problems = chain_reachability(chains, entries, repo)
    _attach_reach(chains, verdicts)

    declared = [(n, prov.get(dp), dp)
                for n, a in atoms.items() for dp in (a.get("depends_on") or [])]
    sig_snap = load_signatures(repo)
    sig_now = build_signatures(impls, prov)
    raw_sites, skipped = count_raw_sites(repo)
    return {
        "repo": repo, "reg": reg, "atoms": atoms, "prov": prov, "impls": impls,
        "calls": calls, "raw_sites": raw_sites, "raw_sites_skipped": skipped,
        "chains": chains, "chain_source": tpl_rel,
        "entries": entries, "entry_problems": entry_problems,
        "declared_deps": declared,
        "call_edges": Counter((c["caller"], c["capability"]) for c in calls if c["capability"]),
        "cap_calls": Counter(c["capability"] for c in calls if c["capability"]),
        "chain_used": _chain_usage(chains),
        "signatures": sig_now,
        "signature_changes": diff_signatures(sig_snap, sig_now),
    }


def _reached(ctx, cap):
    return ctx["cap_calls"].get(cap, 0) > 0 or cap in ctx["chain_used"]


def _build_cap_index(ctx):
    """能力反查索引：能力 → 调用点 / 被哪些组合用到（改动影响面）。"""
    idx = {cap: {"atom": atom, "sites": [], "chains": []}
           for cap, atom in ctx["prov"].items()}
    for c in ctx["calls"]:
        if c["capability"] in idx:
            idx[c["capability"]]["sites"].append(c["site_id"])
    for ch in ctx["chains"]:
        for st in ch["steps"]:
            if st.get("capability") in idx:
                idx[st["capability"]]["chains"].append(
                    {"chain_id": ch["chain_id"], "name": ch["name"], "order": st["order"]})
    return idx


def _build_nodes(ctx):
    nodes = []
    for n, a in ctx["atoms"].items():
        impl = ctx["impls"][n]
        registered = sorted(impl["capabilities"])
        provides = a["provides"]
        nodes.append({
            "id": n, "trace_prefix": "atom:%s" % n,
            "domain": a["domain"], "group": classify_group(a["domain"]),
            "version": a.get("version"), "provides": list(provides),
            "depends_on": list(a.get("depends_on") or []),
            "inputs": list(a.get("inputs") or []), "outputs": list(a.get("outputs") or []),
            "registered": registered,
            "mounted_all": bool(registered) and set(registered) >= set(provides),
            "complete": bool(registered) and set(registered) == set(provides),
            "wired": any(_reached(ctx, c) for c in provides),
            "ncall": sum(ctx["cap_calls"].get(c, 0) for c in provides),
            "nchain": sum(len(ctx["chain_used"].get(c, [])) for c in provides),
            "path": str(a.get("path", "")).replace("\\", "/"),
            "impl_error": impl["error"],
        })
    return nodes


def _build_edges(ctx):
    prov = ctx["prov"]
    edges = [{"from": src, "to": prov.get(cap), "capability": cap, "kind": "call",
              "count": cnt, "resolved": cap in prov}
             for (src, cap), cnt in sorted(ctx["call_edges"].items(), key=lambda x: (-x[1], x[0]))]
    for src, dst, cap in ctx["declared_deps"]:
        edges.append({"from": src, "to": dst, "capability": cap, "kind": "declared_dep",
                      "count": 1, "resolved": dst is not None})
    return edges


# ── 一条问题（findings 的最小单位）──
def _loc(file=None, line=None, chain_id=None, atom=None):
    """问题定位。单独抽出来是为了让 _finding 的参数不超过 5 个（宪法门会数）。"""
    return {"file": file, "line": line, "chain_id": chain_id, "atom": atom}


def _finding(rule, subject, detail, at=None):
    at = at or {}
    return {"rule": rule, "severity": RULES[rule][0], "group": RULE_GROUP[rule],
            "subject": subject, "detail": detail,
            "key": rule + "|" + re.sub(r":\d+", "", subject),
            "file": at.get("file"), "line": at.get("line"),
            "chain_id": at.get("chain_id"), "atom": at.get("atom")}


# ── 规则：调用层（G1 / G5 / G18）──
def _rules_calls(ctx):
    out = []
    for c in ctx["calls"]:
        if c["capability"] and not c["resolved"]:
            out.append(_finding("G1_PHANTOM_CALL", "%s → %s" % (c["caller"], c["capability"]),
                                "%s 调用了未声明的能力" % c["site_id"],
                                _loc(c["file"], c["line"],
                                     atom=None if c["caller"] == "<编排层>" else c["caller"])))
        if c["evidence"] == "blind":
            out.append(_finding("G18_DISPATCH_UNSEALED", "%s · %s" % (c["site_id"], c["caller"]),
                                "能力名是变量却能调任意能力，且没声明转发边界 → 在函数里加一行 "
                                "`# dispatch-sealed: <能力前缀|*>`，把黑盒变成有边界的转发器",
                                _loc(c["file"], c["line"],
                                     atom=None if c["caller"] == "<编排层>" else c["caller"])))
    return out


def _contract_of(ctx, cap):
    """目标能力的签名信息。原子不明 / 没挂载 / 参数不可枚举 → None（不猜）。"""
    dst = ctx["prov"].get(cap)
    info = ctx["impls"].get(dst, {}).get("capabilities", {}).get(cap) if dst else None
    if not info or info.get("opaque") or info.get("has_kwargs"):
        return None, dst
    return info, dst


def _gap_findings(c, info, dst):
    """一个调用点的 G5 问题：多传了不该有的 / 少给了必填的。"""
    accepted = set(info["params"])
    at = _loc(c["file"], c["line"], atom=dst)
    subj = "%s → %s" % (c["site_id"], c["capability"])
    out = []
    bad = sorted(set(c["kwargs"] or []) - accepted)
    if bad:
        out.append(_finding("G5_CONTRACT_GAP", subj,
                            "传了 %s，但 %s.%s() 只接受 %s"
                            % (bad, dst, info.get("method"), sorted(accepted)), at))
    lack = sorted(set(info.get("required") or []) - set(c["kwargs"] or []))
    if c["kwargs"] is not None and lack:
        out.append(_finding("G5_CONTRACT_GAP", subj, "未提供必填参数 %s" % lack, at))
    return out


def _contract_gaps(ctx):
    """G5：调用点实参名/必填项与目标签名对不上（改成 CAP_CONTRACT 之后仍是提交期该拦的错）。"""
    out = []
    for c in ctx["calls"]:
        info, dst = _contract_of(ctx, c["capability"])
        if info:
            out.extend(_gap_findings(c, info, dst))
    return out


# ── 规则：覆盖与命名（G2 / G6）──
def _rules_caps(ctx):
    out = []
    for n, a in ctx["atoms"].items():
        allowed = PREFIX_ALLOWED.get(a["domain"], set())
        for c in a["provides"]:
            if not _reached(ctx, c):
                out.append(_finding("G2_UNWIRED_CAPABILITY", "%s · %s" % (n, c),
                                    "已挂载可调用，但既无调用点、也不在任何组合里（待接线）",
                                    _loc(atom=n)))
            pref = c.split(".")[0]
            if pref != a["domain"] and pref not in allowed:
                out.append(_finding("G6_NAMING_DRIFT", "%s · %s" % (n, c),
                                    "前缀 %r ≠ domain %r，且不在白名单" % (pref, a["domain"]),
                                    _loc(atom=n)))
    return out


# ── 规则：声明与挂载（G12 / G13）──
def _rules_declared(ctx):
    out = []
    for n, a in ctx["atoms"].items():
        impl = ctx["impls"][n]["capabilities"]
        for c in a["provides"]:
            if c not in impl:
                out.append(_finding("G12_CAP_NOT_MOUNTED", "%s · %s" % (n, c),
                                    "provides 声明了，但 %s 里没有 register，调了必失败"
                                    % a.get("entry", "main.py"), _loc(atom=n)))
        for c in impl:
            if c not in a["provides"]:
                out.append(_finding("G13_CAP_UNDECLARED", "%s · %s" % (n, c),
                                    "main.py 挂了这条能力，但 provides 没声明（对外不可见）",
                                    _loc(atom=n)))
    return out


# ── 规则：依赖（G3 / G4）──
def _rules_deps(ctx):
    out = []
    decl_pairs = {(s, c) for s, _d, c in ctx["declared_deps"]}
    for src, _dst, cap in ctx["declared_deps"]:
        if ctx["call_edges"].get((src, cap), 0) == 0:
            out.append(_finding("G3_DEP_NOT_EXERCISED", "%s → %s" % (src, cap),
                                "depends_on 声明了，源码中找不到对应调用", _loc(atom=src)))
    for (src, cap), cnt in sorted(ctx["call_edges"].items()):
        if src == "<编排层>" or ctx["prov"].get(cap) == src:
            continue
        if (src, cap) not in decl_pairs:
            out.append(_finding("G4_UNDECLARED_DEP", "%s → %s" % (src, cap),
                                "原子实际调用了外部能力 ×%d，但 depends_on 未声明" % cnt,
                                _loc(atom=src)))
    return out


# ── 规则：依赖图（G7 成环 / G8 顺序冲突）──
def _find_cycles(ctx):
    adj = defaultdict(list)
    for src, dst, _cap in ctx["declared_deps"]:
        if dst:
            adj[src].append(dst)
    out, color = [], {}

    def walk(u, stack):
        color[u] = 1
        for v in adj[u]:
            if color.get(v) == 1:
                out.append(_finding("G7_CYCLE", "%s → %s" % (u, v),
                                    "depends_on 成环：" + " → ".join(stack + [v]), _loc(atom=u)))
            elif color.get(v, 0) == 0:
                walk(v, stack + [v])
        color[u] = 2

    for n in sorted(ctx["atoms"]):
        if color.get(n, 0) == 0:
            walk(n, [n])
    return out


def _order_conflicts(ctx):
    order = ctx["reg"].get("order") or []
    pos = {n: i for i, n in enumerate(order)}
    out = []
    for src, dst, _cap in ctx["declared_deps"]:
        if dst and pos.get(src, 0) < pos.get(dst, 1 << 30):
            out.append(_finding("G8_ORDER_CONFLICT", "%s → %s" % (src, dst),
                                "order 里 %s(#%d) 排在提供者 %s(#%d) 之前"
                                % (src, pos.get(src), dst, pos.get(dst)), _loc(atom=src)))
    return out


# ── 规则：组合（G9 / G10 / G11）──
def _bad_placeholder(inputs):
    for k, v in _walk_inputs(inputs or {}):
        if isinstance(v, str) and v.startswith("$") and v != _EXPR_MARK \
                and not _PLACEHOLDER_RE.match(v):
            return k, v
    return None, None


def _rules_chains(ctx):
    out = []
    for ch in ctx["chains"]:
        for st in ch["steps"]:
            cap = st.get("capability")
            at = _loc(ch["file"], ch["line"] or None, ch["chain_id"])
            if not cap:
                out.append(_finding("G9_CHAIN_PHANTOM", "%s 第%d步" % (ch["name"], st.get("order", 0)),
                                    "组合里这一步的能力名抽不出来（非字面量），无法追踪", at))
                continue
            if not st["resolved"]:
                out.append(_finding("G9_CHAIN_PHANTOM",
                                    "%s 第%d步 → %s" % (ch["name"], st["order"], cap),
                                    "组合引用了 registry 里不存在的能力，走到这一步必断", at))
            if st["sig_bad"]:
                out.append(_finding("G10_CHAIN_INPUT_MISMATCH",
                                    "%s 第%d步 → %s" % (ch["name"], st["order"], cap),
                                    "静态入参 %s 不在方法签名里，运行时 TypeError 会被吞成 degraded"
                                    % st["sig_bad"], dict(at, atom=st["atom"])))
            k, v = _bad_placeholder(st.get("inputs"))
            if k:
                out.append(_finding("G11_CHAIN_PLACEHOLDER",
                                    "%s 第%d步 → %s" % (ch["name"], st["order"], k),
                                    "占位符 %r 不是可解析形态（认 $task / $upstream_files / "
                                    "$seed.x / $step.name.field）" % v, at))
    return out


# ── 规则：通路（G14 / G19）──
def _rules_reach(ctx):
    out = [_finding("G14_UNREACHABLE_CHAIN", "%s · %s" % (ch["name"], ch["chain_id"]),
                    "这条组合没有任何入口能走到：要么接进入口（CLI/API），要么从模板表里删掉",
                    _loc(chain_id=ch["chain_id"]))
           for ch in ctx["chains"]
           if not ch.get("dynamic") and ch.get("reach") == "declared-only"]
    for p in ctx["entry_problems"]:
        out.append(_finding("G19_ENTRY_STALE", p["subject"], p["detail"], _loc(file=p.get("file"))))
    return out


# ── 规则：第二层（G15 / G16 / G17）──
def _allowed_code_chains(repo):
    """代码内组合的登记簿：允许存在第二套，但必须写明理由（不许默默分叉）。"""
    d = load_json(_p(repo, "config", "atoms_chains_allow.json"), {}) or {}
    return {str(x) for x in (d.get("allow") or [])}


def _implicit_chain_findings(ctx):
    """G15：代码里写死的组合。组合只允许一个源（模板表），第二套必须登记理由。"""
    allowed = _allowed_code_chains(ctx["repo"])
    out = []
    for ch in ctx["chains"]:
        if ch["kind"] != "code" or ch.get("dynamic") or not ch["steps"]:
            continue
        if ch["chain_id"] in allowed:
            continue
        out.append(_finding("G15_IMPLICIT_CHAIN", "%s · %s" % (ch["name"], ch["chain_id"]),
                            "代码里写死的组合（%d 步）：组合只允许一个源（模板表），"
                            "搬进 chain_templates.json 或在 config/atoms_chains_allow.json 登记理由"
                            % len(ch["steps"]), _loc(ch["file"], chain_id=ch["chain_id"])))
    return out


def _blind_findings(calls):
    """G16：盲区比例超阈值。门看不见的越多，它说的"没问题"越不值钱 —— 必须单独报。"""
    ev = Counter(c["evidence"] for c in calls)
    total = sum(ev.values()) or 1
    unseen = ev["blind"] + ev["sealed"]
    if unseen / float(total) <= BLIND_RED:
        return []
    return [_finding("G16_BLIND_RATIO",
                     "看不见 %d/%d = %.1f%%" % (unseen, total, unseen * 100.0 / total),
                     "盲区比例超过 %.0f%%：这些调用点的能力名/参数门都核不了，"
                     "别再往上加包装器，先把盲区收掉" % (BLIND_RED * 100))]


def _signature_findings(ctx):
    """G17：破坏性签名变更未认领。"""
    out = []
    for d in ctx["signature_changes"]:
        if d["severity"] not in ("P0", "P1"):
            continue
        atom = (ctx["signatures"]["capabilities"].get(d["cap"]) or {}).get("atom")
        out.append(_finding("G17_SIGNATURE_BREAKING", "能力 %s · %s" % (d["cap"], d["kind"]),
                            "%s（认领：--update-signatures --reason \"...\"，并说明受影响调用点）"
                            % d["detail"], _loc(atom=atom)))
    return out


def _rules_second(ctx):
    return (_implicit_chain_findings(ctx) + _blind_findings(ctx["calls"])
            + _signature_findings(ctx))


# ── 汇总 ──
def _collect_findings(ctx):
    out = []
    for fn in (_rules_calls, _contract_gaps, _rules_caps, _rules_declared,
               _rules_deps, _find_cycles, _order_conflicts, _rules_chains,
               _rules_reach, _rules_second):
        out.extend(fn(ctx))
    return out


def _core_metrics(ctx, nodes):
    """规模与覆盖：原子、能力、挂载、接线、覆盖率。"""
    total_caps = sum(len(a["provides"]) for a in ctx["atoms"].values())
    reached = len({c for c in ctx["prov"] if _reached(ctx, c)})
    return {
        "atoms": len(ctx["atoms"]),
        "capabilities": total_caps,
        "capabilities_mounted": sum(len(x["registered"]) for x in nodes),
        "atoms_complete": sum(1 for x in nodes if x["complete"]),
        "atoms_wired": sum(1 for x in nodes if x["wired"]),
        "capabilities_reached": reached,
        "capability_coverage": round(reached / total_caps, 4) if total_caps else 0.0,
        "capabilities_chain_only": len([c for c in ctx["prov"]
                                        if ctx["cap_calls"].get(c, 0) == 0 and c in ctx["chain_used"]]),
    }


def _site_metrics(ctx):
    """调用点与证据：这是 Q4「门看得见多少」的数据源。"""
    calls = ctx["calls"]
    ev = Counter(c["evidence"] for c in calls)
    total = sum(ev.values()) or 1
    unseen = ev["blind"] + ev["sealed"]
    return {
        "call_sites": len(calls),
        "call_sites_raw": ctx["raw_sites"],
        "call_sites_static": ev["verified"] + ev["name-only"],
        "call_sites_dynamic": max(0, ctx["raw_sites"] - len(calls)),
        "unreadable_files": list(ctx["raw_sites_skipped"]),
        "evidence": {k: ev[k] for k in EVIDENCE_LEVELS},
        "evidence_ratio": {k: round(ev[k] / float(len(calls) or 1), 4) for k in EVIDENCE_LEVELS},
        "blind_ratio": round(unseen / float(total), 4),
        "blind_thresholds": {"yellow": BLIND_YELLOW, "red": BLIND_RED},
    }


def _chain_metrics(ctx):
    """组合与可达：这是 Q2「通路」的数据源。"""
    chains = ctx["chains"]
    return {
        "chains": len([c for c in chains if not c.get("dynamic")]),
        "chains_dynamic": sum(1 for c in chains if c.get("dynamic")),
        "chain_steps": sum(len(c["steps"]) for c in chains),
        "chains_by_reach": dict(sorted(Counter(ch.get("reach") for ch in chains
                                               if not ch.get("dynamic")).items())),
        "chains_default": [c["name"] for c in chains if c.get("default")],
        "capabilities_in_chains": len({st["capability"] for c in chains for st in c["steps"]
                                       if st.get("capability")}),
    }


def _decl_metrics(ctx):
    """声明侧：入口清单、签名快照、depends_on。"""
    ents = [e for e in (ctx["entries"].get("entries") or []) if isinstance(e, dict)]
    return {
        "entries": len(ents),
        "entries_missing": bool(ctx["entries"].get("_missing")),
        "signatures": len(ctx["signatures"]["capabilities"]),
        "signature_changes": len([d for d in ctx["signature_changes"]
                                  if d["severity"] in ("P0", "P1")]),
        "declared_dep_edges": len(ctx["declared_deps"]),
        "declared_dep_exercised": sum(1 for s, _d, c in ctx["declared_deps"]
                                      if ctx["call_edges"].get((s, c), 0)),
    }


def _build_metrics(ctx, nodes, edges, findings):
    m = {}
    for part in (_core_metrics(ctx, nodes), _site_metrics(ctx),
                 _chain_metrics(ctx), _decl_metrics(ctx)):
        m.update(part)
    atom_level = [e for e in edges if e["kind"] == "call" and e["from"] != "<编排层>"]
    m["atom_level_call_edges"] = len(atom_level)
    m["topology"] = "star" if not atom_level else "mixed"
    m["rules_fired"] = dict(sorted(Counter(f["rule"] for f in findings).items()))
    m["severity"] = dict(sorted(Counter(f["severity"] for f in findings).items()))
    return m


def _waived_by_question(repo, findings):
    """已登记的存量按问题归类：它们不影响档位，但要计数报出来（否则看着像没登记）。"""
    res = apply_baseline(findings, load_baseline(repo))
    out = {}
    for f in res["waived"]:
        q = QUESTION_OF.get(f["rule"])
        if q:
            out[q] = out.get(q, 0) + 1
    return res, out


def build_graph(repo):
    """建图主流程：抽取 → 索引 → 规则 → 指标。每一步都是独立小函数，便于单测与替换。"""
    ctx = _scan_repo(repo)
    nodes = _build_nodes(ctx)
    edges = _build_edges(ctx)
    findings = _collect_findings(ctx)
    metrics = _build_metrics(ctx, nodes, edges, findings)
    # 四问要按"台账登记之后还剩什么"来判：台账里的 P2/P3 是**已承认的存量**，
    # 把它们算进档位会让 Q1 永远是黄，档位就失去分辨力。
    _res, waived_by_q = _waived_by_question(ctx["repo"], findings)
    metrics["questions"] = questions(_res["open"], metrics, waived=waived_by_q)
    return {
        "schema": SCHEMA,
        "nodes": nodes,
        "edges": edges,
        "call_sites": ctx["calls"],
        "chains": ctx["chains"],
        "chain_source": ctx["chain_source"],
        "entries": ctx["entries"],
        "signatures": ctx["signatures"],
        "signature_changes": ctx["signature_changes"],
        "capability_index": _build_cap_index(ctx),
        "findings": sorted(findings, key=lambda f: (f["rule"], f["subject"])),
        "metrics": metrics,
    }


def _level_of(bad, warn):
    return "red" if bad else ("yellow" if warn else "green")


def _special_case(qs, metrics):
    """两个"不产生 finding 但必须表态"的状态：api-only 组合、盲区黄档。"""
    api_only = (metrics.get("chains_by_reach") or {}).get("api-only", 0)
    if qs["Q2"]["level"] == "green" and api_only:
        qs["Q2"]["level"] = "yellow"
        qs["Q2"]["note"] = "%d 条组合只有库 API 可达（CLI 走不到）" % api_only
    ratio = metrics.get("blind_ratio") or 0.0
    if qs["Q4"]["level"] == "green" and ratio > BLIND_YELLOW:
        qs["Q4"]["level"] = "yellow"
        qs["Q4"]["note"] = "看不见 %.1f%%（阈值 黄 %.0f%% / 红 %.0f%%）" % (
            ratio * 100, BLIND_YELLOW * 100, BLIND_RED * 100)
    return qs


def questions(findings, metrics, waived=None):
    """把散装规则归到四个问题上，各给一个档位。**不压成一个布尔**。

    为什么要分：Q1 能全绿而 Q2 全死（6 条组合在 CLI 上一条都走不到就是这样），
    Q1 能全绿而 Q4 在悄悄恶化。四个问题的失败模式互相独立，一个红绿灯表示不了。

    档位口径：red = 有 P0/P1 未登记；yellow = 只有 P2/P3 或落在"待表态/待跟踪"区；
    green = 没有未登记问题。
    """
    wv = waived if isinstance(waived, dict) else ({"Q1": waived} if waived else {})
    out = {}
    for q in ("Q1", "Q2", "Q3", "Q4"):
        mine = [f for f in findings if QUESTION_OF.get(f["rule"]) == q]
        bad = [f for f in mine if f["severity"] in ("P0", "P1")]
        warn = [f for f in mine if f["severity"] in ("P2", "P3")]
        out[q] = {"name": QUESTIONS[q][0], "level": _level_of(bad, warn),
                  "blocking": len(bad), "tracking": len(warn), "waived": wv.get(q, 0)}
    out = _special_case(out, metrics)
    out["overall"] = "fail" if any(out[q]["level"] == "red"
                                   for q in ("Q1", "Q2", "Q3", "Q4")) else "pass"
    return out


# ────────────────────────── 基线台账 ──────────────────────────
def baseline_path(repo):
    return _p(repo, "config", "atoms_graph_baseline.json")


def load_baseline(repo):
    d = load_json(baseline_path(repo))
    if not isinstance(d, dict):
        return {"schema": BASELINE_SCHEMA, "expires": None, "entries": [], "_missing": True}
    d.setdefault("entries", [])
    d.setdefault("_missing", False)
    return d


def _pick_entry(idx, key, used):
    """同一 key 可能有多条基线（同一问题出现多次）：按出现顺序各配一条，一条管不了全部。"""
    for cand in idx.get(key, []):
        if id(cand) not in used:
            used.add(id(cand))
            return cand
    return None


def _entry_flaws(ent, subject, today):
    """基线条目本身的两条纪律：必须写理由、不许过期。"""
    out = []
    if not ent.get("reason"):
        out.append({"rule": "BASELINE_NO_REASON", "severity": "P1",
                    "subject": subject, "detail": "基线条目没有写理由"})
    exp = ent.get("expires")
    if exp and str(exp) < today.isoformat():
        out.append({"rule": "BASELINE_EXPIRED", "severity": "P1", "subject": subject,
                    "detail": "基线已于 %s 到期，须修掉或续期并写清原因" % exp})
    return out


def _stale_entries(idx, used):
    """台账里已经不再对应任何问题的条目 —— 留着会掩盖新问题，必须报删。"""
    out = []
    for key, ents in idx.items():
        for ent in ents:
            if id(ent) not in used:
                out.append({"rule": "BASELINE_STALE", "severity": "P2",
                            "subject": ent.get("subject") or key,
                            "detail": "这条基线已经没有对应问题了，请从台账删掉（否则台账会掩盖新问题）"})
    return out


def apply_baseline(findings, bl, today=None):
    """三条纪律：① 每条基线必须有 reason + expires ② 过期即红 ③ 条目失效要报删除。
    缺一条，台账就会烂成永久豁免 —— 那是比没有台账更坏的结果。"""
    today = today or date.today()
    idx = defaultdict(list)
    for e in bl.get("entries") or []:
        idx[e.get("key")].append(e)

    waived, open_, problems, used = [], [], [], set()
    for f in findings:
        ent = _pick_entry(idx, f["key"], used)
        if ent is None:
            open_.append(f)
            continue
        problems.extend(_entry_flaws(ent, f["subject"], today))
        waived.append(dict(f, baseline_reason=ent.get("reason"),
                           baseline_expires=ent.get("expires")))
    problems.extend(_stale_entries(idx, used))

    top = bl.get("expires")
    if top and str(top) < today.isoformat():
        problems.append({"rule": "BASELINE_EXPIRED", "severity": "P1",
                         "subject": "整本台账 %s" % top,
                         "detail": "台账整体已到期，须重新梳理存量，不许无限续期"})
    return {"waived": waived, "open": open_, "problems": problems}


def shrink_baseline(bl, findings):
    """台账自动收缩：把**已不再触发**的条目摘掉，返回 (保留, 回收)。

    为什么必须自动：机械动作留给人，久了就没人做，台账会烂成永久豁免 ——
    那比没有台账更坏（新问题会被老条目掩盖）。ArchUnit 的 FreezingArchRule
    就是这么做的：违规修好后存储自动收缩。
    注意：这不是"放宽"，是"收紧"—— 只有确实不再出现的问题才会被摘掉。
    """
    pool = Counter(f.get("key") for f in findings)
    kept, removed = [], []
    for e in (bl.get("entries") or []):
        k = e.get("key")
        if pool.get(k, 0) > 0:
            pool[k] -= 1
            kept.append(e)
        else:
            removed.append(e)
    return kept, removed


def dump_baseline(graph, reason_default, expires, today=None):
    """从当前体检结果生成台账（只记 P2/P3；P0/P1 不许进台账，必须当场修）。"""
    today = today or date.today()
    entries = []
    for f in graph["findings"]:
        if f["severity"] not in ("P2", "P3"):
            continue
        entries.append({"key": f["key"], "rule": f["rule"], "subject": f["subject"],
                        "severity": f["severity"], "reason": reason_default, "expires": expires})
    return {"schema": BASELINE_SCHEMA, "_note": "只登记 P2/P3 存量；P0/P1 一律必须当场修，不进台账。"
                                               "每条必须有 reason 与 expires，过期即红，条目失效要删。",
            "expires": expires, "entries": entries}


# ────────────────────────── 增量范围 ──────────────────────────
def _git(repo, *args):
    try:
        r = subprocess.run(["git"] + list(args), cwd=str(repo), capture_output=True,
                           text=True, errors="ignore", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, (r.stdout or "").strip()


def pick_base(repo, explicit=None):
    null_sha = "0" * 40
    for cand in (explicit, os.environ.get("ATOMS_GRAPH_BASE")):
        if cand and cand.strip() and cand.strip() != null_sha:
            rc, _ = _git(repo, "rev-parse", "--verify", cand.strip() + "^{commit}")
            if rc == 0:
                return cand.strip(), "参数/环境变量"
    rc, _ = _git(repo, "rev-parse", "--verify", "origin/HEAD^{commit}")
    if rc == 0:
        return "origin/HEAD", "默认(origin/HEAD)"
    rc, _ = _git(repo, "rev-parse", "--verify", "HEAD~1^{commit}")
    if rc == 0:
        return "HEAD~1", "默认(HEAD~1)"
    return None, "取不到基线"


def changed_files(repo, base):
    files = set()
    for args in (("diff", "--name-only", "--diff-filter=ACMR", base, "HEAD"),
                 ("diff", "--name-only", "--diff-filter=ACMR"),
                 ("diff", "--name-only", "--cached", "--diff-filter=ACMR")):
        rc, out = _git(repo, *args)
        if rc == 0:
            files.update(x.strip() for x in out.splitlines() if x.strip())
    return sorted(files)


def _touched_atoms(changed, atoms):
    """改动文件落在哪些原子的目录里（按 path 前缀算）。"""
    out = set()
    for rel in changed:
        for name, a in atoms.items():
            prefix = str(a.get("path", "")).replace("\\", "/").strip("/")
            if prefix and (rel == prefix or rel.startswith(prefix + "/")):
                out.add(name)
    return out


def _chain_file(chain_id):
    """chain_id（code:file#fn / tpl:file#name）里的文件部分；取不到返回空串。"""
    if not chain_id or ":" not in chain_id:
        return ""
    return chain_id.split(":", 1)[1].split("#", 1)[0]


def _touches(f, changed_files, changed_atoms):
    """一条问题是否落在这次改动里。定位不到任何文件的全局问题一律判（宁可多报，不漏报）。"""
    if f.get("file") and f["file"] in changed_files:
        return True
    if f.get("atom") and f["atom"] in changed_atoms:
        return True
    if _chain_file(f.get("chain_id")) in changed_files:
        return True
    return f.get("atom") is None and f.get("file") is None and not f.get("chain_id")


def scope(findings, changed, atoms):
    """增量判定：只判改动波及的原子/文件；改公共层则全仓。返回 (在范围, 出范围, 是否全仓, 原因)。"""
    if changed is None:
        return list(findings), [], True, "未判增量（--all 或取不到基线）"
    touched = {c for c in changed if os.path.basename(c) in PUBLIC_LAYER}
    if touched:
        return list(findings), [], True, "改了公共层 %s → 全仓进入硬红区间" % sorted(touched)

    changed_atoms, files = _touched_atoms(changed, atoms), set(changed)
    inside = [f for f in findings if _touches(f, files, changed_atoms)]
    outside = [f for f in findings if not _touches(f, files, changed_atoms)]
    return (inside, outside, False,
            "改动 %d 个文件，波及 %d 个原子" % (len(changed), len(changed_atoms)))


# ────────────────────────── 渲染：Markdown ──────────────────────────
NO_HANDEDIT = "> 本文件由 `scripts/verify_atoms_graph.py --emit` 从代码生成，**请勿手写修改**。\n" \
              "> CI 用 `--check-drift` 比对：生成物与代码不同步即红。改图请改代码再重新生成。\n"


def _fmt_inputs(st):
    if st.get("inputs_kind") == "callable":
        return "λ 动态"
    if not st.get("inputs"):
        return "—"
    return " ".join("%s=%s" % (k, v) for k, v in sorted(st["inputs"].items()))


def _md_overview(graph):
    m = graph["metrics"]
    q = m.get("questions") or {}
    reach = "、".join("%s %d" % (k, v) for k, v in (m["chains_by_reach"] or {}).items()) or "—"
    return [
        "## 一、概览\n", "| 指标 | 值 |", "|---|---|",
        "| 原子 / 能力 | **%d** / **%d** |" % (m["atoms"], m["capabilities"]),
        "| 能力挂载 | **%d/%d**（声明=挂载的原子 %d/%d） |"
        % (m["capabilities_mounted"], m["capabilities"], m["atoms_complete"], m["atoms"]),
        "| 有调用边的原子 | **%d/%d** |" % (m["atoms_wired"], m["atoms"]),
        "| 能力覆盖率 | **%.1f%%**（%d/%d） |"
        % (m["capability_coverage"] * 100, m["capabilities_reached"], m["capabilities"]),
        "| 调用点 | **%d** 处（看得见 %d / 看不见 %d） |"
        % (m["call_sites"], m["call_sites_static"], m["call_sites"] - m["call_sites_static"]),
        "| 组合 | **%d** 条 / **%d** 步（可达：%s） |"
        % (m["chains"], m["chain_steps"], reach),
        "| 四问判定 | Q1 %s ｜ Q2 %s ｜ Q3 %s ｜ Q4 %s |"
        % tuple(q.get(k, {}).get("level", "?").upper() for k in ("Q1", "Q2", "Q3", "Q4")),
        "| 进入组合的能力 | **%d/%d** |" % (m["capabilities_in_chains"], m["capabilities"]),
        "| 原子↔原子调用边 | %d（拓扑 %s） |" % (m["atom_level_call_edges"], m["topology"]),
        "",
    ]


def _md_steps_table(ch):
    if not ch["steps"]:
        return []
    L = ["| # | 步骤 | 能力 | 所在原子 | 静态入参 | 签名 |", "|---|---|---|---|---|---|"]
    for st in ch["steps"]:
        ok = "✅" if st.get("resolved") else "❌ 未解析"
        if st.get("sig_bad"):
            ok = "⚠ 入参不符"
        L.append("| %d | %s | `%s` | %s | `%s` | %s |"
                 % (st.get("order", 0), st.get("step") or "—", st.get("capability") or "?",
                    st.get("atom") or "—", _fmt_inputs(st), ok))
    L.append("")
    return L


def _md_one_chain(ch):
    head = "### %s" % ch["name"]
    if ch["kind"] == "template":
        head += "（模板%s）" % ("·默认" if ch.get("default") else "")
    elif ch["kind"] == "code":
        head += "（代码内）"
    L = [head, "", "- 组合 ID：`%s`" % ch["chain_id"],
         "- 定义位置：`%s`%s" % (ch["file"], (":%d" % ch["line"]) if ch["line"] else "")]
    if not ch.get("dynamic"):
        L.append("- 可达：**%s**%s" % (ch.get("reach") or "declared-only",
                                      ("（入口 %s）" % "、".join(ch["reach_by"]))
                                      if ch.get("reach_by") else ""))
    if ch.get("keywords"):
        L.append("- 命中关键词：%s" % "、".join(ch["keywords"]))
    if ch["dynamic"]:
        L.append("- **静态抽不出步骤**：%s" % ch["reason"])
    L.append("")
    return L + _md_steps_table(ch)


def _md_chains(graph):
    L = ["## 二、组合清单（每一步都能落到真实能力）\n",
         "组合 = 有序能力序列。下表每条组合的每一步都给出：能力名 → 所在原子 → 静态入参 → 定义位置。",
         "`未解析` 的行就是会断的地方；`可达` 一列说明这条组合从哪个入口进得来。\n"]
    for ch in graph["chains"]:
        L.extend(_md_one_chain(ch))
    return L


def _md_reverse(graph):
    L = ["## 三、能力 → 组合 反查（改动影响面）\n",
         "改某条能力之前，先在这里查它被哪些组合用到、哪些调用点依赖它。",
         "`调用点 0 / 组合 0` 的就是未接线能力。\n",
         "| 能力 | 原子 | 调用点 | 被哪些组合用到 |", "|---|---|---|---|"]
    rows = [(len(i["sites"]), len(i["chains"]), cap, i)
            for cap, i in graph["capability_index"].items()]
    rows.sort(key=lambda x: (-x[0], -x[1], x[2]))
    for nsite, _nchain, cap, info in rows:
        chains = "、".join("%s 第%d步" % (c["name"], c["order"]) for c in info["chains"]) or "—"
        L.append("| `%s` | %s | %d | %s |" % (cap, info["atom"], nsite, chains))
    L.append("")
    return L


def _md_entry_table(ent):
    L = ["| 入口 | 类型 | 指向 | 证据（文件#符号） |", "|---|---|---|---|"]
    for e in (ent.get("entries") or []):
        ev = e.get("evidence") or {}
        L.append("| `%s` | %s | %s | `%s#%s`:%s |"
                 % (e.get("id"), e.get("kind"), "、".join(e.get("refs") or []) or "—",
                    ev.get("file"), ev.get("symbol"), ev.get("token")))
    return L


def _md_entries(graph):
    ent = graph.get("entries") or {}
    L = ["## 四、入口与可达性（Q2 通路）\n",
         "组合被声明了，不等于有人走得到。这一节回答「从哪个入口进得来」——",
         "`cli-reachable` 敲命令能走；`api-only` 只有库调用能走（允许，须承认）；"
         "`declared-only` 谁都不通（G14 红）。\n"]
    if ent.get("_missing"):
        L.append("> ⚠ 入口清单 `config/atoms_entries.json` 不存在 → "
                 "所有组合都会被判成 `declared-only`。\n")
    L.extend(_md_entry_table(ent))
    L.extend(["", "| 组合 | 判定 | 入口 |", "|---|---|---|"])
    for ch in graph["chains"]:
        if not ch.get("dynamic"):
            L.append("| %s | **%s** | %s |"
                     % (ch["name"], ch.get("reach"),
                        "、".join(ch.get("reach_by") or []) or "—"))
    L.append("")
    return L


_EV_DESC = {
    "verified": "能力名+参数名都可核（全核）",
    "name-only": "能力名可核，参数被 `**kwargs` 摊开（只核名字）",
    "sealed": "能力名来自变量，但声明了 `dispatch-sealed` 边界（边界已知，目标不可核）",
    "blind": "能力名来自变量且无声明（什么都核不了）",
}


def _md_blind(graph):
    m = graph["metrics"]
    evd = m.get("evidence") or {}
    total = sum(evd.values()) or 1
    L = ["## 五、自知：门看得见多少（Q4）\n",
         "看不见的调用点不会违规 —— 所以门必须把「我看不见多少」当成一等结论说出来。\n",
         "| 证据等级 | 含义 | 数量 | 占比 |", "|---|---|---|---|"]
    for k in EVIDENCE_LEVELS:
        L.append("| `%s` | %s | %d | %.1f%% |"
                 % (k, _EV_DESC[k], evd.get(k, 0), evd.get(k, 0) * 100.0 / total))
    L.extend([
        "",
        "- 看得见（verified+name-only）：**%d/%d**；看不见（sealed+blind）：**%d/%d = %.1f%%**"
        % (m["call_sites_static"], m["call_sites"],
           evd.get("sealed", 0) + evd.get("blind", 0), m["call_sites"], m["blind_ratio"] * 100),
        "- 阈值：黄 %.0f%% / 红 %.0f%%（`sealed` 也计入看不见 —— 边界声明不等于能校验）"
        % (BLIND_YELLOW * 100, BLIND_RED * 100),
        "",
    ])
    return L


def _md_rules(graph):
    fired = graph["metrics"]["rules_fired"]
    L = ["## 六、规则命中\n", "| 组 | 规则 | 级别 | 命中 | 说明 |", "|---|---|---|---|---|"]
    last = None
    for rule, (sev, desc) in RULES.items():
        grp = RULE_GROUP[rule]
        n = fired.get(rule, 0)
        L.append("| %s | `%s` | %s | %s | %s |"
                 % (grp if grp != last else "", rule, sev, ("**%d**" % n) if n else "0", desc))
        last = grp
    L.append("")
    return L


def _md_details(graph):
    L = ["## 七、问题明细\n"]
    if not graph["findings"]:
        return L + ["（无）\n"]
    by_rule = defaultdict(list)
    for f in graph["findings"]:
        by_rule[f["rule"]].append(f)
    for rule, (sev, _desc) in RULES.items():
        items = by_rule.get(rule) or []
        if not items:
            continue
        L.append("### %s（%d 项，%s）\n" % (rule, len(items), sev))
        for f in items[:60]:
            L.append("- `%s` — %s" % (f["subject"], f["detail"]))
        if len(items) > 60:
            L.append("- …… 另 %d 项见 `--json` 输出" % (len(items) - 60))
        L.append("")
    return L


_MD_SECTIONS = (_md_overview, _md_chains, _md_reverse, _md_entries,
                _md_blind, _md_rules, _md_details)


def render_markdown(graph):
    L = ["# 原子能力图谱（生成物）", "", NO_HANDEDIT, ""]
    L.append("完整性的环图快照见 `docs/atoms_graph_snapshot_<日期>.html`（当时生成的静态图，非本门产物）；本文件是**组合追踪**视图，两者同源不同视角。\n")
    for section in _MD_SECTIONS:
        L.extend(section(graph))
    return "\n".join(L)


# ────────────────────────── 渲染：HTML 一张图 ──────────────────────────
GROUP_COLOR = {
    "安全治理": ("#FCEBEB", "#A32D2D"),
    "质量验证": ("#E6F1FB", "#185FA5"),
    "生产编排": ("#E1F5EE", "#0F6E56"),
    "记忆上下文": ("#FAEEDA", "#854F0B"),
    "交付运行": ("#EEEDFE", "#534AB7"),
    "其他": ("#F1EFE8", "#5F5E5A"),
}

_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:28px 32px 48px;font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  color:#1f2937;background:#fff;font-size:13px;line-height:1.6}
h1{font-size:19px;font-weight:500;margin:0 0 4px}
h2{font-size:15px;font-weight:500;margin:30px 0 10px;padding-bottom:6px;border-bottom:1px solid #e5e7eb}
.sub{color:#6b7280;font-size:12px;margin:0 0 18px}
.cards{display:flex;gap:10px;flex-wrap:wrap}
.card{flex:1;min-width:132px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px}
.card b{display:block;font-size:20px;font-weight:500;line-height:1.2}
.card span{color:#6b7280;font-size:11.5px}
.grid{display:flex;flex-wrap:wrap;gap:6px}
.atom{display:inline-block;border:1px solid;border-radius:7px;padding:3px 8px;font-size:11.5px;
  font-family:ui-monospace,Consolas,monospace}
.atom i{font-style:normal;font-size:10px;opacity:.65;margin-left:4px}
.grp{margin:10px 0 4px;color:#6b7280;font-size:11.5px}
.chain{border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px;margin-bottom:12px;background:#fff}
.chain-h{display:flex;align-items:baseline;gap:8px;margin-bottom:10px}
.chain-h b{font-weight:500;font-size:13.5px}
.chain-h code{color:#6b7280;font-size:11px}
.flow{display:flex;align-items:stretch;gap:0;flex-wrap:wrap}
.step{border:1px solid;border-radius:8px;padding:6px 10px;min-width:132px;font-size:11.5px}
.step small{display:block;color:#6b7280;font-size:10.5px;font-family:ui-monospace,Consolas,monospace}
.arrow{align-self:center;color:#9ca3af;padding:0 6px;font-size:12px}
.bad{border-color:#E24B4A;background:#FCEBEB}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #eef0f2}
th{color:#6b7280;font-weight:500;background:#fafafa}
code{font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
.tag{display:inline-block;border-radius:4px;padding:1px 6px;font-size:10.5px;font-family:ui-monospace,Consolas,monospace}
"""


def _atom_chip(n):
    fill, stroke = GROUP_COLOR.get(n["group"], GROUP_COLOR["其他"])
    style = "background:%s;border-color:%s;color:%s" % (fill, stroke, stroke)
    if n["wired"]:
        style += ";font-weight:500"
    else:
        style += ";border-style:dashed;opacity:.85"
    extra = "" if n["complete"] else " ⚠"
    return ('<span class="atom" style="%s" title="%s｜%s｜能力 %d｜调用 %d">%s<i>%d</i>%s</span>'
            % (style, n["id"], n["domain"], len(n["provides"]), n["ncall"], n["id"], len(n["provides"]), extra))


def _html_head(graph):
    m = graph["metrics"]
    P = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         "<title>原子能力图谱 · codeagent-minimal</title>",
         "<style>%s</style></head><body>" % _CSS,
         "<h1>原子能力图谱</h1>",
         '<p class="sub">由 <code>scripts/verify_atoms_graph.py --emit</code> 从代码生成，'
         "请勿手写。改图请改代码后重新生成。</p>",
         '<div class="cards">']
    for label, val in (("原子", m["atoms"]), ("能力", m["capabilities"]),
                       ("能力覆盖", "%.1f%%" % (m["capability_coverage"] * 100)),
                       ("组合 / 步", "%d / %d" % (m["chains"], m["chain_steps"])),
                       ("CLI 可达组合", "%d/%d" % (
                           (m["chains_by_reach"] or {}).get("cli-reachable", 0), m["chains"])),
                       ("看不见的调用点", "%.1f%%" % (m.get("blind_ratio", 0) * 100)),
                       ("问题", sum(m["rules_fired"].values()))):
        P.append('<div class="card"><b>%s</b><span>%s</span></div>' % (val, label))
    P.append("</div>")
    return P


def _html_atoms(graph):
    P = ["<h2>一、全部原子（完整性）</h2>",
         '<p class="sub">实线=有调用路径，虚线=待接线（插槽就绪，不是残件）。'
         "数字是能力条数，⚠ 表示挂载与声明不一致。</p>"]
    by_group = defaultdict(list)
    for n in graph["nodes"]:
        by_group[n["group"]].append(n)
    for g in GROUP_ORDER:
        items = sorted(by_group.get(g) or [], key=lambda x: x["id"])
        if not items:
            continue
        wired = sum(1 for x in items if x["wired"])
        P.append('<div class="grp">%s　%d 个在位 · %d 已接线</div>' % (g, len(items), wired))
        P.append('<div class="grid">%s</div>' % "".join(_atom_chip(n) for n in items))
    return P


_REACH_COLOR = {"cli-reachable": "#0F6E56", "api-only": "#854F0B",
                "declared-only": "#A32D2D", "passthrough": "#9ca3af"}


def _step_style(st, nodes):
    """一步的配色：解析不到 = 红框（走到这里必断）；解析到了按归属原子的大类上色。"""
    if not st.get("resolved"):
        return "step bad", "#FCEBEB", "#A32D2D"
    node = next((x for x in nodes if x["id"] == st.get("atom")), None)
    fill, stroke = GROUP_COLOR.get(node["group"], GROUP_COLOR["其他"]) if node \
        else GROUP_COLOR["其他"]
    return "step", fill, stroke


def _html_steps(ch, nodes):
    P = ['<div class="flow">']
    for i, st in enumerate(ch["steps"]):
        cls, fill, stroke = _step_style(st, nodes)
        P.append('<div class="%s" style="background:%s;border-color:%s">%s<small>%s</small></div>'
                 % (cls, fill, stroke, st.get("capability") or "?", st.get("atom") or "未解析"))
        if i < len(ch["steps"]) - 1:
            P.append('<div class="arrow">→</div>')
    P.append("</div>")
    return P


def _html_one_chain(ch, nodes):
    kind = "模板" if ch["kind"] == "template" else "代码内"
    reach = ch.get("reach") or "passthrough"
    P = ['<div class="chain">',
         '<div class="chain-h"><b>%s</b><span class="tag" style="background:#f3f4f6;color:#6b7280">%s</span>'
         '<span class="tag" style="background:#f9fafb;color:%s">%s</span>'
         '<code>%s</code></div>'
         % (ch["name"], kind, _REACH_COLOR.get(reach, "#6b7280"), reach, ch["chain_id"])]
    if ch["dynamic"]:
        P.append('<div class="sub">静态抽不出步骤：%s</div>' % ch["reason"])
    else:
        P.extend(_html_steps(ch, nodes))
    P.append("</div>")
    return P


def _html_chains(graph):
    P = ["<h2>二、组合链（组合可追踪）</h2>",
         '<p class="sub">每条组合的每一步都落到真实能力；红框 = 这一步解析不到能力（走到这里必断）。</p>']
    for ch in graph["chains"]:
        P.extend(_html_one_chain(ch, graph["nodes"]))
    return P


def _html_reverse(graph):
    rows = [(len(v["sites"]), len(v["chains"]), k, v)
            for k, v in graph["capability_index"].items()]
    rows.sort(key=lambda x: (-x[0], -x[1], x[2]))
    P = ["<h2>三、能力被哪些组合用到（反查）</h2>",
         "<table><tr><th>能力</th><th>原子</th><th>调用点</th><th>被哪些组合用到</th></tr>"]
    for nsite, _nchain, cap, info in rows[:45]:
        chains = "、".join("%s 第%d步" % (c["name"], c["order"]) for c in info["chains"]) or "—"
        P.append("<tr><td><code>%s</code></td><td>%s</td><td>%d</td><td>%s</td></tr>"
                 % (cap, info["atom"], nsite, chains))
    P.append("</table>")
    return P


_HTML_EV_DESC = {"verified": "名字 + 参数 + 占位符，全核",
                 "name-only": "只核名字（参数被 **kwargs 摊开）",
                 "sealed": "边界已知，目标不可核",
                 "blind": "什么都核不了"}


def _html_blind(graph):
    m = graph["metrics"]
    evd = m.get("evidence") or {}
    tot = sum(evd.values()) or 1
    P = ["<h2>四、自知：门看得见多少</h2>",
         '<p class="sub">看不见的调用点不会违规，所以必须单独报。'
         "sealed = 包装器声明了转发边界但仍不能核到目标，所以也算看不见。</p>",
         "<table><tr><th>证据等级</th><th>数量</th><th>占比</th><th>门的判定能力</th></tr>"]
    for k in EVIDENCE_LEVELS:
        P.append("<tr><td><code>%s</code></td><td>%d</td><td>%.1f%%</td><td>%s</td></tr>"
                 % (k, evd.get(k, 0), evd.get(k, 0) * 100.0 / tot, _HTML_EV_DESC[k]))
    P.append("</table>")
    return P


def _html_rules(graph):
    fired = graph["metrics"]["rules_fired"]
    P = ["<h2>五、规则命中</h2>",
         "<table><tr><th>组</th><th>规则</th><th>级别</th><th>命中</th><th>说明</th></tr>"]
    for rule, (sev, desc) in RULES.items():
        n = fired.get(rule, 0)
        P.append("<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>"
                 % (RULE_GROUP[rule], rule, sev, ("<b>%d</b>" % n) if n else "0", desc))
    P.append("</table>")
    return P


_HTML_SECTIONS = (_html_atoms, _html_chains, _html_reverse, _html_blind, _html_rules)


def render_html(graph):
    P = _html_head(graph)
    for section in _HTML_SECTIONS:
        P.extend(section(graph))
    P.append("</body></html>")
    return "".join(P)
