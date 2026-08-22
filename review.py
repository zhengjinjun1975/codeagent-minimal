#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CodeReview Minimal — 给"审代码但不写代码的人"的代码审查工具

一个自包含、零第三方依赖的代码审查器。面向技术负责人、QA、审计者、
接收别人代码但不需要自己写代码的人。

特性:
- 静态分析(纯标准库, 不依赖任何第三方包, 不调模型): 语法 / 未用import /
  圈复杂度 / 命名规范 / 安全(SQL注入/命令注入/eval-exec/硬编码密钥/反序列化)
- 可选 LLM 增强审查: 过度工程检测 + 质量问题 + 可证伪改进建议
  (OpenAI 兼容接口, 配置 LLM_API_KEY / LLM_BASE_URL 启用; 不配则纯静态)

用法:
    python review.py <文件或目录>        # 审查单文件或整个目录的 .py
    python review.py <文件> --json       # 输出 JSON
    python review.py <目录> --score-only # 只输出每个文件得分

配置(可选 LLM 审查):
    export LLM_API_KEY=...               # OpenAI 兼容 key
    export LLM_BASE_URL=...              # 默认 https://api.openai.com/v1/chat/completions
    export LLM_MODEL=...                 # 默认 gpt-4o-mini
    export LLM_REVIEW=1                  # 显式启用模型审查(不配 key 则跳过)
"""
import ast
import re
import json
import sys
import os
import argparse
import urllib.request
from pathlib import Path

__version__ = "0.1.1"

# ═══════════════════════════════════════════════════
# 静态分析 (零模型, 纯标准库)
# ═══════════════════════════════════════════════════

def _static_check_syntax(content: str) -> list:
    try:
        ast.parse(content)
        return []
    except SyntaxError as e:
        return [{"severity": "critical", "title": f"语法错误: {e.msg}",
                 "line": e.lineno, "suggestion": str(e)}]

def _static_check_imports(tree, content: str) -> list:
    issues = []
    imports = {}
    used_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports[alias.asname or alias.name] = node.lineno
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.add(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used_names.add(node.value.id)
    for name, lineno in imports.items():
        if name.split(".")[0] not in used_names:
            issues.append({"severity": "minor", "title": f"未使用的 import: {name}",
                           "line": lineno, "suggestion": "删除未使用的 import"})
    return issues

def _static_check_complexity(tree, max_complexity: int = 10) -> list:
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            c = 1
            branches = 0
            for n in ast.walk(node):
                if isinstance(n, (ast.If, ast.While, ast.For, ast.ExceptHandler,
                                  ast.And, ast.Or, ast.Assert, ast.Try)):
                    c += 1
                    branches += 1
            if c > max_complexity:
                kind = "分支复杂" if branches > 5 else "整体复杂"
                issues.append({"severity": "major", "title": f"圈复杂度 {c} > {max_complexity} ({kind}): {node.name}",
                               "line": node.lineno, "suggestion": f"分支多可拆分；纯长逻辑可用 --max-complexity 放宽"})
    return issues

def _static_check_naming(tree) -> list:
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            if not re.match(r'^[a-z_]\w*$', node.name):
                issues.append({"severity": "minor", "title": f"函数名建议小写: {node.name}",
                               "line": node.lineno, "suggestion": f"改名 {node.name.lower()}"})
        elif isinstance(node, ast.ClassDef):
            if not re.match(r'^[A-Z]\w*$', node.name):
                issues.append({"severity": "minor", "title": f"类名建议大写开头: {node.name}",
                               "line": node.lineno, "suggestion": f"改名 {node.name.capitalize()}"})
    return issues

def _static_check_bugs(tree, content: str, strict_undefined: bool = False) -> list:
    """软件 BUG 检测：裸 except/可变默认参数/==None；undefined-name 默认关闭(strict 才查)。"""
    issues = []
    # 裸 except / 空 except
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append({"severity": "major", "title": "裸 except（吞掉所有异常）",
                           "line": node.lineno, "suggestion": "指定异常类型，如 except ValueError:"})
    # 可变默认参数（经典 bug：共享可变对象）
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.args.defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    issues.append({"severity": "major", "title": f"可变默认参数: {node.name}()",
                                   "line": node.lineno, "suggestion": "用 None 作默认，函数内初始化"})
    # == None 应 is None
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and comp.value is None:
                    issues.append({"severity": "minor", "title": "== None 应写 is None",
                                   "line": node.lineno, "suggestion": "用 `is None` 判断"})
    # 未定义名（默认关闭，启发式易误报，仅 strict 启用）
    if strict_undefined:
        defined = set()
        loaded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                defined.add(node.name)
                for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                    defined.add(a.arg)
                if node.args.vararg:
                    defined.add(node.args.vararg.arg)
                if node.args.kwarg:
                    defined.add(node.args.kwarg.arg)
            elif isinstance(node, ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                if isinstance(node.target, ast.Name):
                    defined.add(node.target.id)
            elif isinstance(node, ast.ExceptHandler):
                if node.name:
                    defined.add(node.name)
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for g in node.generators:
                    if isinstance(g.target, ast.Name):
                        defined.add(g.target.id)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    defined.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    defined.add(a.asname or a.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
        builtins = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
        for n in loaded - defined - builtins - {"self", "cls", "__name__", "__file__"}:
            if n in {"if", "for", "in", "or", "and", "not"}:
                continue
            issues.append({"severity": "info", "title": f"可能未定义: {n}", "line": 0,
                           "suggestion": f"确认 {n} 已定义或导入（启发式，可能误报）"})
    return issues


def _static_check_architecture(content: str) -> list:
    """架构稳健评估：文件过大、函数过长、import 依赖过多。"""
    issues = []
    lines = content.split("\n")
    if len(lines) > 500:
        issues.append({"severity": "major", "title": f"文件过大({len(lines)}行)",
                       "line": 0, "suggestion": "考虑拆分为多模块"})
    try:
        tree = ast.parse(content)
        imports = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
        if imports > 15:
            issues.append({"severity": "minor", "title": f"import 依赖过多({imports}个)",
                           "line": 0, "suggestion": "检查是否过度依赖"})
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                size = (node.end_lineno or node.lineno) - node.lineno
                if size > 60:
                    issues.append({"severity": "major", "title": f"函数过长({size}行): {node.name}",
                                   "line": node.lineno, "suggestion": "拆分为多个小函数"})
    except SyntaxError:
        pass
    return issues


def _static_check_network(content: str) -> list:
    """网络安全隐患：SSRF/明文HTTP/URL含凭证/网络数据执行/不安全配置。"""
    issues = []
    # SSRF：请求 URL 来自变量（若用户可控则 SSRF 风险）
    if re.search(r'(requests\.(get|post|put|delete|head)\s*\(\s*[a-zA-Z_][a-zA-Z0-9_]*)', content):
        issues.append({"severity": "major", "title": "SSRF 风险(请求URL为变量)",
                       "line": 0, "suggestion": "若 URL 来自用户输入，攻击者可探测内网；校验协议/域名白名单"})
    if re.search(r'urllib\.request\.urlopen\s*\(\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\)', content):
        issues.append({"severity": "major", "title": "SSRF 风险(urlopen 变量URL)",
                       "line": 0, "suggestion": "校验 URL 协议与域名，防内网探测"})
    # 明文 HTTP（非 TLS）
    if re.search(r'["\']http://[^"\'\s]+', content) and not re.search(r'https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)', content):
        issues.append({"severity": "major", "title": "明文 HTTP（应用 HTTPS）",
                       "line": 0, "suggestion": "生产环境用 https://，避免明文传输敏感数据"})
    # URL 内嵌凭证（http://user:pass@）
    if re.search(r'https?://[^@\s/:]+:[^@\s/]+@', content):
        issues.append({"severity": "critical", "title": "URL 内嵌明文凭证",
                       "line": 0, "suggestion": "凭证不要写进 URL，用环境变量/密钥管理"})
    # 网络获取数据直接执行（eval/exec/__import__ 接收网络数据）
    if re.search(r'(response\.text|\.content|requests\.get[^)]*\))\s*[^\n]{0,40}(eval|exec|__import__)', content, re.S):
        issues.append({"severity": "critical", "title": "网络数据直接执行(eval/exec)",
                       "line": 0, "suggestion": "不可信网络数据不要 eval/exec，用安全解析"})
    # 不安全配置
    if re.search(r'DEBUG\s*=\s*True', content):
        issues.append({"severity": "major", "title": "DEBUG=True 泄漏调试信息",
                       "line": 0, "suggestion": "生产环境关闭 DEBUG"})
    if re.search(r'ALLOWED_HOSTS\s*=\s*\[?\s*["\']\*', content):
        issues.append({"severity": "major", "title": "ALLOWED_HOSTS=*（Host 头注入）",
                       "line": 0, "suggestion": "限定允许的 Host"})
    # shell 网络命令（curl/wget）带变量
    if re.search(r'(curl|wget)\b[^)\n]{0,40}[\+\{]', content):
        issues.append({"severity": "major", "title": "shell 网络命令拼接",
                       "line": 0, "suggestion": "避免用 shell 拼 curl/wget，用参数列表"})
    return issues


def _static_check_security(content: str) -> list:
    issues = []
    # SQL 注入（区分大小写匹配 SQL 关键字，避免 list.insert/dict.update 误判）
    if re.search(r'\b(SELECT|INSERT|UPDATE|DELETE)\b.*?(f["\']|\+\s*["\'a-zA-Z_]|%["\']|\.format\(|\{[^}]*\})', content, re.S):
        issues.append({"severity": "critical", "title": "SQL 注入风险",
                       "suggestion": "用参数化查询/占位符，避免将变量直接拼进 SQL"})
    if re.search(r'subprocess\.[a-z]+\([^)]*shell\s*=\s*True', content, re.I):
        issues.append({"severity": "critical", "title": "命令注入风险(shell=True)",
                       "suggestion": "避免 shell=True；用参数列表传命令，勿拼接用户输入"})
    if re.search(r'os\.system\s*\([^)]*[\+\{]', content):
        issues.append({"severity": "major", "title": "命令拼接风险",
                       "suggestion": "os.system 传动态字符串易注入，改用 subprocess 参数列表"})
    if re.search(r'os\.system\s*\(\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\)', content):
        issues.append({"severity": "major", "title": "命令注入风险(os.system变量)",
                       "suggestion": "os.system(变量) 会执行变量内容，改用 subprocess.run 参数列表"})
    if re.search(r'\beval\s*\([^)]*\)|\bexec\s*\([^)]*\)', content):
        issues.append({"severity": "major", "title": "不安全的 eval/exec",
                       "suggestion": "避免对不可信输入执行 eval/exec；用 ast.literal_eval 等安全替代"})
    if re.search(r'\b(password|passwd|secret|api_key|apikey|token|client_secret)\s*=\s*["\'][^"\']{6,}', content, re.I):
        issues.append({"severity": "major", "title": "硬编码密钥/密码",
                       "suggestion": "密钥不要写死在代码，改用环境变量/配置文件"})
    if re.search(r'pickle\.loads|yaml\.load\s*\([^)]*\)(?!\s*,\s*Loader)', content):
        issues.append({"severity": "major", "title": "不安全的反序列化",
                       "suggestion": "pickle/yaml.load 可执行任意代码，改用安全 Loader 或 JSON"})
    return issues

def _strip_self_check_code(content: str) -> str:
    """剔除安全检查函数自身 + 检测规则常量 + 自检说明注释，修复"扫描器扫到自己"的自指误报。

    覆盖三类自指来源：
     1. 安全检查函数本体（review.py 的 _static_check_security/_static_check_network 等）；
     2. 检测规则常量（模块级赋值，值含污点汇表/正则关键字）；
     3. 自检机制的自述说明注释（说明文字常列举危险函数名，也会触发误报）。
    说明注释在源码里已改写为不出现危险字样的措辞；此处再兜底剔除含危险字样的注释行。
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    func_targets = {"_static_check_security", "_static_check_network", "_strip_self_check_code"}
    # 规则常量名（正则/污点表/哨兵，值含 SQL 关键字或危险 sink 的模块级赋值）
    rule_names = {"_SQL_KW", "_SQL_RE", "_SINK_RE", "_SQL", "TAINT_SINKS", "SINKS",
                  "TAINT_SOURCES", "_SELF_RULE_NAMES", "_SELF_RULE_PAT", "SQL_KW",
                  "SINKS_RE", "KNOWN_VULN_PACKAGES"}
    # 危险字样（SQL 关键字 / 反序列化入口 / 命令执行入口），用于识别规则常量与自检注释
    danger = re.compile(r"(SELECT|INSERT|UPDATE|DELETE|pickle\.loads|yaml\.load"
                        r"|eval|exec|os\.system|cursor\.execute)")
    lines = content.split("\n")
    targets = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in func_targets:
            targets.append((node.lineno - 1, node.end_lineno))
        # 模块级赋值：目标是规则常量 且 值含危险字样 → 整块剔除
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & rule_names:
                text = "\n".join(content.split("\n")[node.lineno - 1:node.end_lineno])
                if danger.search(text):
                    targets.append((node.lineno - 1, node.end_lineno))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id in rule_names:
                text = "\n".join(content.split("\n")[node.lineno - 1:node.end_lineno])
                if danger.search(text):
                    targets.append((node.lineno - 1, node.end_lineno))
    # 兜底：剔除含危险字样的自检说明注释行（行首 #；不影响真实业务代码）
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") and danger.search(line):
            targets.append((i, i + 1))
    for start, end in targets:
        lines[start:end] = [""] * (end - start)
    return "\n".join(lines)

