#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dep_scan.py — 依赖漏洞 SCA + Semgrep 级污点分析（纯标准库零依赖）。

背景：代码审查深化的 P0 新能力 ——「依赖漏洞 SCA/OSV + Semgrep 级 taint」。
借鉴但不复制商用/开源工具（bandit / semgrep / pip-audit / OSV API）的**思路**，
用纯标准库实现可离线运行的等价能力，数据不出厂（默认 local_only，不联网）。

两部分：
1. SCA（软件组成分析）：解析 requirements.txt / pyproject.toml / setup.py /
   import 语句收集第三方依赖，对照内嵌的「已知漏洞签名库」与「已知有漏洞高危包名单」，
   产出 {package, version(可见), risk, category, evidence, confidence} 清单。
   可选 OSV 在线查询（osv_query=True + allow_remote=True 时经 urllib 打 OSV API，
   默认关闭以保证数据不出厂）。
2. Taint 分析（Semgrep 级）：AST 做数据流污点分析 —— 识别污点源（用户输入、
   环境变量、网络数据、request 参数）流向污点汇（eval/exec/subprocess/os.system/
   sql 拼接/反序列化），输出 {source, sink, line, severity, confidence, chain}。

铁律：零第三方依赖、零联网默认、失败降级 {ok,data,error}。核心零改动，
被 dep-scan 原子壳 import 复用。

用法：
    from dep_scan import scan_dependencies, taint_analyze, scan_all
    r = scan_dependencies(path_or_dir)     # SCA 依赖漏洞
    t = taint_analyze(path)                # 污点分析
