#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""security_scan.py — CodeAgent 安全扫描核心（纯 stdlib，数据不出厂）。

安全原子 security-scan 复用的核心模块：10 安全维度 + 危险函数库 + secret 检测 + 误报治理。
零第三方依赖。被 agents/security/security-scan/main.py 壳 import 调用，核心零改动。

能力（供 security 原子 provides）：
  security.scan    — 10 维度一站式安全扫描（默认）
  security.secret  — secret/硬编码密钥检测
  security.govern  — 误报治理（自指剔除 + 去重 + 分级）
  security.dim     — 单维度扫描
"""
import ast
import re
import os
from pathlib import Path

from pathguard import safe_resolve, safe_read_text, assert_within

# ── 10 安全维度 ──
SECURITY_DIMENSIONS = [
    "注入", "认证", "授权", "反序列化", "文件",
    "SSRF", "加密", "配置", "业务", "供应链",
]
DIMENSION_TIER = {
    "注入": "P0", "反序列化": "P0", "文件": "P0", "SSRF": "P0",
    "认证": "P1", "授权": "P1", "加密": "P1", "供应链": "P1",
    "配置": "P2", "业务": "P2",
}


# ── 危险函数库：危险调用 → (维度, 严重度, 建议) ──
DANGEROUS_FUNCS = {
    # 注入
    "eval": ("注入", "critical", "代码执行"), "exec": ("注入", "critical", "代码执行"),
    "compile": ("注入", "major", "动态编译不可信输入"), "os.system": ("注入", "critical", "命令执行"),
    "os.popen": ("注入", "critical", "命令执行"), "os.spawn": ("注入", "major", "命令执行"),
    "subprocess.Popen": ("注入", "major", "命令执行"), "subprocess.run": ("注入", "major", "命令执行"),
    "subprocess.call": ("注入", "major", "命令执行"), "subprocess.check_call": ("注入", "major", "命令执行"),
    "cursor.execute": ("注入", "critical", "SQL执行"), "execute": ("注入", "major", "SQL/动态执行"),
    "render_template_string": ("注入", "critical", "模板注入"),
    "document.write": ("注入", "major", "XSS"), "innerHTML": ("注入", "major", "XSS"),
    # 反序列化
    "pickle.loads": ("反序列化", "critical", "反序列化"), "pickle.load": ("反序列化", "critical", "反序列化"),
    "yaml.load": ("反序列化", "critical", "反序列化"), "marshal.loads": ("反序列化", "major", "反序列化"),
    "jsonpickle.decode": ("反序列化", "major", "反序列化"),
    # 文件
    "open": ("文件", "major", "文件读写"), "os.remove": ("文件", "major", "文件删除"),
    "os.rename": ("文件", "major", "文件操作"), "shutil.rmtree": ("文件", "major", "递归删除"),
    "zipfile.ZipFile.extractall": ("文件", "critical", "zip解压(zip-slip)"),
    "os.path.join": ("文件", "minor", "路径拼接(需防穿越)"),
    # SSRF
    "requests.get": ("SSRF", "major", "SSRF"), "urllib.request.urlopen": ("SSRF", "major", "SSRF"),
    "urlopen": ("SSRF", "major", "SSRF"), "requests.post": ("SSRF", "minor", "SSRF"),
    # 加密
    "hashlib.md5": ("加密", "major", "弱哈希"), "hashlib.sha1": ("加密", "major", "弱哈希"),
    "cryptography.fernet": ("加密", "minor", "对称加密(需看密钥管理)"),
    # 认证/授权
    "set_password": ("认证", "minor", "口令设置"), "login": ("认证", "minor", "认证入口"),
    "is_admin": ("授权", "minor", "权限判断"),
}

# ── secret 检测模式 ──
SECRET_PATTERNS = [
    ("AWS Access Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "critical"),
    ("AWS Secret Key", re.compile(r"(?i)\baws_secret_access_key\b\s*=\s*['\"][A-Za-z0-9/+=]{40}['\"]"), "critical"),
    ("私钥(PEM)", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"), "critical"),
    ("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), "critical"),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "major"),
    ("Slack Token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"), "major"),
    ("JWT(HS256 secret)", re.compile(r"(?i)\b(jwt_?secret|secret_key|SECRET_KEY)\b\s*=\s*['\"][^'\"]{16,}['\"]"), "major"),
    ("硬编码口令", re.compile(r"(?i)\b(password|passwd|pwd|secret|api_key|apikey|token|client_secret)\b\s*=\s*['\"][^'\"]{6,}['\"]"), "major"),
    ("高熵hex(>=32位)", re.compile(r"\b[0-9a-f]{32}\b"), "minor"),
    ("高熵base64(>=24位)", re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"), "minor"),
]

# ── 自指规则名（误报治理：剔除扫描器自身）──
_SELF_NAMES = {"SECURITY_DIMENSIONS", "DANGEROUS_FUNCS", "SECRET_PATTERNS",
               "_SELF_NAMES", "TAINT_SOURCES", "TAINT_SINKS", "SECURITY_DIMENSIONS"}


def _strip_self(content: str) -> str:
    """剔除扫描器自身/规则常量/说明注释，防"扫到自己"自指误报。"""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    lines = content.split("\n")
    drop = set()
    danger = re.compile(r"(SELECT|INSERT|UPDATE|DELETE|pickle\.loads|yaml\.load|eval|exec"
                        r"|os\.system|cursor\.execute|AKIA|BEGIN \w+ PRIVATE KEY|password)")
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = {t.id for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
                     if isinstance(t, ast.Name)}
            if names & _SELF_NAMES:
                drop.update(range(node.lineno - 1, (node.end_lineno or node.lineno)))
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") and danger.search(line):
            drop.add(i)
    for i in drop:
        lines[i] = ""
    return "\n".join(lines)


# ── 维度检查函数 ──
def _check_injection(content: str) -> list:
    out = []
    if re.search(r'\b(SELECT|INSERT|UPDATE|DELETE)\b.*?(f["\']|\+\s*["\'a-zA-Z_]|%["\']|\.format\(|\{[^}]*\})', content, re.S):
        out.append({"dimension": "注入", "severity": "critical", "title": "SQL 注入风险(字符串拼接查询)",
                    "suggestion": "用参数化查询/占位符，避免把变量直接拼进 SQL", "confidence": "high"})
    if re.search(r'subprocess\.[a-z]+\([^)]*shell\s*=\s*True', content, re.I):
        out.append({"dimension": "注入", "severity": "critical", "title": "命令注入(shell=True)",
                    "suggestion": "避免 shell=True，用参数列表传命令", "confidence": "high"})
    if re.search(r'os\.system\s*\([^)]*[\+\{]', content):
        out.append({"dimension": "注入", "severity": "major", "title": "命令拼接(os.system 动态字符串)",
                    "suggestion": "改用 subprocess 参数列表传参", "confidence": "high"})
    if re.search(r'render_template_string\s*\(', content):
        out.append({"dimension": "注入", "severity": "critical", "title": "模板注入风险(render_template_string)",
                    "suggestion": "模板内勿注入用户输入；用沙箱模板", "confidence": "medium"})
    if re.search(r'\.innerHTML\s*=|dangerouslySetInnerHTML', content):
        out.append({"dimension": "注入", "severity": "major", "title": "XSS: innerHTML 注入",
                    "suggestion": "用 textContent/escape 转义输出", "confidence": "medium"})
    return out


def _check_auth(content: str) -> list:
    out = []
    if re.search(r'\b(admin|administrator|root)\b\s*/\s*\1\b|user\s*=\s*["\']admin["\']\s*,\s*pass\s*=\s*["\']admin["\']', content, re.I):
        out.append({"dimension": "认证", "severity": "critical", "title": "默认弱口令(admin/admin)",
                    "suggestion": "强制强口令+初始化修改机制", "confidence": "medium"})
    if re.search(r'\b(login|auth|verify)\s*\([^)]*\)\s*:\s*(?=\s*return\s+True)', content):
        out.append({"dimension": "认证", "severity": "major", "title": "认证函数恒真(未校验即通过)",
                    "suggestion": "补齐密码/凭据校验", "confidence": "medium"})
    if re.search(r'user\s*=\s*request\.[a-z_]+\.get\(["\'](user|name)["\']\)[^;\n]*\bwithout\b|no_auth|@login_required', content, re.I):
        # 注解缺失兜底：handler 无鉴权装饰器难以静态判定，用 @login_required 缺失提示
        pass
    return out


def _check_authz(content: str) -> list:
    out = []
    if re.search(r'\.get\([^)]*\.get\(|request\.[a-z_]+\.get\(["\'](id|uid|user_id)["\']\)', content) and "is_admin" not in content and "role" not in content and "authorize" not in content:
        out.append({"dimension": "授权", "severity": "major", "title": "越权风险(IDOR): 直用请求参数取资源且无权限校验",
                    "suggestion": "校验当前用户/角色对资源的访问权(对象级授权)", "confidence": "medium"})
    if re.search(r'if\s+.*(user|role|admin).*:\s*\n\s*[^#]*$', content) and "return False" not in content and "deny" not in content.lower():
        out.append({"dimension": "授权", "severity": "minor", "title": "权限判断可能缺少拒绝分支",
                    "suggestion": "权限校验失败需显式拒绝并返回 403", "confidence": "low"})
    return out


def _check_deserialization(content: str) -> list:
    out = []
    if re.search(r'pickle\.loads?\s*\(', content):
        out.append({"dimension": "反序列化", "severity": "critical", "title": "不安全反序列化(pickle)",
                    "suggestion": "勿对不可信数据 pickle.loads；改用 JSON/安全格式", "confidence": "high"})
    if re.search(r'yaml\.load\s*\([^)]*\)(?!\s*,\s*Loader)', content):
        out.append({"dimension": "反序列化", "severity": "critical", "title": "不安全反序列化(yaml.load 无安全 Loader)",
                    "suggestion": "用 yaml.safe_load", "confidence": "high"})
    if re.search(r'\bmarshal\.loads?\s*\(', content):
        out.append({"dimension": "反序列化", "severity": "major", "title": "不安全反序列化(marshal)",
                    "suggestion": "marshal 不可信数据可执行任意代码", "confidence": "high"})
    return out


def _check_file(content: str) -> list:
    out = []
    if re.search(r'open\s*\([^)]*["\']w["\']|\bopen\s*\([^)]*(path|filename|name|user_input)', content):
        out.append({"dimension": "文件", "severity": "major", "title": "任意文件写风险(open 用户可控路径)",
                    "suggestion": "校验路径在允许目录内，防路径穿越/任意写", "confidence": "medium"})
    if re.search(r'(path|filename|name|dir|target)\s*=\s*request\.[a-z_]+\.get\([^)]*\)[^;\n]*open\s*\(', content, re.S):
        out.append({"dimension": "文件", "severity": "critical", "title": "路径穿越风险(请求参数直达 open)",
                    "suggestion": "限制文件访问于白名单目录，拒绝 ../", "confidence": "medium"})
    if re.search(r'\.extractall\s*\(|\.extract\s*\(', content):
        out.append({"dimension": "文件", "severity": "critical", "title": "zip 解压任意写(zip-slip)",
                    "suggestion": "解压前校验文件名不含 ../ 或绝对路径", "confidence": "medium"})
    if re.search(r'\bshutil\.rmtree\s*\([^)]*(path|dir|name)', content):
        out.append({"dimension": "文件", "severity": "major", "title": "递归删除风险(rmtree 用户可控路径)",
                    "suggestion": "确认路径范围，防误删/穿越删除", "confidence": "medium"})
    return out


def _check_ssrf(content: str) -> list:
    out = []
    if re.search(r'(requests\.(get|post)|urlopen)\s*\([^)]*(url|target|host|link|input)', content):
        out.append({"dimension": "SSRF", "severity": "major", "title": "SSRF 风险(用户可控 URL 发起请求)",
                    "suggestion": "校验 URL 协议/域名白名单，禁内网/localhost/元数据", "confidence": "medium"})
    if re.search(r'(requests\.(get|post)|urlopen)\s*\([^)]*[\+\{]', content):
        out.append({"dimension": "SSRF", "severity": "major", "title": "SSRF/URL 拼接风险",
                    "suggestion": "用 allowlist 校验 host，勿拼接用户输入", "confidence": "medium"})
    return out


def _check_crypto(content: str) -> list:
    out = []
    if re.search(r'hashlib\.(md5|sha1)\s*\(', content):
        out.append({"dimension": "加密", "severity": "major", "title": "弱哈希(md5/sha1 用于安全场景)",
                    "suggestion": "用 sha256/bcrypt/argon2 等强哈希", "confidence": "medium"})
    if re.search(r'\bCipher\.(DES|RC4)|MODE_ECB', content):
        out.append({"dimension": "加密", "severity": "critical", "title": "弱加密算法(DES/RC4/ECB)",
                    "suggestion": "用 AES-GCM/ChaCha20 等认证加密", "confidence": "medium"})
    if re.search(r'http://', content) and re.search(r'(url|endpoint|api|server)', content):
        out.append({"dimension": "加密", "severity": "minor", "title": "明文 HTTP 传输",
                    "suggestion": "生产环境用 HTTPS", "confidence": "low"})
    return out


def _check_config(content: str) -> list:
    out = []
    if re.search(r'debug\s*=\s*True', content):
        out.append({"dimension": "配置", "severity": "major", "title": "调试模式开启(debug=True)",
                    "suggestion": "生产环境关 debug，防泄露堆栈/敏感信息", "confidence": "high"})
    if re.search(r'CORS|Access-Control-Allow-Origin.*\*|allowed_origins\s*=\s*\[["\']\*', content):
        out.append({"dimension": "配置", "severity": "major", "title": "宽松 CORS(允许任意源)",
                    "suggestion": "CORS 白名单具体域名，勿用 *", "confidence": "medium"})
    if re.search(r'ALLOWED_HOSTS\s*=\s*\[["\']\*', content):
        out.append({"dimension": "配置", "severity": "major", "title": "ALLOWED_HOSTS=*(Host 头攻击)",
                    "suggestion": "配置具体域名白名单", "confidence": "high"})
    return out


def _check_business(content: str) -> list:
    out = []
    if re.search(r'(price|amount|quantity|total|count)\s*=\s*request\.[a-z_]+\.get\([^)]*\)[^;\n]*(price|amount|total)', content, re.S):
        out.append({"dimension": "业务", "severity": "critical", "title": "价格/金额由客户端参数直接指定(逻辑篡改)",
                    "suggestion": "金额/数量以服务端核算为准，勿信客户端", "confidence": "medium"})
    if re.search(r'float\(request\.[a-z_]+\.get|int\(request\.[a-z_]+\.get', content):
        out.append({"dimension": "业务", "severity": "minor", "title": "业务数值未校验范围(负数/越界/超大)",
                    "suggestion": "校验取值范围/上下限，防业务逻辑滥用", "confidence": "low"})
    return out


def _check_supplychain(content: str) -> list:
    out = []
    if re.search(r'^\s*(requests|flask|django|pip|numpy|pandas|torch|tensorflow|pyyaml|cryptography|httpx|fastapi)\s*[><=!]', content, re.M) \
            and not re.search(r'==\s*[\'"\d]', content.split("\n")[0] if content else ""):
        out.append({"dimension": "供应链", "severity": "minor", "title": "依赖未锁版本(>=/< 无精确 pin)",
                    "suggestion": "requirements 用 == 精确锁定版本，防供应链投毒/回归", "confidence": "medium"})
    if re.search(r'pip\s+install\s+[^>]*\b--[^\n]*(pre|no-deps)|npm\s+install\s+--unsafe', content):
        out.append({"dimension": "供应链", "severity": "major", "title": "安装命令含不安全标志",
                    "suggestion": "审查安装来源，避免 --unsafe-perm/预发布", "confidence": "medium"})
    return out


_DIM_CHECKS = {
    "注入": _check_injection, "认证": _check_auth, "授权": _check_authz,
    "反序列化": _check_deserialization, "文件": _check_file, "SSRF": _check_ssrf,
    "加密": _check_crypto, "配置": _check_config, "业务": _check_business,
    "供应链": _check_supplychain,
}


# ── 主扫描 ──
def scan_security(content: str, dimensions=None) -> dict:
    """10 维度安全扫描单文件。返回 {dimensions, issues, by_dimension, by_tier, summary}。"""
    stripped = _strip_self(content)
    dims = dimensions or SECURITY_DIMENSIONS
    issues = []
    by_dim = {}
    for d in dims:
        fn = _DIM_CHECKS.get(d)
        if not fn:
            continue
        found = fn(stripped)
        for i in found:
            i["dimension"] = d
            i["tier"] = DIMENSION_TIER.get(d, "P2")
            i.setdefault("line", 0)
            i["source"] = "security-scan"
        issues += found
        if found:
            by_dim[d] = found
    issues = govern_false_positives(issues)
    by_tier = {"P0": 0, "P1": 0, "P2": 0}
    for i in issues:
        by_tier[i.get("tier", "P2")] += 1
    return {"dimensions": dims, "issues": issues, "by_dimension": by_dim,
            "by_tier": by_tier,
            "summary": f"安全扫描 {len(dims)} 维度, {len(issues)} 项 (P0={by_tier['P0']}/P1={by_tier['P1']}/P2={by_tier['P2']})",
            "total": len(issues)}


def scan_security_file(path: str) -> dict:
    content = safe_read_text(path)
    r = scan_security(content)
    r["file"] = str(path)
    return r


def scan_security_project(target: str, file_pattern: str = "*.py") -> dict:
    """扫描整个项目。target 为文件或目录。返回按文件聚合。"""
    p = safe_resolve(target)
    if p.is_file():
        files = [p]
    else:
        files = sorted(f for f in p.rglob(file_pattern)
                       if ".venv" not in str(f) and "node_modules" not in str(f))
    results = []
    by_tier = {"P0": 0, "P1": 0, "P2": 0}
    for f in files:
        assert_within(p, f)            # 扫描文件必须位于目标根内（防穿越逃逸）
        r = scan_security_file(str(f))
        results.append(r)
        for k in by_tier:
            by_tier[k] += r["by_tier"][k]
    total = sum(r["total"] for r in results)
    return {"files": results, "file_count": len(files), "total": total,
            "by_tier": by_tier,
            "summary": f"项目安全扫描 {len(files)} 文件, {total} 项 (P0={by_tier['P0']}/P1={by_tier['P1']}/P2={by_tier['P2']})"}


# ── secret 检测 ──
def detect_secrets(content: str) -> list:
    """检测硬编码密钥/高熵 secret。返回 [{type, severity, line, matched, suggestion}]。"""
    out = []
    lines = content.split("\n")
    for i, line in enumerate(lines):
        for name, pat, sev in SECRET_PATTERNS:
            m = pat.search(line)
            if m:
                out.append({"type": name, "severity": sev, "line": i + 1,
                            "matched": m.group(0)[:24] + "…",
                            "suggestion": f"检出{name}，移入密钥管理/环境变量，勿入库",
                            "tier": "P0" if sev == "critical" else "P1", "source": "secret"})
                break  # 一行报一个最高优先
    return govern_false_positives(out)


def detect_secrets_file(path: str) -> dict:
    content = safe_read_text(path)
    found = detect_secrets(content)
    return {"file": str(path), "secrets": found, "total": len(found),
            "summary": f"secret 检测: 命中 {len(found)} 项"}


# ── 误报治理 ──
def govern_false_positives(issues: list) -> list:
    """误报治理：按 (title, line) 去重 + 疑似误报降噪(P2) + 危险函数库交叉验证。"""
    seen, out = set(), []
    for i in issues:
        key = (i.get("title", ""), i.get("line", 0))
        if key in seen:
            continue
        seen.add(key)
        title = i.get("title", "")
        # 危险函数库交叉验证：命中库内危险函数的提升置信度，否则降为 low 并降噪
        func = next((f for f in DANGEROUS_FUNCS if f in title), None)
        if func and i.get("dimension") in ("注入", "反序列化", "文件", "SSRF"):
            i["danger_func"] = func
            i["confidence"] = i.get("confidence", "high")
        elif i.get("confidence") == "low":
            i["tier"] = "P2"  # low 置信 → 降噪 P2
            i["fp_note"] = "低置信启发式，倾向误报，仅供提示"
        out.append(i)
    return out


def dimension_scan(content: str, dimension: str) -> list:
    """单维度扫描。"""
    if dimension not in _DIM_CHECKS:
        return []
    stripped = _strip_self(content)
    return govern_false_positives(_DIM_CHECKS[dimension](stripped))


# ── CLI 自测 ──
def main(argv=None):
    import argparse, json
    ap = argparse.ArgumentParser(description="security_scan 核心自测")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--capability", default="scan",
                    choices=["scan", "secret", "project", "dim"])
    ap.add_argument("--dimension", default="注入")
    a = ap.parse_args(argv)
    if a.capability == "secret":
        r = detect_secrets_file(a.target)
    elif a.capability == "project":
        r = scan_security_project(a.target)
    elif a.capability == "dim":
        content = Path(a.target).read_text(encoding="utf-8", errors="ignore")
        r = {"dimension": a.dimension, "issues": dimension_scan(content, a.dimension)}
    else:
        r = scan_security_file(a.target)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