SEVERITY_WEIGHTS = {"critical": 20, "major": 10, "minor": 3, "info": 1}

# ── Obsidian 代码原子库路径（复用优先·极简落地的方法论①本地原子）──
ATOMS_DIR = os.environ.get("ATOMS_DIR", "atoms")

def _list_code_atoms() -> list:
    """列出 Obsidian 代码原子库的全部原子（.md，极简可复用片段）。"""
    if not os.path.isdir(ATOMS_DIR):
        return []
    atoms = []
    for root, _dirs, files in os.walk(ATOMS_DIR):
        for f in files:
            if f.endswith(".md") and not f.startswith("_"):
                atoms.append(os.path.join(root, f))
    return atoms

def _reuse_suggestion(content: str, top_k: int = 3) -> list:
    """应用接口：检索 Obsidian 代码原子库，给待审代码的复用建议。

    从待审代码提取关键词（import 模块名 + 函数名），扫描原子库的标题/源码，
    命中的原子说明"该功能已有极简实现，可复用"，提示审查者对照。

    返回：[{"atom": 文件名, "domain": 领域, "title": 标题, "matched": 命中词}]
    """
    atoms = _list_code_atoms()
    if not atoms:
        return []
    # 提取待审代码的关键词（import 的模块 + 定义的函数名）
    keywords = set()
    try:
        tree = ast.parse(content)
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    keywords.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                keywords.add(n.module.split(".")[0] if n.module else "")
            elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                keywords.add(n.name)
    except SyntaxError:
        pass
    keywords = {k for k in keywords if k and len(k) >= 2}
    suggestions = []
    for atom in atoms:
        try:
            txt = open(atom, encoding="utf-8").read()
        except Exception:
            continue
        hits = [k for k in keywords if k in txt or k.lower() in txt.lower()]
        if hits:
            name = os.path.splitext(os.path.basename(atom))[0]
            domain = os.path.basename(os.path.dirname(atom))
            # 取标题
            title = ""
            m = re.search(r"^title:\s*[\"']?([^\"']+)", txt, re.M)
            if m:
                title = m.group(1).strip()
            suggestions.append({"atom": name, "domain": domain, "title": title or name, "matched": hits[:4]})
    suggestions.sort(key=lambda s: -len(s["matched"]))
    if suggestions:
        return suggestions[:top_k]
    # 本地 Obsidian 原子未命中 → ③远端降级：检索 GitHub 开源代码库（全程静默不报错）
    remote = _remote_reuse_suggestion(content, top_k)
    if remote:
        return [dict(r, source="github") for r in remote]
    return []


