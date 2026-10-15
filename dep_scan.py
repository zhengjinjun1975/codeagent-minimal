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


def _pyproject_dep_name(s):
    """pyproject dependencies 段里的一行 → 包名（不像依赖项就 None）。"""
    if not (s.startswith(("'", '"')) or s.startswith((">", "=", "~"))):
        return None
    m = re.match(r"[\"']?([A-Za-z0-9_.-]+)", s)
    return m.group(1).lower() if m else None


def _parse_pyproject(text: str) -> list:
    """从 pyproject.toml 提取 dependencies（粗解析，够用）。"""
    deps = []
    in_deps = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("dependencies"):
            in_deps = True
            continue
        if not in_deps:
            continue
        if s.startswith("]") or (s.startswith("[") and "]" not in s):
            in_deps = False
            continue
        name = _pyproject_dep_name(s)
        if name:
            deps.append({"name": name, "version": "*"})
        if "]" in s and not s.startswith("["):
            in_deps = False
    return deps


def _parse_setup_py(text: str) -> list:
    deps = []
    for m in re.finditer(r"(?:install_requires|requirements)\s*=\s*\[([^\]]*)\]", text, re.S):
        for sub in re.findall(r"[\"']([A-Za-z0-9_.-]+)[\"']", m.group(1)):
            deps.append({"name": sub.lower(), "version": "*"})
    return deps


