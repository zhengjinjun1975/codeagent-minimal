#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""constitution_gate.py — 代码宪法的执法者（强宪法 P0 / 重核心 P1 按阈值判）。

阈值单点：config/constitution_thresholds.json（改阈值只改那里）。
例外机制：越限处（同一行或上一行）写 `# 宪法例外: <理由>` → 放行并计入台账；**没有标记就是红**。

用法：
    python scripts/constitution_gate.py <文件或目录> [--json]
退出码：0 = 全过（含"已登记的例外"）；1 = 有未登记的越限。

只做"机器可判"的那部分：P0 全部 + P1 里能静态量的（长度/参数/嵌套/圈复杂度/新依赖提示）。
P1 里"契约调用点""diff 量级"这类需要跨文件/git 的，本门只在 --json 里给出提示位，由评审承接。
"""
import argparse
import ast
import fnmatch
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THRESHOLD_FILE = os.path.join(ROOT, "config", "constitution_thresholds.json")
STDLIB_OK = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
SKIP_DIRS = {"__pycache__", "_impl_output", ".git", ".taskstate", "node_modules", "lab_data", ".venv", "vendor"}
CRED_PATTERNS = [
    (r"\bsk-[A-Za-z0-9\-_]{16,}", "疑似明文 API key(sk-…)"),
    (r"(?i)\bpassword\s*=\s*[\"'][^\"']{3,}[\"']", "明文口令"),
    (r"(?i)\b(api_key|apikey|secret|token)\s*=\s*[\"'][A-Za-z0-9\-_]{12,}[\"']", "明文密钥/令牌"),
]
# 明显是样例/假值的形态：门不当它真凭据（否则测试夹具与文档示例会把门淹掉）
FAKE_CRED_RE = re.compile(r"(abcdef|example|dummy|fake|xxxx|0{6,}|1234)", re.I)
PLACEHOLDER_RE = re.compile(r"(TODO|FIXME|XXX|待补|待实现)")
IGNORE_FILE = ".constitutionignore"
# 仓里既有的夹具约定（agents/test/code-test/project_verify.py 用的是同一套）：文件名前缀即夹具
FIXTURE_PREFIXES = ("bad_sample",)
CTRL = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)


def load_thresholds(path=None):
    with io.open(path or THRESHOLD_FILE, encoding="utf-8") as f:
        return json.load(f)


def line_of(text, n):
    lines = text.splitlines()
    return lines[n - 1] if 1 <= n <= len(lines) else ""


def is_waived(text, lineno, marker):
    """越限处同一行或上一行有例外标记 → 放行。"""
    for n in (lineno, lineno - 1):
        if marker in line_of(text, n):
            return line_of(text, n).split(marker, 1)[1].strip() or "(未写理由)"
    return None


def _empty_body(body):
    """函数体是否为空壳：只有 pass / ... / 文档字符串。"""
    real = [n for n in body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                    and isinstance(n.value.value, str))]
    if not real:
        return True
    if len(real) == 1:
        n = real[0]
        if isinstance(n, ast.Pass):
            return True
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and n.value.value is Ellipsis:
            return True
    return False


# 宪法例外: 三项规则各自独立判，合并成表驱动反而更难读（每条规则的回调才是真抽象）
def _except_is_silent(node):
    body = node.body
    if all(isinstance(n, ast.Pass) for n in body):
        return True
    if len(body) == 1 and isinstance(body[0], ast.Return):
        v = body[0].value
        if v is None or (isinstance(v, ast.Constant) and (v.value is None or v.value is False)):
            return True
        if isinstance(v, (ast.Dict, ast.List, ast.Tuple, ast.Set)) and not getattr(v, "elts", None) \
                and not getattr(v, "keys", None):
            return True
    return False


# 宪法例外: 只做"数分支"的纯函数，把四种节点类型拆到四个小函数只会增加跳转次数
def _complexity(node):
    cc = 1
    for sub in ast.walk(node):
        if isinstance(sub, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)):
            cc += 1
        elif isinstance(sub, ast.BoolOp):
            cc += max(0, len(sub.values) - 1)
        elif isinstance(sub, (ast.comprehension,)) and sub.ifs:
            cc += len(sub.ifs)
    return cc


def _nesting(node, depth=0):
    worst = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, CTRL):
            worst = max(worst, _nesting(child, depth + 1))
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                                    # 嵌套定义另算
        else:
            worst = max(worst, _nesting(child, depth))
    return worst


def _same_drive(a, b):
    try:
        os.path.relpath(a, b)
        return True
    except ValueError:                      # 宪法例外: 跨盘符探测本身要吞掉 ValueError（不同盘无法取相对路径）
        return False


def _is_abstract_stub(node):
    """抽象方法 / Protocol 桩：留空是它的定义，不算"空壳交付"。

    判据：@abstractmethod（含 @abc.abstractmethod）装饰；或所在类继承了 ABC/Protocol。
    """
    for d in node.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        name = getattr(f, "attr", None) or getattr(f, "id", None)
        if name == "abstractmethod":
            return True
    return False


# 宪法例外: 单文件体检的调度函数，把阈值/例外标记/收集器三个状态拆到多个函数只会增加依赖（P1-6 深模块）
def check_file(path, th, root=ROOT):
    text = io.open(path, encoding="utf-8", errors="replace").read()
    marker = th["例外机制"]["标记"]
    p1 = th["P1_阈值"]
    found, waived, hints = [], [], []

    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        # 语法错误的文件也要按同一形状返回（曾经这里少返回一个 hints 导致门直接崩，见改进记录）
        return [{"tier": "P0", "kind": "不能解析", "line": e.lineno or 0, "detail": str(e),
                 "file": os.path.relpath(path, root) if _same_drive(path, root) else os.path.abspath(path)}], [], []

    def add(tier, kind, lineno, detail):
        reason = is_waived(text, lineno, marker)
        try:
            rel = os.path.relpath(path, root)
        except ValueError:                    # 跨盘符（如目标在 C:，仓库在 D:）
            rel = os.path.abspath(path)
        item = {"tier": tier, "kind": kind, "line": lineno, "detail": detail, "file": rel}
        (waived if reason else found).append(dict(item, reason=reason) if reason else item)

    # ── P0-1 死码：导入名在文件其余部分 0 引用 ──
    import_re = re.compile(r"(?<![\w.])(%s)\b")
    imported = {}
    import_lines = {}
    noqa_lines = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue                                   # __future__ 导入从不被"引用"，不算死码
            for a in node.names:
                nm = (a.asname or a.name).split(".")[0]
                imported.setdefault(nm, node.lineno)
                import_lines.setdefault(nm, node.lineno)
    # 作者显式声明过的 import（# noqa）与 TYPE_CHECKING 内的注解专用导入：都不当死码
    for i, l in enumerate(text.splitlines(), 1):
        if "# noqa" in l:
            noqa_lines.add(i)
    for nm, ln in list(imported.items()):
        if ln in noqa_lines:
            del imported[nm]
    body_wo_imports = "\n".join(
        l for i, l in enumerate(text.splitlines(), 1) if i not in set(import_lines.values()))
    for nm, ln in imported.items():
        if nm == "*":
            continue
        # 注意：**不剔除字符串里的出现**——`__all__ = ["path"]`、注解字符串 "Decimal" 这类都算"用过"。
        # 宁可漏报（少假阳性）也不冤枉正经代码：P0 硬门不能靠"我觉得像死码"来判。
        if not re.search(r"(?<![\w.])" + re.escape(nm) + r"\b", body_wo_imports):
            add("P0", "死导入", ln, "导入 %r 在本文件内 0 引用" % nm)

    # 死定义只判**私有名**，且只作**提示**：跨文件的字符串查表调用（eng("_name")）单文件证不了，
    # 不可证的东西不配当 P0 硬门槛（实测踩过：被另一处的字符串查表按名字调用）
    defined = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined[node.name] = node.lineno
    for nm, ln in defined.items():
        if not nm.startswith("_") or nm.startswith("__"):
            continue
        uses = len(re.findall(r"(?<![\w.])" + re.escape(nm) + r"\b", text))
        if uses <= 1:
            hints.append({"tier": "P1", "kind": "疑似死定义(待考证)", "line": ln,
                          "file": os.path.relpath(path, root) if _same_drive(path, root) else os.path.abspath(path),
                          "detail": "私有定义 %r 在本文件内仅出现 %d 次；可能被别的文件按名字调用，需人工确认" % (nm, uses)})

    # ── P0-2 静默失败 ──
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _except_is_silent(node):
            add("P0", "静默吞错", node.lineno, "except 体只有 pass/返回空值，未留痕")

    # ── P0-3 占位/空壳 ──
    import tokenize
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and PLACEHOLDER_RE.search(tok.string):
                # 排除小节分隔线与说明性长注释里的同类字样（假阳性）
                body = tok.string.lstrip("# ").strip()
                if "──" in tok.string or len(body) > 50:
                    continue
                add("P0", "占位标记", tok.start[0], tok.string.strip()[:60])
    except tokenize.TokenError:
        # 注释扫不动就别装作扫过了：显式记一条，让门红（静默跳过=自己违反 P0-2）
        add("P0", "注释扫描失败", 1, "tokenize 无法解析该文件，占位标记未检查")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _empty_body(node.body):
            if _is_abstract_stub(node):
                continue                                  # 抽象方法/协议桩：留空是它的定义
            add("P0", "空壳函数", node.lineno, "%s() 只有 pass/省略号/文档字符串" % node.name)

    # ── P0-5 明文凭据（样例/假值形态不计，避免夹具与文档示例淹掉真信号）──
    for pat, why in CRED_PATTERNS:
        for m in re.finditer(pat, text):
            if FAKE_CRED_RE.search(m.group(0)):
                hints.append({"tier": "P0", "kind": "样例凭据(未计)", "line": text[:m.start()].count("\n") + 1,
                              "file": os.path.relpath(path, root) if _same_drive(path, root) else os.path.abspath(path),
                              "detail": "看着像样例值，未按真凭据计入：%s" % m.group(0)[:24]})
                continue
            ln = text[:m.start()].count("\n") + 1
            add("P0", "明文凭据", ln, why)

    # ── P1-1 阈值：长度/参数/嵌套/圈复杂度 ──
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        ln = node.lineno
        n_lines = (node.end_lineno or ln) - ln + 1
        if n_lines > p1["fn_max_lines"]:
            add("P1", "函数过长", ln, "%s() %d 行 > %d" % (node.name, n_lines, p1["fn_max_lines"]))
        n_args = len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
        if n_args > p1["fn_max_args"]:
            add("P1", "参数过多", ln, "%s() %d 个 > %d" % (node.name, n_args, p1["fn_max_args"]))
        depth = _nesting(node)
        if depth > p1["nest_max_depth"]:
            add("P1", "嵌套过深", ln, "%s() 嵌套 %d 层 > %d" % (node.name, depth, p1["nest_max_depth"]))
        cc = _complexity(node)
        if cc > p1["cc_max"]:
            add("P1", "圈复杂度超标", ln, "%s() cc=%d > %d" % (node.name, cc, p1["cc_max"]))

    # ── P1-2 新依赖提示（非标准库导入；**提示**，默认不判红，--strict 才计入）──
    if p1.get("new_deps_max", 0) == 0 and STDLIB_OK:
        local = {os.path.splitext(f)[0] for f in os.listdir(os.path.dirname(path) or ".")}
        for node in tree.body:
            top = None
            if isinstance(node, ast.Import) and node.names:
                top = node.names[0].name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                top = node.module.split(".")[0]
            if top and top not in STDLIB_OK and top not in local:
                hints.append({"tier": "P1", "kind": "第三方依赖", "line": node.lineno,
                              "file": os.path.relpath(path, root) if _same_drive(path, root) else os.path.abspath(path),
                              "detail": "引入第三方 %r（须答'标准库为什么不行'）" % top})

    return found, waived, hints


def iter_files(target):
    if os.path.isfile(target):
        yield target
        return
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def load_ignore(root=ROOT):
    """夹具/生成物排除清单：.constitutionignore（每行一个 glob，支持 # 注释）。

    另外内置仓里既有的约定：文件名以 bad_sample 开头的即夹具（project_verify.py 同款口径）。
    """
    pats = list(FIXTURE_PREFIXES) if FIXTURE_PREFIXES else []
    p = os.path.join(root, IGNORE_FILE)
    if os.path.isfile(p):
        for line in io.open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def is_ignored(path, pats, root=ROOT):
    base = os.path.basename(path)
    try:
        rel = os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        rel = path.replace("\\", "/")
    for pat in pats:
        if pat in FIXTURE_PREFIXES:
            # 内置夹具约定按「文件名前缀」判（与 project_verify.py 同款口径）。
            # ⚠️ 不能交给下面的 fnmatch —— 无通配符的模式要求**全等**，"bad_sample" 永远匹配不上 bad_sample.py。
            if base.startswith(pat):
                return pat
            continue
        if fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.rstrip("/") + "/*"):
            return pat
        if pat.endswith("*") and base.startswith(pat[:-1]):
            return pat
    return None


# 宪法例外: CLI 入口（读阈值→遍历文件→分档打印），拆开要把三类结果集来回传
def main():
    ap = argparse.ArgumentParser(description="代码宪法执法者（P0 硬门槛 / P1 阈值）")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="把'提示'类（如第三方依赖）也计入退出码")
    ap.add_argument("--thresholds", default=None)
    args = ap.parse_args()

    th = load_thresholds(args.thresholds)
    pats = load_ignore()
    all_found, all_waived, all_hints, n_files, excluded = [], [], [], 0, []
    for fp in iter_files(args.target):
        if os.path.basename(fp).startswith("_"):
            continue
        why = is_ignored(fp, pats)
        if why:
            excluded.append({"file": os.path.relpath(fp, ROOT) if _same_drive(fp, ROOT) else fp, "by": why})
            continue
        n_files += 1
        f, w, h = check_file(fp, th)
        all_found += f
        all_waived += w
        all_hints += h

    if args.json:
        print(json.dumps({"files": n_files, "violations": all_found, "hints": all_hints,
                          "waived": all_waived, "excluded": excluded},
                         ensure_ascii=False, indent=1))
        return 1 if all_found or (args.strict and all_hints) else 0

    print("宪法执法者 · 强宪法(P0)/重核心(P1)  目标: %s  文件数: %d" % (args.target, n_files))
    p0 = [v for v in all_found if v["tier"] == "P0"]
    p1 = [v for v in all_found if v["tier"] == "P1"]
    for label, items in (("P0 硬门槛（违反即不合格）", p0), ("P1 阈值（越限须给理由或登记例外）", p1)):
        print("\n%s：%d 条" % (label, len(items)))
        for v in items[:25]:
            print("  %-14s %s:%s  %s" % (v["kind"], v["file"], v["line"], v["detail"]))
        if len(items) > 25:
            print("  …（另有 %d 条，--json 看全部）" % (len(items) - 25))
    if all_waived:
        print("\n已登记例外（放行，计入台账）：%d 条" % len(all_waived))
        for v in all_waived[:10]:
            print("  %-14s %s:%s  理由: %s" % (v["kind"], v["file"], v["line"], v["reason"]))
    if all_hints:
        print("\n提示（默认不判红，--strict 计入）：%d 条" % len(all_hints))
        for v in all_hints[:10]:
            print("  %-14s %s:%s  %s" % (v["kind"], v["file"], v["line"], v["detail"]))
    print("\n结论: %s" % ("PASS（无未登记越限）" if not all_found else "FAIL —— %d 条未登记越限" % len(all_found)))
    return 1 if all_found else 0


if __name__ == "__main__":
    sys.exit(main())