def _remote_reuse_suggestion(content: str, top_k: int = 3) -> list:
    """应用接口③远端降级：本地 Obsidian 原子未命中时，检索 GitHub 开源代码库。

    用 GitHub 代码搜索 API（带 gh token，repo scope 即可）。全程 try/except 静默，
    网络失败/无 token/限流 都返回 []，绝不抛异常中断审查。

    返回：[{"repo", "path", "url", "name"}]  GitHub 代码命中项
    """
    # 提取关键词（import 模块 + 函数名）
    keywords = set()
    try:
        tree = ast.parse(content)
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    keywords.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                keywords.add((n.module or "").split(".")[0])
            elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                keywords.add(n.name)
    except SyntaxError:
        return []
    keywords = {k for k in keywords if k and len(k) >= 3 and k not in ("def", "the", "and", "for", "with", "from", "import")}
    if not keywords:
        return []
    # 读 gh token（repo scope 即可）
    token = ""
    try:
        hosts = os.path.expanduser("~") + "/AppData/Roaming/GitHub CLI/hosts.yml"
        for line in open(hosts, encoding="utf-8"):
            if "oauth_token:" in line:
                token = line.split(":", 1)[1].strip().strip('"')
                break
    except Exception:
        return []
    if not token:
        return []
    query = " ".join(list(keywords)[:4]) + " language:python"
    try:
        import urllib.parse
        url = "https://api.github.com/search/code?q=" + urllib.parse.quote(query) + "&per_page=" + str(top_k)
        req = urllib.request.Request(url, headers={"Authorization": f"token {token}",
                                                   "Accept": "application/vnd.github+json",
                                                   "User-Agent": "codeagent-minimal"})
        resp = urllib.request.urlopen(req, timeout=12)
        data = json.loads(resp.read().decode("utf-8"))
        items = data.get("items", [])
        return [{"repo": i.get("repository", {}).get("full_name"),
                 "path": i.get("path"), "name": i.get("name"),
                 "url": i.get("html_url")} for i in items[:top_k]]
    except Exception:
        return []


def _static_check_reuse(tree, content: str) -> list:
    """复用优先·极简落地审查维度。

    检查代码是否违反方法论：能复用却重写、该极简却过度抽象、重复实现。
    （复用优先 → 提醒已有代码/原子库可复用；极简落地 → 提醒去掉冗余抽象。）
    """
    issues = []
    try:
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        # 1. 函数体过短却独立成函数（不必要的抽象）
        for fn in funcs:
            body_len = sum(1 for s in ast.walk(fn) if isinstance(s, ast.stmt))
            if body_len <= 2 and fn.name.startswith("_"):
                issues.append({"severity": "minor", "title": f"冗余抽象: 函数 {fn.name} 体过短({body_len}句), 可内联", "line": fn.lineno})
        # 2. 纯转发函数（仅调用另一函数，无附加值）→ 极简落地应内联/复用
        for fn in funcs:
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            if len(calls) == 1 and len(fn.body) == 1 and isinstance(fn.body[0], (ast.Return, ast.Expr)):
                # 参数都是简单标识符（ast.arg 类型），且不含默认值/复杂结构
                simple_args = all(not a.arg.startswith("_") for a in fn.args.args) and not fn.args.vararg and not fn.args.kwarg
                if simple_args:
                    issues.append({"severity": "minor", "title": f"转发函数 {fn.name}: 仅调用一次, 考虑直接复用调用点", "line": fn.lineno})
        # 3. 重复字符串常量（同一字面量出现≥3次 → 应提取常量复用）
        from collections import Counter
        str_lits = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) >= 4]
        for s, cnt in Counter(str_lits).most_common(3):
            if cnt >= 3:
                issues.append({"severity": "minor", "title": f"重复字符串 '{s}' 出现{cnt}次, 建议提取常量复用", "line": 1})
        # 4. 不必要的类包装（类仅一个方法且是 __init__ → 过度工程）
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if len(methods) == 1 and methods[0].name == "__init__":
                issues.append({"severity": "minor", "title": f"过度工程: 类 {cls.name} 仅含 __init__, 可用 dict/简单结构替代", "line": cls.lineno})
    except Exception:
        pass
    return issues


# ═══════════════════════════════════════════════════
# P0-1 逐行语义审查（定位高影响 bug/边界/断链，非仅静态规则）
# P0-2 严重度分级去噪（P0安全断链 / P1功能 / P2风格，薄转发归P2）
# P2-8 缺陷根因库（已知坑：布尔字段/中文tokenize/引号转义）
# P2-7 审查质量自评（漏bug/误报率，迭代优化）
# ═══════════════════════════════════════════════════

# 严重度 → 业务分级：P0=安全/断链/高影响，P1=功能，P2=风格/可维护
SEVERITY_TIER = {"critical": "P0", "major": "P1", "minor": "P2", "info": "P2"}
TIER_LABEL = {"P0": "安全/断链/高影响", "P1": "功能", "P2": "风格/可维护"}


def _classify_tier(issue: dict) -> dict:
    """给 issue 标注业务分级 P0/P1/P2 与分级语义（去噪：薄转发类/纯风格归 P2）。"""
    i = dict(issue)
    sev = i.get("severity", "minor")
    tier = SEVERITY_TIER.get(sev, "P2")
    # 去噪：标题显式声明"转发/冗余/过度/仅含__init__/行过长/占位/命名/未使用import"等纯风格 → P2
    title = i.get("title", "")
    style_hints = ("转发函数", "冗余抽象", "过度工程", "仅含 __init__", "行过长",
                   "占位", "未使用的 import", "函数名建议", "类名建议", "重复字符串",
                   "建议小写", "建议大写", "响应式", "结构松散", "内容单薄", "== None 应写",
                   "文件过大", "函数过长", "复杂度", "依赖过多", "分支复杂", "整体复杂")
    if tier != "P0" and any(h in title for h in style_hints):
        tier = "P2"
    i["tier"] = tier
    i["tier_label"] = TIER_LABEL[tier]
    # 架构取舍标注：规模/复杂度/import 依赖类为"架构取舍"，非功能缺陷，降噪提示
    if any(k in title for k in ("过大", "过长", "复杂度", "依赖过多")):
        i["arch_tradeoff"] = True
        i["denoise_note"] = "架构取舍项：结构性/可维护性，非功能或安全缺陷，可人工权衡是否拆解"
    return i


def _static_check_semantic(tree) -> list:
    """逐行语义审查：定位高影响 bug/边界/断链（非仅静态规则）。

    基于 AST 的语义分析（非正则），识别：
      - 除零风险（分母为变量/可能为0）→ 功能 P1
      - 未判空的属性/下标访问（None 解引用风险）→ 功能 P1
      - while True 无 break（潜在死循环）→ 功能 P1
      - 迭代中修改正在遍历的容器 → 功能 P1
      - return 后不可达代码 → 功能 P1
      - 忽略错误信号返回值 → 边界 P1
      - 可能的边界越界（range(len) 访问 [i+1]）→ 边界 P1
    高置信度项才报（避免误报噪音）。
    """
    issues = []
    try:
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for fn in funcs:
            fn_issues = _semantic_fn(fn)
            for iss in fn_issues:
                iss.setdefault("line", fn.lineno)
                iss["semantic"] = True
                issues.append(iss)
    except Exception:
        pass
    return issues