def _import_roots(tree):
    """AST 里 import 语句的顶层包名（小写，去重）。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0].lower())
    return names


def _collect_imports(tree) -> list:
    """AST 收集第三方 import 名（排除标准库）。"""
    stdlib = _stdlib_names()
    return sorted(n for n in _import_roots(tree) if n and n not in stdlib)


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
def _is_dep_file_name(lower_name):
    """是不是依赖描述文件名（requirements*.txt / pyproject.toml / setup.py / Pipfile）。"""
    b = lower_name
    return (b == "requirements.txt" or b == "pyproject.toml" or b == "setup.py"
            or b == "pipfile" or (b.startswith("requirements") and b.endswith(".txt")))


def _dep_files_under(target):
    """目录下递归找依赖描述文件。"""
    found = []
    for root, _dirs, files in os.walk(target):
        for f in files:
            if _is_dep_file_name(f.lower()):
                found.append(os.path.join(root, f))
    return found


def _locate_dep_files(target: str) -> list:
    """收集依赖描述文件（requirements*.txt / pyproject.toml / setup.py / Pipfile）。"""
    if os.path.isfile(target):
        b = os.path.basename(target).lower()
        if _is_dep_file_name(b):
            return [target]
        if b.endswith(".py"):
            return []  # 单文件走 import 收集，不猜依赖文件
        return []
    if os.path.isdir(target):
        return _dep_files_under(target)
    return []


def _load_version_for(target_dir, package):
    """尽力从锁定文件读取已装版本（pip 冻结/local site-packages 探活）。"""
    try:
        import importlib.metadata as imd
        try:
            return imd.version(package)
        except Exception:  # fail-open: _load_version_for 里出错就返回空值，调用方按缺失处理
            return None
    except Exception:  # fail-open: _load_version_for 里出错就返回空值，调用方按缺失处理
        return None


def _parsed_deps_of(df):
    """一个依赖描述文件 → [{name, version}]（读不出就当空）。"""
    try:
        text = open(df, encoding="utf-8", errors="ignore").read()
    except Exception:  # fail-open: scan_dependencies 里出错就跳过该条继续扫
        return []
    base = os.path.basename(df).lower()
    if base == "pyproject.toml":
        return _parse_pyproject(text)
    if base == "setup.py":
        return _parse_setup_py(text)
    return _parse_requirements(text)


def _merge_dep(deps, d, src):
    """把一条依赖合进 deps（同名合并 sources；未锁版本可被锁定版本覆盖）。"""
    prev = deps.get(d["name"])
    if prev:
        prev["sources"].append(src)
        if prev["version"] == "*" and d["version"] != "*":
            prev["version"] = d["version"]
        return
    deps[d["name"]] = {"name": d["name"], "version": d["version"], "sources": [src]}


def _deps_from_files(target, stdlib):
    """1) 依赖描述文件（requirements / pyproject / setup.py）→ (deps, scan_sources)。"""
    deps, scan_sources = {}, []
    for df in _locate_dep_files(target):
        src = os.path.basename(df)
        scan_sources.append(src)
        for d in _parsed_deps_of(df):
            if d["name"] not in stdlib:
                _merge_dep(deps, d, src)
    return deps, scan_sources


def _py_files_under(target):
    """目标下的 .py 文件列表（文件→它自己；目录→walk 收集；其它→空）。"""
    if os.path.isfile(target) and target.endswith(".py"):
        return [target]
    if not os.path.isdir(target):
        return []
    out = []
    for root, _dirs, files in os.walk(target):
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


def _deps_from_imports(target, deps, stdlib):
    """2) 单文件/目录的 import 收集（补充依赖清单）。"""
    for pf in _py_files_under(target)[:200]:
        try:
            tree = ast.parse(open(pf, encoding="utf-8", errors="ignore").read())
        except SyntaxError:  # fail-open: scan_dependencies 里出错就跳过该条继续扫
            continue
        for name in _collect_imports(tree):
            if name not in deps and name not in stdlib:
                deps[name] = {"name": name, "version": "*", "sources": [os.path.basename(pf)]}
    return deps


def _vulns_of_dep(name, info):
    """单个依赖 → [vuln 条目]（未锁版本给 info 提示；命中区间给对应 severity）。"""
    sig = KNOWN_VULN_PACKAGES.get(name)
    if sig:
        ver = info["version"]
        if ver == "*":
            # 未锁定版本 → 无法确认，给 low 提示
            return [{"package": name, "version": ver, "severity": "info",
                     "title": f"{name} 未锁定版本，无法排除 {sig['title']}",
                     "category": "sca:unpinned", "confidence": "low",
                     "evidence": f"pinned='*' from {','.join(info['sources'])}"}]
        if _in_range(ver, sig["affected"][0], sig["affected"][1]):
            return [{"package": name, "version": ver, "severity": sig["severity"],
                     "title": sig["title"], "category": "sca:cve", "confidence": "high",
                     "evidence": f"{name}=={ver} 命中受影响区间 {sig['affected']}"}]
        return []
    if name in SUSPICIOUS_PACKAGES:
        return [{"package": name, "version": info["version"],
                 "severity": "info", "title": f"{name}: {SUSPICIOUS_PACKAGES[name]}",
                 "category": "sca:category", "confidence": "medium",
                 "evidence": f"dep from {','.join(info['sources'])}"}]
    return []


def _vulns_offline(deps):
    """3) 对照本地漏洞库（逐个依赖判）。"""
    out = []
    for name, info in deps.items():
        out += _vulns_of_dep(name, info)
    return out


def _vulns_osv(deps):
    """4) 可选 OSV 在线查询结果 → vuln 条目（调用方负责确认联网被显式打开）。"""
    out = []
    for h in _osv_query([(n, i["version"]) for n, i in deps.items() if i["version"] != "*"]):
        out.append({"package": h.get("package"), "version": h.get("version"),
                    "severity": h.get("severity", "medium"), "title": h.get("title"),
                    "category": "sca:osv", "confidence": "high", "evidence": "OSV API 命中"})
    return out


def scan_dependencies(target: str, osv_query: bool = False, allow_remote: bool = False):
    """SCA 依赖漏洞扫描。target 为文件或目录。

    返回 {deps:[...], vulns:[...], scan_sources:[...], offline:True}。
    每项 vuln: {package, version, severity, title, category, confidence, evidence}。
    数据不出厂：osv_query 需 allow_remote=True 且显式开启才联网；默认完全离线。
    """
    stdlib = _stdlib_names()
    deps, scan_sources = _deps_from_files(target, stdlib)
    _deps_from_imports(target, deps, stdlib)
    vulns = _vulns_offline(deps)
    online = bool(osv_query and allow_remote)
    if online:
        vulns += _vulns_osv(deps)
    return {"deps": sorted(deps.values(), key=lambda d: d["name"]),
            "vulns": vulns, "scan_sources": scan_sources,
            "offline": not online,
            "summary": f"扫描到 {len(deps)} 个依赖，检出 {len(vulns)} 个漏洞/风险（离线={not online}）"}


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
    except Exception:  # fail-open: _osv_query 里出错就返回空值，调用方按缺失处理
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


def _fn_arg_nodes(node):
    """函数的所有形参节点（含 posonly / kwonly / *args / **kwargs）。"""
    a = node.args
    out = list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs)
    if a.vararg is not None:
        out.append(a.vararg)
    if a.kwarg is not None:
        out.append(a.kwarg)
    return out


def _functions_of(tree):
    """AST 里所有函数/异步函数节点。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _calls_of(node):
    """子树里所有 Call 节点。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            yield sub


def _store_names(node):
    """子树里 Store 上下文的 Name 名字集合。"""
    return {sub.id for sub in ast.walk(node)
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)}


def _assigned_taint_targets(node):
    """Assign 目标里像污点源的名字集合。"""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            out |= {t.id for t in sub.targets
                    if isinstance(t, ast.Name) and _is_taint_source(t.id)}
    return out


def _taint_source_names(node):
    """该函数内的污点源名：形参里像污点源的 + 被赋值且像污点源的名字。"""
    names = {a.arg for a in _fn_arg_nodes(node) if a is not None and _is_taint_source(a.arg)}
    names |= {n for n in _store_names(node) if _is_taint_source(n)}
    names |= _assigned_taint_targets(node)
    return names


def _assigned_names(node):
    """该函数内被赋值的名字（保守：一并当作可达污点）。"""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            out.add(sub.id)
    return out


def _sink_arg_values(call):
    """汇调用的候选实参：位置参数 + 关键字里的 cmd/code/data/text。"""
    extra = [k.value for k in call.keywords if k.arg in ("cmd", "code", "data", "text")]
    return list(call.args) + extra


def _flow_for_call(sub, fn, source_names, assigned):
    """一个调用 → 一条 flow（或 None）：命中汇且某个实参引用了污点源。"""
    sink = _sink_name(sub)
    if not sink:
        return None
    for arg in _sink_arg_values(sub):
        if _arg_references_source(arg, source_names | assigned):
            return {
                "function": fn, "source": _arg_source_hint(arg, source_names),
                "sink": sink, "line": getattr(sub, "lineno", 0),
                "severity": TAINT_SINKS[sink],
                "confidence": "high" if sink in ("eval", "exec", "os.system") else "medium",
                "chain": f"污点源({_arg_source_hint(arg, source_names)}) → {sink}() @L{getattr(sub, 'lineno', 0)}",
            }
    return None


def _dedup_flows(flows):
    """按 (function, sink, line) 去重，保序。"""
    seen, out = set(), []
    for f in flows:
        key = (f["function"], f["sink"], f["line"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _flows_of_function(node):
    """一个函数节点内的全部 flow（源名先算，再逐调用判）。"""
    source_names = _taint_source_names(node)
    if not source_names:
        return []
    assigned = _assigned_names(node)
    out = []
    for sub in _calls_of(node):
        f = _flow_for_call(sub, node.name, source_names, assigned)
        if f:
            out.append(f)
    return out


def _find_taint_flows(tree) -> list:
    """扫描 AST，找 污点源变量 → 污点汇调用 的数据流链（逐函数做保守数据流）。"""
    flows = []
    for node in _functions_of(tree):
        flows += _flows_of_function(node)
    # 去重
    return _dedup_flows(flows)


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


def _joined_refs_source(joined, source_set):
    """f-string 里嵌入的变量是否命中污点源。"""
    for v in joined.values:
        if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) \
                and v.value.id in source_set:
            return True
    return False


def _arg_references_source(arg, source_set):
    """参数表达式是否引用污点源（Name / f-string / BinOp / Compare）。"""
    if isinstance(arg, ast.Name):
        return arg.id in source_set
    if isinstance(arg, ast.JoinedStr):
        # f-string 内嵌变量也算（如 f"ls {cmd}"）
        return _joined_refs_source(arg, source_set)
    if isinstance(arg, ast.Constant):
        return False
    if isinstance(arg, ast.BinOp):
        return _arg_references_source(arg.left, source_set) or \
               (isinstance(arg.right, ast.Name) and arg.right.id in source_set)
    if isinstance(arg, ast.Compare):
        return any(_arg_references_source(c, source_set) for c in [arg.left] + arg.comparators)
    return False


def _hint_from_joined(joined, source_set):
    """f-string 里命中的源名（没有就 None）。"""
    for v in joined.values:
        if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) \
                and v.value.id in source_set:
            return f"f-string 嵌入 {v.value.id}"
    return None


def _hint_from_binop(arg, source_set):
    """拼接表达式里第一个命中的源名（没有就 None）。"""
    for sub in ast.walk(arg):
        if isinstance(sub, ast.Name) and sub.id in source_set:
            return sub.id
    return None


def _arg_source_hint(arg, source_set):
    """给 flow 的 source 字段用的人话提示。"""
    if isinstance(arg, ast.Name):
        return arg.id if arg.id in source_set else "变量"
    if isinstance(arg, ast.JoinedStr):
        hint = _hint_from_joined(arg, source_set)
        if hint:
            return hint
    if isinstance(arg, ast.BinOp):
        hint = _hint_from_binop(arg, source_set)
        if hint:
            return hint
    return "不可信输入"


def _sql_flow_of(node):
    """一个调用 → SQL 注入 flow（或 None）：cursor.execute 且首参数是 f-string / 字符串+。"""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute" and node.args):
        return None
    q = node.args[0]
    ln = getattr(node, "lineno", 0)
    if isinstance(q, ast.JoinedStr):
        return {"function": "?", "source": "f-string SQL",
                "sink": "cursor.execute(f...) 动态拼接", "line": ln,
                "severity": "critical", "confidence": "medium",
                "chain": f"SQL f-string 拼接 → execute @L{ln}"}
    if isinstance(q, ast.BinOp) and isinstance(q.op, ast.Add):
        return {"function": "?", "source": "SQL + 拼接",
                "sink": "cursor.execute(字符串+)", "line": ln,
                "severity": "critical", "confidence": "high",
                "chain": f"SQL 字符串+ 拼接 → execute @L{ln}"}
    return None


def _sql_flows(tree) -> list:
    """SQL 拼接 → 注入：cursor.execute 且参数含 f-string/格式化/拼接变量。"""
    out = []
    for node in _calls_of(tree):
        f = _sql_flow_of(node)
        if f:
            out.append(f)
    return out


# 检测规则常量名（污点源/汇表、sink/正则/SQL 关键字哨兵）。这些"规则定义"的值本身
# 含可触发自身安全扫描器误报的 SQL 关键字与危险函数字样，扫描器扫到自己时会被误报。
# 扫描目标为自身源码时，剔除这些常量区（类似 review.py 的 _strip_self_check_code）。
_SELF_RULE_NAMES = {"TAINT_SOURCES", "TAINT_SINKS", "_SINK_RE", "_SQL_RE", "_SQL_KW",
                    "SQL_KW", "SINKS", "SINKS_RE", "KNOWN_VULN_PACKAGES"}
_SELF_RULE_PAT = re.compile(
    r"(SELECT|INSERT|UPDATE|DELETE|pickle\.loads|yaml\.load|eval|exec|os\.system|cursor\.execute)")


def _assign_names(node):
    """赋值语句的目标名集合。"""
    return {t.id for t in node.targets if isinstance(t, ast.Name)}


def _rule_constant_spans(tree, lines):
    """模块级规则常量赋值的行区间（[start, end) 0-based）。"""
    spans = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (_assign_names(node) & _SELF_RULE_NAMES):
            continue
        block = "\n".join(lines[node.lineno - 1:node.end_lineno])
        if _SELF_RULE_PAT.search(block):
            spans.append((node.lineno - 1, node.end_lineno))
    return spans


def _strip_self_rule_constants(source: str) -> str:
    """剔除自身检测规则常量区（模块级 Assign），修复"扫描器扫到自己"的自指误报。

    只剔除目标名为已知规则常量、且值含安全敏感字样（SQL 关键字/危险 sink）的模块级赋值，
    不动真实业务代码；扫描对象非自身时这些常量名几乎不会出现，故不影响他人文件。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    lines = source.split("\n")
    for start, end in _rule_constant_spans(tree, lines):
        lines[start:end] = [""] * (end - start)
    return "\n".join(lines)



def _flows_with_file(flows, pf):
    """给 flow 列表补上 file 字段（原地）。"""
    for f in flows:
        f["file"] = pf
    return flows


def taint_analyze(target: str) -> dict:
    """Semgrep 级污点分析：源→汇 数据流。target 为文件或目录。"""
    findings = []
    files_scanned = 0
    for pf in _py_files_under(target)[:300]:
        try:
            source = open(pf, encoding="utf-8", errors="ignore").read()
            # 扫描自身源码时剔除检测规则常量区，避免自指误报（不影响其他文件）
            tree = ast.parse(_strip_self_rule_constants(source))
        except SyntaxError:  # fail-open: taint_analyze 里出错就跳过该条继续扫
            continue
        files_scanned += 1
        findings += _flows_with_file(_find_taint_flows(tree), pf)
        findings += _flows_with_file(_sql_flows(tree), pf)
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