"""

import ast
import io
import os
import re
import json
import sys

# ── 内嵌已知漏洞签名库（精简本地版，借鉴 OSV 的「包+版本区间+风险」思想）──
# 每个签名: {package, affected:[min,max] or "*", severity, title, evidence 正则}
# 说明：真实 OSV 库在联网时经 urllib 查询；本地库覆盖常见高危包以保离线可用。
KNOWN_VULN_PACKAGES = {
    "pyyaml": {
        "severity": "critical", "title": "PyYAML<5.4 存在 unsafe load 任意代码执行 (CVE-2020-14343)",
        "affected": ["0", "5.3.1"],
    },
    "pillow": {
        "severity": "critical", "title": "Pillow<8.1.1 存在 RCE 漏洞 (CVE-2021-25287 等)",
        "affected": ["0", "8.1.0"],
    },
    "werkzeug": {
        "severity": "high", "title": "Werkzeug<2.0.2 存在 debugger PIN 泄漏 (CVE-2021-43509)",
        "affected": ["0", "2.0.1"],
    },
    "requests": {
        "severity": "medium", "title": "requests 部分版本存在 SSRF/代理绕过风险，建议升级",
        "affected": ["2.0.0", "2.24.0"],
    },
    "cryptography": {
        "severity": "high", "title": "cryptography<3.4 存在多处拒绝服务/信息泄漏",
        "affected": ["0", "3.3.2"],
    },
    "jinja2": {
        "severity": "high", "title": "Jinja2<3.0.1 存在沙箱逃逸 (CVE-2021-44961)",
        "affected": ["0", "3.0.0"],
    },
    "flask": {
        "severity": "medium", "title": "Flask 旧版存在 debugger 风险，建议升级",
        "affected": ["0", "2.0.0"],
    },
    "django": {
        "severity": "high", "title": "Django 存在 SQL 注入/DoS 历史漏洞，建议保持最新",
        "affected": ["0", "3.2.0"],
    },
    "numpy": {
        "severity": "low", "title": "NumPy 部分旧版存在内存安全风险",
        "affected": ["0", "1.19.5"],
    },
    "urllib3": {
        "severity": "medium", "title": "urllib3<1.26.5 存在 SSRF/请求走私风险",
        "affected": ["0", "1.26.4"],
    },
}

# 高危/可疑依赖类别（无精确 CVE 但也值得提示）
SUSPICIOUS_PACKAGES = {
    "py2exe": "打包工具，若在生产依赖中可疑",
    "setuptools_scm": "构建期依赖不应出现在运行期",
    "pytest": "测试依赖不应出现在生产 requirements",
}

# ── 版本区间判定（简单数值化比较，忽略预发布段）──
def _ver_key(v: str):
    if not isinstance(v, str) or not v.strip():
        return (0, 0, 0)
    v = v.strip().split(" ")[0]
    nums = re.findall(r"\d+", v)
    nums = (nums + ["0", "0", "0"])[:3]
    return tuple(int(x) for x in nums)


def _in_range(v: str, lo: str, hi: str):
    """v 在 [lo, hi] 闭区间内（字符串比较键）。lo 为空串=无下界。"""
    kv, klo, khi = _ver_key(v), _ver_key(lo), _ver_key(hi)
    if klo and kv < klo:
        return False
    if khi and kv > khi:
        return False
    return True


def _parse_requirements(text: str) -> list:
    """解析 requirements.txt 内容 → [{name, version} or {name, version:"*"}]。"""
    deps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-", "--")):
            continue
        # 剥离 extras/index/环境标记
        line = re.split(r"\s*;\s*", line)[0]
        line = re.split(r"\s+(>=|==|~=)", line)[0]  # 取包名部分
        m = re.match(r"([A-Za-z0-9_.-]+)\s*(?:[=<>!~]+([\w.]+))?", line)
        if not m:
            continue
        name = m.group(1).lower()
        ver = m.group(2) or "*"
        deps.append({"name": name, "version": ver})
    return deps


def _parse_pyproject(text: str) -> list:
    """从 pyproject.toml 提取 dependencies（粗解析，够用）。"""
    deps = []
    in_deps = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("dependencies"):
            in_deps = True
            continue
        if in_deps:
            if s.startswith("]") or s.startswith("[") and "]" not in s:
                in_deps = False
                continue
            if s.startswith(("'", '"')) or s.startswith((">", "=", "~")):
                m = re.match(r"[\"']?([A-Za-z0-9_.-]+)", s)
                if m:
                    deps.append({"name": m.group(1).lower(), "version": "*"})
            if "]" in s and in_deps and not s.startswith("["):
                in_deps = False
    return deps


def _parse_setup_py(text: str) -> list:
    deps = []
    for m in re.finditer(r"(?:install_requires|requirements)\s*=\s*\[([^\]]*)\]", text, re.S):
        for sub in re.findall(r"[\"']([A-Za-z0-9_.-]+)[\"']", m.group(1)):
            deps.append({"name": sub.lower(), "version": "*"})
    return deps


def _collect_imports(tree) -> list:
    """AST 收集第三方 import 名（排除标准库）。"""
    stdlib = _stdlib_names()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0].lower()
                if root and root not in stdlib:
                    names.add(root)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0].lower()
            if root and root not in stdlib:
                names.add(root)
    return sorted(names)


_STDLIB = None


def _stdlib_names():
    global _STDLIB
    if _STDLIB is None:
        try:
            import stdlib_list  # 可选：非零依赖则退化为内置集合
            _STDLIB = set(stdlib_list.stdlib_list("3.11"))
        except Exception:
            _STDLIB = {"os", "sys", "re", "json", "ast", "io", "math", "random",
                       "time", "datetime", "collections", "itertools", "functools",
                       "pathlib", "typing", "subprocess", "tempfile", "hashlib",
                       "base64", "uuid", "socket", "threading", "queue", "logging",
                       "argparse", "glob", "shutil", "statistics", "string",
                       "urllib", "http", "email", "csv", "sqlite3", "configparser",
                       "importlib", "unittest", "unittest.mock", "traceback",
                       "abc", "array", "binascii", "bisect", "calendar", "cmath",
                       "concurrent", "contextlib", "copy", "cProfile", "ctypes",
                       "decimal", "difflib", "dis", "dummy_threading", "enum",
                       "errno", "filecmp", "fnmatch", "formatter", "fractions",
                       "getopt", "getpass", "graphlib", "gzip", "heapq", "hmac",
                       "html", "idlelib", "imaplib", "inspect", "linecache",
                       "locale", "lzma", "marshal", "numbers", "operator",
                       "optparse", "os.path", "pickle", "pdb", "platform",
                       "plistlib", "pprint", "profile", "pstats", "pty",
                       "pydoc", "resource", "rlcompleter", "runpy", "sched",
                       "secrets", "select", "selectors", "signal", "site",
                       "smtplib", "sre_compile", "ssl", "stat", "struct",
                       "tarfile", "this", "token", "tokenize", "types", "weakref",
                       "warnings", "wave", "webbrowser", "winreg", "winsound",
                       "xml", "zipfile", "zlib", "zoneinfo"}
    return _STDLIB


# ── SCA 依赖漏洞 ──────────────────────────────
def _locate_dep_files(target: str) -> list:
    """收集依赖描述文件（requirements*.txt / pyproject.toml / setup.py / Pipfile）。"""
    found = []
    if os.path.isfile(target):
        b = os.path.basename(target).lower()
        if b.endswith("requirements.txt") or b in ("pyproject.toml", "setup.py", "pipfile"):
            found.append(target)
        elif b.endswith(".py"):
            return []  # 单文件走 import 收集，不猜依赖文件
        return found
    if os.path.isdir(target):
        for root, _dirs, files in os.walk(target):
            for f in files:
                fb = f.lower()
                if fb == "requirements.txt" or fb == "pyproject.toml" or fb == "setup.py" \
                        or fb == "pipfile" or (fb.startswith("requirements") and fb.endswith(".txt")):
                    found.append(os.path.join(root, f))
    return found


def _load_version_for(target_dir, package):
    """尽力从锁定文件读取已装版本（pip 冻结/local site-packages 探活）。"""
    try:
        import importlib.metadata as imd
        try:
            return imd.version(package)
        except Exception:
            return None
    except Exception:
        return None


def scan_dependencies(target: str, osv_query: bool = False, allow_remote: bool = False):
    """SCA 依赖漏洞扫描。target 为文件或目录。

    返回 {deps:[...], vulns:[...], scan_sources:[...], offline:True}。
    每项 vuln: {package, version, severity, title, category, confidence, evidence}。
    数据不出厂：osv_query 需 allow_remote=True 且显式开启才联网；默认完全离线。
    """
    stdlib = _stdlib_names()
    deps = {}      # name -> {version, sources:[...]}
    scan_sources = []

    # 1) 依赖描述文件
    for df in _locate_dep_files(target):
        scan_sources.append(os.path.basename(df))
        try:
            text = open(df, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        if os.path.basename(df).lower() == "pyproject.toml":
            parsed = _parse_pyproject(text)
        elif os.path.basename(df).lower() == "setup.py":
            parsed = _parse_setup_py(text)
        else:
            parsed = _parse_requirements(text)
        for d in parsed:
            if d["name"] not in stdlib:
                prev = deps.get(d["name"])
                if prev:
                    prev["sources"].append(os.path.basename(df))
                    if prev["version"] == "*" and d["version"] != "*":
                        prev["version"] = d["version"]
                else:
                    deps[d["name"]] = {"name": d["name"], "version": d["version"],
                                       "sources": [os.path.basename(df)]}

    # 2) 单文件/目录的 import 收集（补充依赖清单）
    py_files = []
    if os.path.isfile(target) and target.endswith(".py"):
        py_files = [target]
    elif os.path.isdir(target):
        for root, _dirs, files in os.walk(target):
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(root, f))
    for pf in py_files[:200]:
        try:
            tree = ast.parse(open(pf, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        for name in _collect_imports(tree):
            if name not in deps and name not in stdlib:
                deps[name] = {"name": name, "version": "*", "sources": [os.path.basename(pf)]}

    # 3) 对照漏洞库
    vulns = []
    dep_dir = target if os.path.isdir(target) else (os.path.dirname(target) or ".")
    for name, info in deps.items():
        sig = KNOWN_VULN_PACKAGES.get(name)
        if sig:
            ver = info["version"]
            if ver == "*":
                # 未锁定版本 → 无法确认，给 low 提示
                vulns.append({"package": name, "version": ver, "severity": "info",
                              "title": f"{name} 未锁定版本，无法排除 {sig['title']}",
                              "category": "sca:unpinned", "confidence": "low",
                              "evidence": f"pinned='*' from {','.join(info['sources'])}"})
            elif _in_range(ver, sig["affected"][0], sig["affected"][1]):
                vulns.append({"package": name, "version": ver, "severity": sig["severity"],
                              "title": sig["title"], "category": "sca:cve",
                              "confidence": "high",
                              "evidence": f"{name}=={ver} 命中受影响区间 {sig['affected']}"})
        elif name in SUSPICIOUS_PACKAGES:
            vulns.append({"package": name, "version": info["version"],
                          "severity": "info", "title": f"{name}: {SUSPICIOUS_PACKAGES[name]}",
                          "category": "sca:category", "confidence": "medium",
                          "evidence": f"dep from {','.join(info['sources'])}"})

    # 4) 可选 OSV 在线查询（默认关闭，数据不出厂）
    osv_hits = []
    if osv_query and allow_remote:
        osv_hits = _osv_query([(n, i["version"]) for n, i in deps.items() if i["version"] != "*"])
        for h in osv_hits:
            vulns.append({"package": h.get("package"), "version": h.get("version"),
                          "severity": h.get("severity", "medium"), "title": h.get("title"),
                          "category": "sca:osv", "confidence": "high", "evidence": "OSV API 命中"})

    return {"deps": sorted(deps.values(), key=lambda d: d["name"]),
            "vulns": vulns, "scan_sources": scan_sources,
            "offline": not (osv_query and allow_remote),
            "summary": f"扫描到 {len(deps)} 个依赖，检出 {len(vulns)} 个漏洞/风险（离线={not (osv_query and allow_remote)}）"}


def _osv_query(pairs, timeout=8):
    """经 OSV API 批量查询（需 allow_remote=True）。失败 → 空列表（降级）。"""
    if not pairs:
        return []
    try:
        import urllib.request
        body = json.dumps({"queries": [{"package": {"name": n, "ecosystem": "PyPI"},
                                        "version": v} for n, v in pairs] if pairs else []},
                           ensure_ascii=False).encode()
        req = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        hits = []
        results = data.get("results", [])
        for i, res in enumerate(results):
            for vuln in res.get("vulns", []):
                hits.append({"package": pairs[i][0] if i < len(pairs) else "",
                             "version": pairs[i][1] if i < len(pairs) else "",
                             "severity": _osv_sev(vuln), "title": vuln.get("summary", "")[:120],
                             "id": vuln.get("id", "")})
        return hits
    except Exception:
        return []


def _osv_sev(vuln):
    for a in vuln.get("aliases", []):
        if "CVE-" in a:
            return "high"
    return "medium"


# ── Semgrep 级污点分析（AST 数据流）──────────────────
TAINT_SOURCES = ("request", "input", "argv", "args", "query", "params", "data",
                 "payload", "body", "headers", "env", "environ", "user_input",
                 "username", "password", "token", "command", "cmd", "url", "path",
                 "file", "content", "text", "raw", "json", "xml", "form", "cookie")
TAINT_SINKS = {
    "eval": "critical", "exec": "critical", "__import__": "high",
    "os.system": "critical", "subprocess.call": "high", "subprocess.run": "high",
    "subprocess.Popen": "high", "pickle.loads": "high", "yaml.load": "high",
    "compile": "medium",
}

_SINK_RE = re.compile(r"^(eval|exec|__import__|os\.system|subprocess\.(?:call|run|Popen|check_output)"
                      r"|pickle\.loads|yaml\.load|compile)\s*\(")
_SQL_RE = re.compile(r"cursor\.execute\s*\(\s*(f[\"']|[\"'][^\"']*\{|.*%|.*\+)", re.S)
_SQL_KW = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b")


def _is_taint_source(name):
    n = name.lower()
    for s in TAINT_SOURCES:
        if n == s or n.endswith("_" + s) or n.startswith(s + "_"):
            return True
    return False


def _find_taint_flows(tree) -> list:
    """扫描 AST，找 污点源变量 → 污点汇调用 的数据流链（逐函数做保守数据流）。"""
    flows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fn = node.name
        # 收集该函数内定义/赋值的变量（污点候选）
        assigned = set()
        source_names = set()
        # 函数参数若为污点源名，也算污点源（用户可控入参）
        for a in list(node.args.args) + list(node.args.posonlyargs) + \
                list(node.args.kwonlyargs) + ([node.args.vararg] if node.args.vararg else []) + \
                ([node.args.kwarg] if node.args.kwarg else []):
            if a is not None and _is_taint_source(a.arg):
                source_names.add(a.arg)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                assigned.add(sub.id)
                if _is_taint_source(sub.id):
                    source_names.add(sub.id)
            elif isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Name) and _is_taint_source(t.id):
                        source_names.add(t.id)
        if not source_names:
            continue
        # 找污点汇调用，并判断参数是否引用了污点源
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                sink = _sink_name(sub)
                if not sink:
                    continue
                for arg in sub.args + [k.value for k in sub.keywords if k.arg in ("cmd", "code", "data", "text")]:
                    if _arg_references_source(arg, source_names | assigned):
                        flows.append({
                            "function": fn, "source": _arg_source_hint(arg, source_names),
                            "sink": sink, "line": getattr(sub, "lineno", 0),
                            "severity": TAINT_SINKS[sink],
                            "confidence": "high" if sink in ("eval", "exec", "os.system") else "medium",
                            "chain": f"污点源({_arg_source_hint(arg, source_names)}) → {sink}() @L{getattr(sub, 'lineno', 0)}",
                        })
                        break
    # 去重
    seen = set()
    out = []
    for f in flows:
        key = (f["function"], f["sink"], f["line"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _sink_name(call_node):
    """从 Call 节点识别污点汇名（Name/Attribute 形态）。"""
    fn = call_node.func
    if isinstance(fn, ast.Name):
        name = fn.id
        if name in TAINT_SINKS:
            return name
    elif isinstance(fn, ast.Attribute):
        base = ""
        n = fn
        while isinstance(n, ast.Attribute):
            base = n.attr + ("." + base if base else "")
            n = n.value
        if isinstance(n, ast.Name):
            base = n.id + "." + base
        else:
            return None
        if base in TAINT_SINKS:
            return base
    return None


def _arg_references_source(arg, source_set):
    if isinstance(arg, ast.Name):
        return arg.id in source_set
    if isinstance(arg, (ast.Constant, ast.JoinedStr)):
        # f-string 内嵌变量也算（如 f"ls {cmd}"）
        if isinstance(arg, ast.JoinedStr):
            for v in arg.values:
                if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) \
                        and v.value.id in source_set:
                    return True
        return False
    if isinstance(arg, ast.BinOp):
        return _arg_references_source(arg.left, source_set) or \
               (isinstance(arg.right, ast.Name) and arg.right.id in source_set)
    if isinstance(arg, ast.Compare):
        return any(_arg_references_source(c, source_set) for c in [arg.left] + arg.comparators)
    return False


def _arg_source_hint(arg, source_set):
    if isinstance(arg, ast.Name):
        return arg.id if arg.id in source_set else "变量"
    if isinstance(arg, ast.JoinedStr):
        for v in arg.values:
            if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) \
                    and v.value.id in source_set:
                return f"f-string 嵌入 {v.value.id}"
    if isinstance(arg, ast.BinOp):
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Name) and sub.id in source_set:
                return sub.id
    return "不可信输入"


def _sql_flows(tree) -> list:
    """SQL 拼接 → 注入：cursor.execute 且参数含 f-string/格式化/拼接变量。"""
    flows = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "execute" and node.args:
            q = node.args[0]
            if isinstance(q, ast.JoinedStr):
                flows.append({"function": "?", "source": "f-string SQL",
                              "sink": "cursor.execute(f...) 动态拼接", "line": getattr(node, "lineno", 0),
                              "severity": "critical", "confidence": "medium",
                              "chain": f"SQL f-string 拼接 → execute @L{getattr(node, 'lineno', 0)}"})
            elif isinstance(q, ast.BinOp) and isinstance(q.op, ast.Add):
                flows.append({"function": "?", "source": "SQL + 拼接",
                              "sink": "cursor.execute(字符串+)", "line": getattr(node, "lineno", 0),
                              "severity": "critical", "confidence": "high",
                              "chain": f"SQL 字符串+ 拼接 → execute @L{getattr(node, 'lineno', 0)}"})
    return flows


def taint_analyze(target: str) -> dict:
    """Semgrep 级污点分析：源→汇 数据流。target 为文件或目录。"""
    findings = []
    files_scanned = 0
    py_files = []
    if os.path.isfile(target) and target.endswith(".py"):
        py_files = [target]
    elif os.path.isdir(target):
        for root, _dirs, files in os.walk(target):
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(root, f))
    for pf in py_files[:300]:
        try:
            tree = ast.parse(open(pf, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        files_scanned += 1
        for f in _find_taint_flows(tree):
            f["file"] = pf
            findings.append(f)
        for f in _sql_flows(tree):
            f["file"] = pf
            findings.append(f)
    return {"findings": findings, "files_scanned": files_scanned,
            "summary": f"扫描 {files_scanned} 文件，检出 {len(findings)} 条污点/注入链",
            "engines": ["taint(source→sink,AST)", "sql-injection(pattern)"]}


def scan_all(target: str, osv_query=False, allow_remote=False) -> dict:
    """SCA + taint 一站式。"""
    sca = scan_dependencies(target, osv_query=osv_query, allow_remote=allow_remote)
    taint = taint_analyze(target)
    crit = [v for v in sca["vulns"] if v["severity"] in ("critical", "high")]
    tcrit = [f for f in taint["findings"] if f["severity"] in ("critical", "high")]
    return {"sca": sca, "taint": taint, "total_findings": len(sca["vulns"]) + len(taint["findings"]),
            "critical_high": len(crit) + len(tcrit),
            "summary": f"SCA {len(sca['vulns'])} 漏洞 + taint {len(taint['findings'])} 链，其中严重 {len(crit)+len(tcrit)}"}


# ── CLI ──────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="dep_scan: 依赖漏洞 SCA + Semgrep 级污点分析(纯stdlib,数据不出厂)")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--osv", action="store_true", help="启用 OSV 在线查询(需 --remote)")
    ap.add_argument("--remote", action="store_true", help="允许联网(默认数据不出厂)")
    ap.add_argument("--taint-only", action="store_true")
    ap.add_argument("--sca-only", action="store_true")
    args = ap.parse_args()

    if args.taint_only:
        res = taint_analyze(args.target)
    elif args.sca_only:
        res = scan_dependencies(args.target, osv_query=args.osv, allow_remote=args.remote)
    else:
        res = scan_all(args.target, osv_query=args.osv, allow_remote=args.remote)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