def _semantic_fn(fn) -> list:
    """对单个函数做逐行语义分析。返回 list[issue]。"""
    issues = []
    body = list(ast.walk(fn))
    # 1. 除零风险：除法/取模分母为变量或表达式（可能为0，且无显式 guard）
    for n in body:
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            d = n.right
            if isinstance(d, ast.Name):
                issues.append({"severity": "major",
                               "title": f"除零风险: 分母 {d.id} 未校验可能为 0",
                               "line": n.lineno,
                               "suggestion": f"除前校验 {d.id} != 0，否则运行时 ZeroDivisionError"})
            elif isinstance(d, (ast.BinOp, ast.Call)) :
                issues.append({"severity": "major",
                               "title": "除零风险: 分母为表达式未校验非 0",
                               "line": n.lineno,
                               "suggestion": "除前校验分母非 0 或捕获 ZeroDivisionError"})
    # 2. while True 无 break
    for n in body:
        if isinstance(n, ast.While):
            if isinstance(n.test, ast.Constant) and n.test.value is True:
                has_break = any(isinstance(x, ast.Break) for x in ast.walk(n))
                if not has_break:
                    issues.append({"severity": "major",
                                   "title": "while True 无 break（潜在死循环）",
                                   "line": n.lineno,
                                   "suggestion": "循环内需有 break/return 退出条件，或改为有界循环"})
    # 3. return 后不可达代码
    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.If, ast.For, ast.While, ast.Try)):
            for i, stmt in enumerate(getattr(n, "body", [])):
                if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                    if i + 1 < len(n.body):
                        nxt = n.body[i + 1]
                        issues.append({"severity": "major",
                                       "title": f"不可达代码: 行{nxt.lineno} 在 {type(stmt).__name__} 之后不会执行",
                                       "line": nxt.lineno,
                                       "suggestion": "删除死代码或调整控制流"})
                    break
    # 4. 迭代中修改正在遍历的容器（list.remove/del/pop 在 for-in 内）→ 高影响断链
    for n in body:
        if isinstance(n, ast.For):
            target = n.target
            if isinstance(target, ast.Name):
                loop_var = target.id
                for inner in ast.walk(n):
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) \
                            and inner.func.attr in ("remove", "pop", "append", "extend", "insert", "__delitem__"):
                        src = inner.func.value
                        if isinstance(src, ast.Name) and src.id == loop_var:
                            issues.append({"severity": "major",
                                           "title": f"迭代中修改遍历容器 {loop_var}（可能跳过/索引错位）",
                                           "line": inner.lineno,
                                           "suggestion": "先收集待改项，循环结束后统一修改，或复制列表遍历"})
    # 5. 忽略错误信号返回值（os.system/subprocess/requests 返回码被丢弃）
    for n in body:
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
            fn_call = n.value
            fname = _call_name(fn_call)
            if fname in ("os.system", "subprocess.run", "subprocess.call", "subprocess.check_call") \
                    and not isinstance(n, ast.Assign):
                issues.append({"severity": "minor",
                               "title": f"忽略 {fname} 返回值（未检查执行成功/失败）",
                               "line": n.lineno,
                               "suggestion": "检查返回码，失败时抛异常或记录"})
    return issues


def _call_name(call) -> str:
    """把 Call 节点转为可读名（如 os.system / requests.get）。"""
    f = call.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


# ═══════════ P2-8 缺陷根因库：已知坑模式库 ═══════════
# 根因库已抽离为独立文件 known_defects.py（可读/可扩展/与审查逻辑解耦）。
# 此处不再内嵌 KNOWN_DEFECTS，改为 import 加载；审查命中模式时 match_defects
# 会给出"[已知坑·<id>]"提示（根因沉淀，防重复踩坑）。新增缺陷条目只需改
# known_defects.py，无需动 review.py 代码。
import known_defects as _defect_lib


def _check_known_defects(content: str, line_index) -> list:
    """按已知坑模式库匹配源码，命中即提示（根因沉淀防重复踩坑）。

    委托 known_defects.match_defects 完成全库匹配，返回结构与该库一致
    [{severity, title, line, suggestion, root_cause, defect_id, semantic}]。
    """
    return _defect_lib.match_defects(content, line_index)


# ═══════════ P2-7 审查质量自评（漏bug/误报率）═══════════
import time as _time

SELF_EVAL_LOG = os.environ.get("CODEAGENT_SELF_EVAL", ".codeagent/review_self_eval.json")


def self_eval_record(file, findings, reported=None, missed=None, extra_fp=None):
    """记录一次审查的自评结果：是否漏 bug / 误报率，用于迭代优化 review 原子。

    reported: 本次实际报出的 issue 数；missed: 事后发现漏掉的真实 bug 数；
    extra_fp: 事后判定为误报的 issue 数（reviewer 人工复核后回填）。
    返回 {ok, data:{累计审查次数, 累计误报率, 累计漏报}}。
    """
    import json as _json
    path = SELF_EVAL_LOG if os.path.isabs(SELF_EVAL_LOG) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), SELF_EVAL_LOG)
    rec = []
    try:
        if os.path.exists(path):
            rec = _json.load(open(path, encoding="utf-8"))
    except Exception:
        rec = []
    reported = len(findings) if reported is None else reported
    rec.append({"ts": _time.strftime("%Y-%m-%d %H:%M:%S"), "file": file,
                "reported": reported, "missed": missed or 0, "fp": extra_fp or 0})
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(rec, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    total_fp = sum(r["fp"] for r in rec)
    total_reported = sum(r["reported"] for r in rec) or 1
    total_missed = sum(r["missed"] for r in rec)
    return {"ok": True, "recorded": len(rec), "log_path": path,
            "total_reported": sum(r["reported"] for r in rec),
            "total_missed": total_missed,
            "false_positive_rate": round(total_fp / total_reported * 100, 1),
            "missed_rate": round(total_missed / max(1, total_missed + total_reported) * 100, 1),
            "hint": "误报率高→放宽规则；漏报高→加强/新增语义检查",
            "history": rec[-5:]}


def self_eval_stats():
    """读取自评日志，返回累计统计（供迭代优化 review 原子）。"""
    import json as _json
    path = SELF_EVAL_LOG if os.path.isabs(SELF_EVAL_LOG) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), SELF_EVAL_LOG)
    if not os.path.exists(path):
        return {"ok": True, "records": 0, "false_positive_rate": 0.0, "missed_rate": 0.0, "history": []}
    try:
        rec = _json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"ok": True, "records": 0, "false_positive_rate": 0.0, "missed_rate": 0.0, "history": []}
    total_fp = sum(r["fp"] for r in rec)
    total_reported = sum(r["reported"] for r in rec) or 1
    total_missed = sum(r["missed"] for r in rec)
    return {"ok": True, "records": len(rec),
            "total_reported": sum(r["reported"] for r in rec),
            "total_missed": total_missed,
            "false_positive_rate": round(total_fp / total_reported * 100, 1),
            "missed_rate": round(total_missed / max(1, total_missed + total_reported) * 100, 1),
            "history": rec[-5:]}





