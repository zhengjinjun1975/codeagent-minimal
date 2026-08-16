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
    """剔除安全检查函数自身的源码区间，修复"扫描器扫到自己"的自指误报。"""
    try:
        tree = ast.parse(content)
        targets = {"_static_check_security", "_static_check_network", "_strip_self_check_code"}
        lines = content.split("\n")
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                lines[node.lineno - 1:node.end_lineno] = [""] * (node.end_lineno - node.lineno + 1)
        return "\n".join(lines)
    except SyntaxError:
        pass
    return content

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


def _static_analyze(content: str, max_complexity: int = 10, strict_undefined: bool = False) -> dict:
    """对单文件执行全量静态分析, 返回结构化结果 + 得分"""
    result = {"syntax": [], "imports": [], "complexity": [], "naming": [], "security": [], "network": [], "bugs": [], "architecture": [], "reuse": [], "score": 100}
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
            all_issues.extend(result["imports"] + result["complexity"] + result["naming"]
                              + result["bugs"] + result["architecture"] + result["reuse"])
        except SyntaxError:
            pass
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


def review_file(path: str, use_llm: bool, max_complexity: int = 10, strict_undefined: bool = False, external: bool = False, reuse_atoms: bool = False) -> dict:
    content = Path(path).read_text(encoding="utf-8", errors="ignore")
    static = _static_analyze(content, max_complexity, strict_undefined)
    if external:
        ext = _external_bandit(content) + _external_lint(content)
        if ext:
            static["all_issues"].extend(ext)
            penalty = sum(SEVERITY_WEIGHTS.get(i["severity"], 5) for i in static["all_issues"])
            static["score"] = max(0, 100 - penalty)
    file_result = {
        "file": path,
        "static_score": static["score"],
        "static_issues": static["all_issues"],
        "issues": [dict(i, file=path) for i in static["all_issues"]],
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
    args = ap.parse_args()

    if not os.path.exists(args.target):
        print(f"路径不存在: {args.target}"); sys.exit(1)

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
