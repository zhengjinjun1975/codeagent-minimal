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

def _static_check_complexity(tree) -> list:
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            c = 1
            for n in ast.walk(node):
                if isinstance(n, (ast.If, ast.While, ast.For, ast.ExceptHandler,
                                  ast.And, ast.Or, ast.Assert, ast.Try)):
                    c += 1
            if c > 10:
                issues.append({"severity": "major", "title": f"圈复杂度 {c} > 10: {node.name}",
                               "line": node.lineno, "suggestion": "考虑拆分为多个小函数"})
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
        targets = {"_static_check_security", "_strip_self_check_code"}
        lines = content.split("\n")
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                lines[node.lineno - 1:node.end_lineno] = [""] * (node.end_lineno - node.lineno + 1)
        return "\n".join(lines)
    except SyntaxError:
        pass
    return content

SEVERITY_WEIGHTS = {"critical": 20, "major": 10, "minor": 3}

def _static_analyze(content: str) -> dict:
    """对单文件执行全量静态分析, 返回结构化结果 + 得分"""
    result = {"syntax": [], "imports": [], "complexity": [], "naming": [], "security": [], "score": 100}
    all_issues = []
    result["syntax"] = _static_check_syntax(content)
    all_issues.extend(result["syntax"])
    result["security"] = _static_check_security(_strip_self_check_code(content))
    all_issues.extend(result["security"])
    if not result["syntax"]:
        try:
            tree = ast.parse(content)
            result["imports"] = _static_check_imports(tree, content)
            result["complexity"] = _static_check_complexity(tree)
            result["naming"] = _static_check_naming(tree)
            all_issues.extend(result["imports"] + result["complexity"] + result["naming"])
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

def review_file(path: str, use_llm: bool) -> dict:
    content = Path(path).read_text(encoding="utf-8", errors="ignore")
    static = _static_analyze(content)
    file_result = {
        "file": path,
        "static_score": static["score"],
        "static_issues": static["all_issues"],
        "issues": [dict(i, file=path) for i in static["all_issues"]],
    }
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

def main():
    ap = argparse.ArgumentParser(description="CodeReview Minimal — 审代码不写代码的人用的审查器")
    ap.add_argument("target", help="要审查的文件或目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--score-only", action="store_true", help="只输出每文件得分")
    ap.add_argument("--llm", action="store_true", help="启用 LLM 增强审查(需配 LLM_API_KEY)")
    args = ap.parse_args()

    if not os.path.exists(args.target):
        print(f"路径不存在: {args.target}"); sys.exit(1)

    files = _collect_py_files(args.target)
    if not files:
        print("未找到 .py 文件"); sys.exit(0)

    results = [review_file(str(f), args.llm) for f in files]

    if args.score_only:
        for r in results:
            flag = "✅" if r["static_score"] >= 60 else "❌"
            print(f"{flag} {r['static_score']:3} {r['file']}")
        return

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
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
        if "model" in r and r["model"].get("overengineering"):
            print("   🧹 过度工程:")
            for o in r["model"]["overengineering"]:
                print(f"      - {o}")
        if "model" in r and r["model"].get("predictions"):
            print("   🔮 可证伪改进(修复后预期):")
            for p in r["model"]["predictions"]:
                print(f"      - {p}")

    # 汇总
    avg = sum(r["static_score"] for r in results) / len(results)
    total_issues = sum(len(r["issues"]) for r in results)
    print("\n" + "=" * 60)
    print(f"共 {len(results)} 个文件 | 平均静态得分 {avg:.0f}/100 | 问题总数 {total_issues}")
    bad = [r["file"] for r in results if r["static_score"] < 60]
    if bad:
        print(f"⚠️ 需关注(<60分): {', '.join(bad)}")

if __name__ == "__main__":
    main()