def _static_analyze(content: str, max_complexity: int = 10, strict_undefined: bool = False,
                    semantic: bool = True, denoise: bool = True) -> dict:
    """对单文件执行全量静态分析, 返回结构化结果 + 得分

    semantic=True: 启用逐行语义审查（定位高影响 bug/边界/断链）。
    denoise=True: 严重度分级去噪（每 issue 标 tier P0/P1/P2，薄转发归 P2，架构取舍标注）。
    """
    result = {"syntax": [], "imports": [], "complexity": [], "naming": [], "security": [], "network": [], "bugs": [], "architecture": [], "reuse": [], "semantic": [], "score": 100}
    all_issues = []
    result["syntax"] = _static_check_syntax(content)
    all_issues.extend(result["syntax"])
    result["security"] = _static_check_security(_strip_self_check_code(content))
    all_issues.extend(result["security"])
    result["network"] = _static_check_network(_strip_self_check_code(content))
    all_issues.extend(result["network"])
    if not result["syntax"]:
        try:
            tree = ast.parse(content)
            result["imports"] = _static_check_imports(tree, content)
            result["complexity"] = _static_check_complexity(tree, max_complexity)
            result["naming"] = _static_check_naming(tree)
            result["bugs"] = _static_check_bugs(tree, content, strict_undefined)
            result["architecture"] = _static_check_architecture(content)
            result["reuse"] = _static_check_reuse(tree, content)
            if semantic:
                # P0-1 逐行语义审查（除零/死循环/不可达/迭代改容器/忽略返回值）
                result["semantic"] = _static_check_semantic(tree)
                # P2-8 缺陷根因库提示（已知坑：布尔字段/中文tokenize/引号转义）
                result["semantic"] += _check_known_defects(content, content.split("\n"))
            all_issues.extend(result["imports"] + result["complexity"] + result["naming"]
                              + result["bugs"] + result["architecture"] + result["reuse"]
                              + result["semantic"])
        except SyntaxError:
            pass
    if denoise and all_issues:
        # P0-2 严重度分级去噪：逐条标 tier / 架构取舍 / 薄转发归 P2
        all_issues = [_classify_tier(i) for i in all_issues]
    penalty = sum(SEVERITY_WEIGHTS.get(i["severity"], 5) for i in all_issues)
    result["score"] = max(0, 100 - penalty)
    result["all_issues"] = all_issues
    return result

# ═══════════════════════════════════════════════════
# 可选 LLM 增强审查 (OpenAI 兼容, 用标准库 urllib)
# ═══════════════════════════════════════════════════

def _llm_enabled() -> bool:
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("LLM_REVIEW") == "1")

def _call_llm(messages: list, temp: float = 0.3) -> str:
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1/chat/completions")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    key = os.environ.get("LLM_API_KEY", "")
    payload = json.dumps({"model": model, "messages": messages,
                          "temperature": temp, "max_tokens": 2000}).encode()
    req = urllib.request.Request(base, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"]

def _llm_review(code_text: str) -> dict:
    """LLM 审查: 过度工程 + 质量 + 可证伪改进建议"""
    prompt = (
        "你是代码审查专家，关注极简性和过度工程。审查以下代码，输出纯JSON：\n"
        "{\"passed\":bool,\"score\":0-100,\"summary\":\"一句话总结\",\n"
        "\"issues\":[{\"file\":\"\",\"severity\":\"critical|major|minor\",\"title\":\"\",\"suggestion\":\"\"}],\n"
        "\"overengineering\":[\"发现过度工程问题\"],\n"
        "\"predictions\":[\"修复<问题X>后，预期<哪个测试/可观察行为>会通过\"]}\n"
        "特别检查：有没有未要求的功能/抽象/配置系统？有没有只用一次的抽象层？"
        "有没有能用标准库替代的第三方依赖？\n"
        "可证伪要求：每条 critical/major issue 必须给一条 predictions 项，"
        "明确'修复后会改变哪个可观察行为'，禁止空泛建议。\n\n" + code_text
    )
    try:
        raw = _call_llm([
            {"role": "system", "content": "你是代码审查专家。关注极简性和过度工程。输出纯JSON。"},
            {"role": "user", "content": prompt},
        ])
        # 去代码块围栏后解析
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        return json.loads(cleaned)
    except Exception as e:
        return {"error": str(e), "score": 0, "issues": [], "overengineering": [], "predictions": []}

# ═══════════════════════════════════════════════════
# 扫描 + 汇总 + CLI
# ═══════════════════════════════════════════════════

def _collect_py_files(target: str) -> list:
    p = Path(target)
    if p.is_file():
        return [p] if p.suffix == ".py" else []
    return sorted([f for f in p.rglob("*.py") if ".venv" not in str(f) and "node_modules" not in str(f)])

def _external_bandit(content: str) -> list:
    """可选对接 bandit（装了才用）：深度安全扫描，无则返回 []。"""
    import shutil
    if shutil.which("bandit") is None:
        try:
            import bandit  # noqa
        except ImportError:
            return []
    import tempfile
    import subprocess as sp
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = f.name
    issues = []
    try:
        r = sp.run(["bandit", "-f", "json", "-q", tmp], capture_output=True, text=True, timeout=30)
        import json as _j
        data = _j.loads(r.stdout) if r.stdout.strip() else {}
        for res in data.get("results", []):
            sev = {"HIGH": "critical", "MEDIUM": "major", "LOW": "minor"}.get(res.get("issue_severity", "LOW"), "minor")
            issues.append({"severity": sev, "title": f"[bandit] {res.get('test_id','')} {res.get('issue_text','')[:60]}",
                           "line": res.get("line_number", 0), "suggestion": "见 bandit 文档修复", "source": "bandit"})
    except Exception:
        pass
    finally:
        try: os.remove(tmp)
        except OSError: pass
    return issues


def _external_lint(content: str) -> list:
    """可选对接 ruff/pyflakes（装了才用）：深度静态，无则返回 []。"""
    import shutil, tempfile
    import subprocess as sp
    tool = None
    if shutil.which("ruff") or _module_exists("ruff"):
        tool = ["ruff", "check", "--quiet", "--output-format", "concise"]
    elif shutil.which("pyflakes"):
        tool = ["pyflakes"]
    else:
        try:
            import pyflakes  # noqa
            tool = ["pyflakes"]
        except ImportError:
            return []
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = f.name
    issues = []
    try:
        r = sp.run(tool + [tmp], capture_output=True, text=True, timeout=30)
        for line in (r.stdout or "").splitlines() + (r.stderr or "").splitlines():
            # ruff: path:line:col: E501 msg ; pyflakes: path:line: msg
            m = __import__("re").match(r".*?:(\d+):\d*:\s*(.*)", line) or __import__("re").match(r".*?:(\d+):\s*(.*)", line)
            if m:
                issues.append({"severity": "minor", "title": f"[{tool[0]}] {m.group(2)[:70]}",
                               "line": int(m.group(1)), "suggestion": "按提示修复", "source": tool[0]})
    except Exception:
        pass
    finally:
        try: os.remove(tmp)
        except OSError: pass
    return issues


def _module_exists(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def review_file(path: str, use_llm: bool, max_complexity: int = 10, strict_undefined: bool = False, external: bool = False, reuse_atoms: bool = False, semantic: bool = True, denoise: bool = True) -> dict:
    content = Path(path).read_text(encoding="utf-8", errors="ignore")
    static = _static_analyze(content, max_complexity, strict_undefined, semantic=semantic, denoise=denoise)
    if external:
        ext = _external_bandit(content) + _external_lint(content)
        if ext:
            static["all_issues"].extend(ext)
            if denoise:
                static["all_issues"] = [_classify_tier(i) for i in static["all_issues"]]
            penalty = sum(SEVERITY_WEIGHTS.get(i["severity"], 5) for i in static["all_issues"])
            static["score"] = max(0, 100 - penalty)
    # P0-2 严重度去噪汇总：按 P0/P1/P2 分级计数
    by_tier = {"P0": 0, "P1": 0, "P2": 0}
    for i in static["all_issues"]:
        by_tier[i.get("tier", SEVERITY_TIER.get(i.get("severity", "minor"), "P2"))] = \
            by_tier.get(i.get("tier", SEVERITY_TIER.get(i.get("severity", "minor"), "P2")), 0) + 1
    file_result = {
        "file": path,
        "static_score": static["score"],
        "static_issues": static["all_issues"],
        "issues": [dict(i, file=path) for i in static["all_issues"]],
        "severity_summary": by_tier,
    }
    # 应用接口：检索 Obsidian 代码原子库给复用建议（复用优先·极简落地）
    if reuse_atoms:
        file_result["reuse_suggestions"] = _reuse_suggestion(content)
    if use_llm and _llm_enabled():
        llm = _llm_review(f"### {path}\n```python\n{content}\n```")
        if "error" not in llm:
            file_result["model"] = {
                "score": llm.get("score", 0),
                "overengineering": llm.get("overengineering", []),
                "predictions": llm.get("predictions", []),
                "summary": llm.get("summary", ""),
            }
            file_result["issues"].extend(dict(i, file=path) for i in llm.get("issues", []))
    return file_result

def _dep_enrich(file_result, content, graph):
    """依赖图感知增强：把 issue 关联到『谁调用它/改它波及谁』，审查更准更深。
    对审查命中的每个实体(函数/类)，标出直接调用方(影响面)。"""
    try:
        import dep_audit as da
        mod = Path(file_result["file"]).stem
        defined = [e for e in graph["entities"] if e.split(".")[0] == mod]
        if not defined:
            return
        for i in file_result["issues"]:
            title = i.get("title", "")
            hit = next((e for e in defined if e.split(".")[-1] in title), None)
            if not hit:
                continue
            callers = da.callers(graph, hit)
            if callers:
                i["callers"] = callers
                i["suggestion"] = (i.get("suggestion", "") or "").strip()
                i["impact"] = f"改 {hit} 波及 {len(callers)} 个调用方: {', '.join(callers[:8])}"
    except Exception:
        pass


def _run_dep(target, impact, transitive):
    """运行本体感知依赖图审查, 返回 (json_dict, human_text)。"""
    import dep_audit as da
    report = da.dep_report([target], impact=impact, transitive=transitive)
    return report, da._fmt_report(report)


def _run_self_evolve(memdir="experience"):
    """打通 self_evolve: 把经验记忆目录设为仓库内 experience/。"""
    import self_evolve
    return self_evolve


# ═══════════════════════════════════════════════════
# 5方向：轻审 review --light（增量扫描 git diff，快速静态+安全基线）
#       + 重审 review --deep（数据流污点追踪 + 双引擎静态+对抗性验证）
# ═══════════════════════════════════════════════════

def git_diff_py_files(target: str, base: str = "HEAD") -> list:
    """git diff 只扫变更：返回变更的 .py 文件绝对路径（新增 A/修改 M/复制 C/重命名 R）。

    base 默认 HEAD（未提交变更）；传空串可对暂存区 --cached。非 git 仓库或失败 → []。
    """
    import subprocess as sp
    root = str(Path(target))
    changed = []
    try:
        r = sp.run(["git", "diff", "--name-only", "--diff-filter=ACMR", base],
                   cwd=root, capture_output=True, text=True, timeout=20)
        changed = [l.strip() for l in (r.stdout or "").splitlines() if l.strip().endswith(".py")]
        # 未跟踪的新文件也纳入（git diff 不含 others）
        r2 = sp.run(["git", "ls-files", "--others", "--exclude-standard"],
                    cwd=root, capture_output=True, text=True, timeout=20)
        changed += [l.strip() for l in (r2.stdout or "").splitlines() if l.strip().endswith(".py")]
        changed = sorted({str(Path(root) / c) for c in changed if c})
    except Exception:
        return []
    return changed


def light_review(target: str, base: str = "HEAD", max_complexity: int = 10) -> dict:
    """轻审：增量扫描 git diff 只扫变更文件，跑快速静态 + 安全基线。

    快速：跳过耗时的语义审查/复用建议/LLM（semantic=False），只跑语法/import/复杂度/
    命名/安全/网络，并把 critical/major 汇总为"安全基线"。适合 CI 增量门禁。
    """
    files = git_diff_py_files(target, base)
    results = []
    for p in files:
        try:
            content = Path(p).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        static = _static_analyze(content, max_complexity, semantic=False, denoise=True)
        baseline = [i for i in static["all_issues"]
                    if i.get("severity") in ("critical", "major")]
        results.append({"file": p, "score": static["score"],
                        "issues": static["all_issues"],
                        "security_baseline": baseline, "security_count": len(baseline)})
    by_tier = {"P0": 0, "P1": 0, "P2": 0}
    for r in results:
        for i in r["issues"]:
            by_tier[i.get("tier", SEVERITY_TIER.get(i.get("severity", "minor"), "P2"))] += 1
    sev = sum(r["security_count"] for r in results)
    return {"files": results, "changed_files": len(files),
            "security_findings": sev, "severity_summary": by_tier,
            "summary": f"轻审增量 {len(files)} 个变更文件, 安全/高影响 {sev} 项",
            "mode": "light", "incremental": True, "git_diff_base": base,
            "human_review": {"needs_review": True, "reason": "轻审为增量快速静态初筛，P0/P1 安全基线需人工复核真实性与影响面后再放行",
                             "progressive": True, "auto_pass": False}}


# ── 重审深度：数据流污点追踪（source→sink 变量传播）──
# 污点源：外部/不可信输入入口（赋值给变量的调用）
TAINT_SOURCES = {
    "input": "标准输入", "raw_input": "标准输入", "getpass": "密码输入",
    "os.environ.get": "环境变量", "os.getenv": "环境变量", "sys.argv": "命令行参数",
    "request.args.get": "HTTP参数", "request.form.get": "HTTP表单",
    "request.json.get": "HTTP JSON", "request.cookies.get": "HTTP Cookie",
    "request.headers.get": "HTTP头", "flask.request": "HTTP请求",
    "get_param": "查询参数", "json.loads": "外部JSON", "read": "文件/流读取",
    "open": "文件读取", "urlopen": "远端响应", "requests.get": "远端响应",
}
# 污点汇：危险调用（需不可信输入到达才能触发的安全 sink）
TAINT_SINKS = {
    "eval": "代码执行", "exec": "代码执行", "compile": "代码编译",
    "os.system": "命令执行", "os.popen": "命令执行", "os.spawn": "命令执行",
    "subprocess.Popen": "命令执行", "subprocess.run": "命令执行",
    "subprocess.call": "命令执行", "subprocess.check_call": "命令执行",
    "cursor.execute": "SQL执行", "execute": "SQL执行",
    "pickle.loads": "反序列化", "pickle.load": "反序列化", "yaml.load": "反序列化",
    "open": "文件读写", "os.remove": "文件删除", "os.rename": "文件操作",
    "requests.get": "SSRF", "urlopen": "SSRF",
    "render_template_string": "模板注入",
}


def _dataflow_analyze(tree, content):
    """数据流分析：变量传播/污点追踪。返回 (confirmed_flows, sink_sites)。

    confirmed_flows: source(输入)→sink(危险调用) 的已确认污点路径（高置信 P0）。
    sink_sites: {行号: [sink名,...]} 所有危险 sink 调用点（供对抗性验证对照"是否有污点流入"）。
    思路：单函数内逐语句，把来自 TAINT_SOURCES 的赋值变量标记为 tainted，赋值传播，
    命中 TAINT_SINKS 且实参含 tainted 变量或直接 source 调用 → 确认路径。
    """
    confirmed, sink_sites = [], {}
    try:
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for fn in funcs:
            tainted = set()
            # 参数：来自 request/input/argv 的参数名可能由外部调用方污染（保守标记常见名）
            for a in fn.args.args:
                if a.arg in ("request", "req", "data", "payload", "body", "params",
                             "query", "user_input", "cmd", "url", "name", "path",
                             "content", "text", "value", "filename"):
                    tainted.add(a.arg)
            for node in ast.walk(fn):
                # 污点源赋值：var = source_call(...)
                if isinstance(node, ast.Assign):
                    targets = [t for t in node.targets if isinstance(t, ast.Name)]
                    val = node.value
                    if isinstance(val, ast.Call) and _call_name(val) in TAINT_SOURCES:
                        for t in targets:
                            tainted.add(t.id)
                    # 变量传播：var2 = tainted_var
                    elif isinstance(val, ast.Name) and val.id in tainted:
                        for t in targets:
                            tainted.add(t.id)
                # sink 调用检查
                if isinstance(node, ast.Call):
                    cname = _call_name(node)
                    if cname in TAINT_SINKS:
                        line = getattr(node, "lineno", 0)
                        sink_sites.setdefault(line, []).append(cname)
                        tainted_args = [a.id for a in node.args
                                        if isinstance(a, ast.Name) and a.id in tainted]
                        direct_src = [a for a in node.args
                                      if isinstance(a, ast.Call) and _call_name(a) in TAINT_SOURCES]
                        if tainted_args or direct_src:
                            src_desc = ", ".join(
                                [f"{a}({TAINT_SOURCES.get(a, '输入')})" for a in direct_src]
                                + [f"{a}←污染" for a in tainted_args])
                            confirmed.append({
                                "severity": "critical", "title": f"污点路径: 输入→{cname}（{TAINT_SINKS[cname]}）",
                                "line": line, "sink": cname,
                                "tainted_args": tainted_args,
                                "flow": f"source→sink 已确认: {src_desc} 流入 {cname}",
                                "engine": "dataflow", "tier": "P0"})
    except Exception:
        pass
    return confirmed, sink_sites


def _adversarial_verify(issues, flow_findings, sink_sites):
    """对抗性引擎：对每条静态安全 issue 先假设误报，用数据流证伪/证实。

    confirmed   — 数据流确认 source→sink（真实，高置信）
    needs_review — 命中危险 sink 但未见污点流入（先假设误报，需人工复核）
    likely_fp    — 未找到对应 sink/流佐证（倾向误报，降级为 P2 提示）
    静态 issue 多无行号 → 除按行匹配外，再用标题中的 sink 关键字做语义兜底匹配。
    """
    # 标题关键字 → 相关 sink 名（用于无行号静态 issue 的语义匹配）
    def _title_sinks(title):
        t = (title or "")
        hits = []
        for cname in TAINT_SINKS:
            if cname in t:
                hits.append(cname)
        if "命令" in t or "os.system" in t:
            hits += ["os.system", "os.popen", "subprocess.Popen", "subprocess.run"]
        if "SQL" in t or "注入" in t:
            hits += ["cursor.execute", "execute"]
        if "eval" in t.lower() or "exec" in t.lower():
            hits += ["eval", "exec"]
        if "反序列化" in t:
            hits += ["pickle.loads", "pickle.load", "yaml.load"]
        if "密钥" in t or "密码" in t:
            hits += ["secret"]  # 密钥类静态命中，未见 sink，倾向需复核
        if "SSRF" in t or "url" in t.lower():
            hits += ["requests.get", "urlopen"]
        return set(hits)

    out = []
    for i in issues:
        if i.get("severity") not in ("critical", "major"):
            out.append(dict(i, engine="static")); continue
        line = i.get("line")
        kw = _title_sinks(i.get("title"))
        matched_flow = [f for f in flow_findings
                        if (line and f["line"] == line)
                        or (kw and f.get("sink") in kw)]
        matched_sink = (line and sink_sites.get(line)) or \
                       [s for s in kw if s in set(n for sites in sink_sites.values() for n in sites)]
        if matched_flow:
            ev = [f["flow"] for f in matched_flow]
            out.append(dict(i, engine="adversarial", adversarial_verdict="confirmed",
                            adversarial_evidence="数据流佐证: " + "; ".join(ev)))
        elif matched_sink:
            out.append(dict(i, engine="adversarial", adversarial_verdict="needs_review",
                            adversarial_evidence="存在危险 sink 但未见 source→sink 污点路径，先假设误报，需人工复核"))
        else:
            out.append(dict(i, engine="adversarial", adversarial_verdict="likely_fp",
                            tier="P2", tier_label=TIER_LABEL["P2"],
                            adversarial_evidence="未找到对应危险 sink 的数据流佐证，倾向误报（已降噪为 P2）"))
    return out


def deep_review(path: str, max_complexity: int = 10, denoise: bool = True) -> dict:
    """重审：数据流分析（变量传播/污点追踪）+ 双引擎（静态规则 + AI 语义对抗性验证）。

    引擎1 静态：_static_analyze 全量（含语义/缺陷根因库）；
    引擎2 数据流+对抗：_dataflow_analyze 确认 source→sink 污点路径，_adversarial_verify
    对每条静态安全 issue 先假设误报再证实/证伪（confirmed / needs_review / likely_fp）。
    返回：static_result(引擎1) + dataflow_findings + adversarial(引擎2 验证后安全 issues)
    + 合并后的 score（critical 确认项重罚）。
    """
    content = Path(path).read_text(encoding="utf-8", errors="ignore")
    static = _static_analyze(content, max_complexity, semantic=True, denoise=denoise)
    try:
        tree = ast.parse(content)
    except SyntaxError:
        tree = None
    flows, sink_sites = ([], {}) if tree is None else _dataflow_analyze(tree, content)
    sec_net = [i for i in static["all_issues"]
               if i.get("engine") in (None, "static") and i.get("severity") in ("critical", "major")
               and ("注入" in i.get("title", "") or "风险" in i.get("title", "")
                    or "eval" in i.get("title", "").lower() or "exec" in i.get("title", "").lower()
                    or "反序列化" in i.get("title", "") or "密钥" in i.get("title", "")
                    or "命令" in i.get("title", ""))]
    adversarial = _adversarial_verify(sec_net, flows, sink_sites) if sec_net else []
    by_tier = {"P0": 0, "P1": 0, "P2": 0}
    for i in static["all_issues"]:
        by_tier[i.get("tier", SEVERITY_TIER.get(i.get("severity", "minor"), "P2"))] += 1
    for f in flows:
        by_tier["P0"] += 1
    merged_issues = static["all_issues"] + list(flows)
    penalty = sum(SEVERITY_WEIGHTS.get(i.get("severity", 5), 5) for i in merged_issues)
    score = max(0, 100 - penalty)
    return {"file": path, "score": score, "static_result": static,
            "dataflow_findings": flows, "sink_sites": sink_sites,
            "adversarial": adversarial, "issues": merged_issues,
            "severity_summary": by_tier,
            "engine_count": {"static": len(static["all_issues"]),
                             "dataflow": len(flows), "adversarial": len(adversarial)},
            "summary": (f"重审双引擎 {path}: 静态{len(static['all_issues'])} + 污点{len(flows)}"
                        f" + 对抗验证{len(adversarial)}, 分 {score}"),
            "human_review": {"needs_review": True,
                             "reason": "重审含数据流+对抗验证，confirmed/needs_review 项需人工复核真实攻击路径后再放行",
                             "progressive": True, "auto_pass": False}}


def main():
    ap = argparse.ArgumentParser(description="CodeReview Minimal — 审代码不写代码的人用的审查器 + 测试 harness")
    ap.add_argument("target", help="要审查的文件或目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--score-only", action="store_true", help="只输出每文件得分")
    ap.add_argument("--llm", action="store_true", help="启用 LLM 增强审查(需配 LLM_API_KEY)")
    ap.add_argument("--max-complexity", type=int, default=10, help="圈复杂度阈值(默认10)")
    ap.add_argument("--strict-undefined", action="store_true", help="启用未定义名检查(启发式,易误报,默认关)")
    ap.add_argument("--external", action="store_true", help="可选对接 bandit/ruff(装了才用,深度增强)")
    ap.add_argument("--reuse-atoms", action="store_true", help="复用优先·极简落地: 检索本地Obsidian代码原子→GitHub远端开源→(大模型兜底), 全程静默不报错")
    ap.add_argument("--threshold", type=int, default=0, help="平均得分低于此值则 exit 1(CI 门禁)")
    ap.add_argument("--test", action="store_true", help="运行测试 harness(冒烟/单元/边界/变异/稳定性)")
    ap.add_argument("--test-dir", default=".", help="测试文件所在目录(配合 --test)")
    ap.add_argument("--dep", action="store_true", help="本体感知依赖图审查: 实体/调用图/循环依赖/模块耦合/跨文件影响(改A波及B)")
    ap.add_argument("--impact", metavar="SYMBOL", help="影响分析: 改该符号/模块波及谁(配合 --dep)")
    ap.add_argument("--transitive", action="store_true", help="影响分析含传递闭包")
    ap.add_argument("--refine", metavar="OUTCOME_JSON", help="refine() 自省闭环: 观察→归因→精炼→校验(快照回滚)+自动沉淀技能+记忆复盘")
    ap.add_argument("--tdd", action="store_true", help="TDD反馈闭环: 测试反馈→改进→再测试(红→改→绿→回归)")
    ap.add_argument("--full", action="store_true", help="一键套餐: 静态+测试harness+external+reuse+依赖图 一次跑完")
    ap.add_argument("--light", action="store_true", help="轻审: 增量扫描 git diff 只扫变更, 快速静态+安全基线(CI增量门禁)")
    ap.add_argument("--deep", action="store_true", help="重审: 数据流污点追踪 + 双引擎(静态+对抗性验证先假设误报证伪)")
    ap.add_argument("--base", default="HEAD", help="git diff 基线(配合 --light, 默认 HEAD)")
    args = ap.parse_args()

    if not os.path.exists(args.target):
        print(f"路径不存在: {args.target}"); sys.exit(1)

    # 5方向：轻审/重审 走专属分支
    if args.light:
        print(json.dumps(light_review(args.target, base=args.base), ensure_ascii=False,
                         indent=2, default=str))
        return
    if args.deep:
        try:
            tree = ast.parse(Path(args.target).read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError, IsADirectoryError):
            print(f"重审需单文件(可解析的 .py), 目标: {args.target}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(deep_review(str(args.target)), ensure_ascii=False, indent=2, default=str))
        return

    files = _collect_py_files(args.target)
    if not files:
        print("未找到 .py 文件"); sys.exit(0)

    # --full 一键套餐: 静态 + 测试harness + external + reuse + 依赖图 一次跑完
    if args.full:
        args.test = True
        args.external = True
        args.reuse_atoms = True
        args.dep = True

    results = [review_file(str(f), args.llm, args.max_complexity, args.strict_undefined, args.external, args.reuse_atoms) for f in files]

    # ── 本体感知依赖图审查（依赖图感知增强 + 影响分析/循环依赖/耦合）──
    dep_report = None
    if args.dep or args.impact:
        try:
            import dep_audit as da
            graph = da.build_graph([args.target])
            for r in results:
                try:
                    _dep_enrich(r, Path(r["file"]).read_text(encoding="utf-8", errors="ignore"), graph)
                except Exception:
                    pass
            dep_report, dep_text = _run_dep(args.target, args.impact, args.transitive)
        except Exception as e:
            dep_report, dep_text = None, f"(依赖图审查不可用: {e})"

    # ── refine() 自省闭环 + 自我提示 + 记忆复盘（Graph Engineering 自进化执行）──
    if args.refine:
        try:
            import self_evolve as se
            memdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experience")
            outcome = json.loads(args.refine) if not os.path.exists(args.refine) \
                else json.loads(Path(args.refine).read_text(encoding="utf-8"))
            res = se.refine(outcome.get("task", args.target), outcome, memdir=memdir)
            print("🧬 refine() 自省闭环:")
            print(json.dumps(res, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"🧬 refine() 不可用: {e}")

    # ── 测试 harness（模块B）──
    test_report = None
    if args.test:
        try:
            import test_harness as th
            test_report = th.run_all(str(files[0]), args.test_dir)
        except Exception as e:
            test_report = {"error": str(e)}

    if args.score_only:
        for r in results:
            flag = "✅" if r["static_score"] >= 60 else "❌"
            print(f"{flag} {r['static_score']:3} {r['file']}")
        return

    if args.json:
        out = {"results": results}
        if dep_report:
            out["dep_audit"] = dep_report
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # 人类可读报告
    for r in results:
        print("=" * 60)
        print(f"📄 {r['file']}   静态得分: {r['static_score']}/100")
        if "model" in r:
            print(f"   🤖 LLM 审查: {r['model'].get('score')}分 | {r['model'].get('summary','')}")
        bysev = {"critical": 0, "major": 0, "minor": 0}
        for i in r["issues"]:
            bysev[i.get("severity", "minor")] = bysev.get(i.get("severity", "minor"), 0) + 1
        print(f"   问题: critical {bysev['critical']} / major {bysev['major']} / minor {bysev['minor']}")
        for i in r["issues"]:
            mark = {"critical": "🔴", "major": "🟠", "minor": "🟡"}.get(i.get("severity", "minor"), "⚪")
            loc = f"L{i['line']} " if i.get("line") else ""
            print(f"   {mark} [{i.get('severity')}] {loc}{i.get('title')}")
            if i.get("suggestion"):
                print(f"      → {i['suggestion']}")
            if i.get("impact"):
                print(f"      ↳ {i['impact']}")
        if "model" in r and r["model"].get("overengineering"):
            print("   🧹 过度工程:")
            for o in r["model"]["overengineering"]:
                print(f"      - {o}")
        if "model" in r and r["model"].get("predictions"):
            print("   🔮 可证伪改进(修复后预期):")
            for p in r["model"]["predictions"]:
                print(f"      - {p}")
        if "reuse_suggestions" in r and r["reuse_suggestions"]:
            print("   ♻️ 复用建议（复用优先·极简落地）:")
            for s in r["reuse_suggestions"]:
                if s.get("source") == "github":
                    print(f"      - [GitHub] {s.get('repo')} {s.get('path')} ({s.get('url')})")
                else:
                    print(f"      - [Obsidian原子·{s.get('domain')}] {s.get('title')} 命中: {','.join(s.get('matched',[]))}")

    # 依赖图审查报告
    if dep_report:
        print("\n" + "=" * 60)
        print(dep_text)

    # ── TDD 反馈闭环（测试反馈→改进→再测试）──
    if args.tdd:
        try:
            import self_evolve as se
            memdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experience")
            res = se.tdd_loop(str(files[0]), memdir=memdir, task=args.target)
            print("\n🧪 TDD 反馈闭环（红→改→绿→回归）:")
            print(json.dumps(res, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"🧪 TDD 闭环不可用: {e}")

    # 汇总
    avg = sum(r["static_score"] for r in results) / len(results)
    total_issues = sum(len(r["issues"]) for r in results)
    print("\n" + "=" * 60)
    print(f"共 {len(results)} 个文件 | 平均静态得分 {avg:.0f}/100 | 问题总数 {total_issues}")
    bad = [r["file"] for r in results if r["static_score"] < 60]
    if bad:
        print(f"⚠️ 需关注(<60分): {', '.join(bad)}")

    # CI 门禁：平均得分低于阈值则 exit 1
    if args.threshold > 0:
        avg = sum(r["static_score"] for r in results) / len(results)
        if avg < args.threshold:
            print(f"\n❌ CI 门禁: 平均得分 {avg:.0f} < 阈值 {args.threshold} → 失败(exit 1)")
            sys.exit(1)
        else:
            print(f"\n✅ CI 门禁: 平均得分 {avg:.0f} >= 阈值 {args.threshold} → 通过")

    # 测试 harness 报告
    if test_report:
        print("\n" + "=" * 60)
        print("🧪 测试 harness（冒烟/单元/边界/变异/稳定性）")
        if "error" in test_report:
            print(f"   ⚠️ harness 错误: {test_report['error']}")
        for k in ["smoke", "unit", "boundary", "mutation", "stability"]:
            if k in test_report:
                v = test_report[k]
                mark = "✅" if v.get("ok", False) else "⚠️" if v.get("skipped") else "❌"
                print(f"   {mark} {k}: {v.get('details', '')}")
                if k == "boundary" and v.get("findings"):
                    for fnd in v["findings"][:5]:
                        print(f"      → {fnd['suggestion']}")
                if k == "mutation" and v.get("survived"):
                    print(f"      → 有 {v['survived']} 个变异未被测试捕获，测试覆盖需加强")
        if test_report and not test_report.get("unit", {}).get("skipped") and test_report.get("unit", {}).get("test_count", 0) == 0:
            print("   ⚠️ 无测试用例：变异/单元未充分覆盖，建议补测试")

if __name__ == "__main__":
    main()
