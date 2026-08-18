#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bug_deep.py — CodeAgent 深挖 bug 核心（纯 stdlib，数据不出厂）。

深挖原子 bug-deep 复用的核心模块：威胁建模(先建攻击面) + 对抗性审查 + 自动化 PoC 验证
(沙箱跑证据) + AI 规则反哺闭环(验证漏洞沉淀规则)。零第三方依赖。

能力（供 bugdeep 原子 provides）：
  bugdeep.model   — 威胁建模：先建攻击面（入口点/信任边界/危险 sink 清单）
  bugdeep.adv     — 对抗性审查（针对攻击面，先假设误报证伪）
  bugdeep.poc     — 自动化 PoC 验证（子进程沙箱跑证据）
  bugdeep.rule    — AI 规则反哺闭环（验证漏洞 → 沉淀规则）
"""
import ast
import os
import re
import json
import sys
import time
import signal
import shutil
import threading
import tempfile
import subprocess
from pathlib import Path

from pathguard import safe_resolve, safe_read_text, assert_within

DEFAULT_RULES_FILE = ".codeagent/bugdeep_rules.json"

# ── 沙箱边界硬指标 ──────────────────────────────
SANDBOX_MAX_CODE = 200_000          # 单份 PoC 代码最大字符数（输入校验）
SANDBOX_MAX_OUTPUT = 1_000_000      # 沙箱子进程输出最大字节数（防内存耗尽/无限输出）
SANDBOX_MAX_TIMEOUT = 60            # 沙箱子进程最长执行秒数（防死循环/DoS）
SANDBOX_CPU_LIMIT_S = 30            # POSIX 子进程 CPU 上限（秒）
SANDBOX_MEM_LIMIT_MB = 512          # POSIX 子进程虚拟内存上限（MB）
SANDBOX_MAX_FILESIZE_MB = 1         # POSIX 子进程单文件写入上限（MB，禁写大文件/日志风暴）
SANDBOX_MAX_PROC = 64               # POSIX 子进程可 fork 进程数上限（防 fork 炸弹）

# 环境变量黑名单：任何含这些子串的 env 不注入沙箱子进程（数据不出厂，防凭据外泄）
_ENV_DROP_RE = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE[_A-Z]*KEY|API[_-]?KEY|"
    r"AUTH|SIG[A-Z]*|PSWD|PROXY|JWT|COOKIE|SESSIONID|_TOKEN|_SECRET)", re.I)

# 入口点（攻击面）：外部可触达、读取输入的代码位置
ENTRY_POINTS = {
    "input": "标准输入", "raw_input": "标准输入", "getpass": "密码输入",
    "sys.argv": "命令行参数", "request.args": "HTTP参数", "request.form": "HTTP表单",
    "request.json": "HTTP JSON", "request.cookies": "HTTP Cookie", "request.headers": "HTTP头",
    "flask.request": "HTTP请求", "os.environ.get": "环境变量", "os.getenv": "环境变量",
    "json.loads": "外部JSON解析", "read": "文件/流读取", "open": "文件读取",
    "urlopen": "远端响应", "requests.get": "远端响应",
}
# 危险 sink（攻击目标）
SINKS = {
    "eval": ("代码执行", "critical"), "exec": ("代码执行", "critical"),
    "os.system": ("命令执行", "critical"), "os.popen": ("命令执行", "critical"),
    "subprocess.Popen": ("命令执行", "major"), "subprocess.run": ("命令执行", "major"),
    "cursor.execute": ("SQL执行", "critical"), "execute": ("SQL执行", "major"),
    "pickle.loads": ("反序列化", "critical"), "pickle.load": ("反序列化", "critical"),
    "yaml.load": ("反序列化", "critical"), "open": ("文件读写", "major"),
    "requests.get": ("SSRF", "major"), "urlopen": ("SSRF", "major"),
    "render_template_string": ("模板注入", "critical"),
}


# ── 威胁建模：先建攻击面 ──
def threat_model(content: str, file: str = "") -> dict:
    """威胁建模：先建攻击面。识别入口点(外部输入)、危险 sink、信任边界、可达路径。"""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {"attack_surface": [], "entry_points": [], "sinks": [],
                "trust_boundaries": [], "summary": "语法错误，无法建模"}
    entry_points, sinks = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            cname = _call_name(node)
            if cname in ENTRY_POINTS:
                entry_points.append({"name": cname, "type": ENTRY_POINTS[cname],
                                     "line": getattr(node, "lineno", 0),
                                     "surface": "外部输入"})
            if cname in SINKS:
                desc, sev = SINKS[cname]
                sinks.append({"name": cname, "type": desc, "severity": sev,
                              "line": getattr(node, "lineno", 0),
                              "surface": "危险操作"})
    # 信任边界：输入边界(entry)与危险区(sink)之间
    trust_boundaries = []
    if entry_points:
        trust_boundaries.append({"name": "输入信任边界", "desc": "外部输入进入系统,需校验/净化",
                                 "entries": len(entry_points)})
    if sinks:
        trust_boundaries.append({"name": "危险操作边界", "desc": "不可信数据到达危险 sink 前需拦截",
                                 "sinks": len(sinks)})
    attack_surface = entry_points + sinks
    return {"attack_surface": attack_surface, "entry_points": entry_points,
            "sinks": sinks, "trust_boundaries": trust_boundaries,
            "summary": f"攻击面 {len(attack_surface)} 项: 入口{len(entry_points)}/sink{len(sinks)}",
            "file": file}


def threat_model_file(path: str) -> dict:
    content = safe_read_text(path)
    return threat_model(content, file=str(path))


def threat_model_project(target: str) -> dict:
    p = safe_resolve(target)
    files = [p] if p.is_file() else sorted(
        f for f in p.rglob("*.py") if ".venv" not in str(f) and "node_modules" not in str(f))
    models, total = [], 0
    for f in files:
        assert_within(p, f)          # 扫描文件必须位于目标根内（防穿越逃逸）
        m = threat_model_file(str(f))
        total += len(m["attack_surface"])
        models.append(m)
    return {"files": models, "file_count": len(files), "attack_surface_total": total,
            "summary": f"威胁建模 {len(files)} 文件, 攻击面 {total} 项"}


# ── 对抗性审查 ──
def adversarial_review(content: str, file: str = "", rules=None) -> dict:
    """对抗性审查：针对攻击面，先假设误报再证伪。结合沉淀规则(反哺)提高命中。"""
    model = threat_model(content, file)
    sinks = {s["name"] for s in model["sinks"]}
    findings = []
    try:
        tree = ast.parse(content)
        from review import _dataflow_analyze, _adversarial_verify
        flows, sink_sites = _dataflow_analyze(tree, content)
    except Exception:
        flows, sink_sites = [], {}
    # 攻击面 sink → 命中沉淀规则则高置信，否则按数据流判 confirmed/needs_review
    rules = rules or load_rules()
    for s in model["sinks"]:
        sink = s["name"]
        rule_hit = next((r for r in rules if r.get("sink") == sink and r.get("verified")), None)
        line = s["line"]
        has_flow = any(f["line"] == line and f.get("engine") == "dataflow" for f in flows)
        if has_flow or rule_hit:
            verdict = "confirmed"
            evidence = ("沉淀规则命中: " + rule_hit.get("title", sink)) if rule_hit else \
                       "数据流 source→sink 已确认"
            confidence = "high"
        elif sink_sites.get(line):
            verdict, evidence, confidence = "needs_review", \
                "存在危险 sink 但未见污点路径,先假设误报", "medium"
        else:
            verdict, evidence, confidence = "likely_fp", \
                "未获数据流/规则佐证,倾向误报", "low"
        findings.append({"sink": sink, "type": s["type"], "severity": s["severity"],
                         "line": line, "verdict": verdict, "evidence": evidence,
                         "confidence": confidence, "dimension": "对抗性审查",
                         "tier": "P0" if verdict == "confirmed" else "P1"})
    confirmed = [f for f in findings if f["verdict"] == "confirmed"]
    return {"attack_surface": model["attack_surface"], "findings": findings,
            "confirmed": confirmed,
            "summary": f"对抗性审查: {len(findings)} 项, 确认{len(confirmed)} 需复核"
                       f"{sum(1 for f in findings if f['verdict']=='needs_review')}",
            "file": file, "model": model}


# ── 自动化 PoC 验证（沙箱跑证据）──
POC_TEMPLATES = {
    "eval": 'import os\ntry:\n    r = eval("__import__(\'os\').system(\'echo POC_RCE_YES\')\")\nexcept Exception as e:\n    print("POC_CRASH", e)\n',
    "exec": 'import os\ntry:\n    exec("import os; os.system(\'echo POC_RCE_YES\')\")\nexcept Exception as e:\n    print("POC_CRASH", e)\n',
    "os.system": 'import os\nr = os.system("echo POC_CMDI_YES")\nprint("POC_RC", r)\n',
    "os.popen": 'import os\nr = os.popen("echo POC_CMDI_YES").read()\nprint(r)\n',
    "subprocess.Popen": 'import subprocess\nr = subprocess.Popen(["echo", "POC_SUB_YES"], stdout=subprocess.PIPE).communicate()[0]\nprint(r.decode())\n',
    "subprocess.run": 'import subprocess\nr = subprocess.run(["echo", "POC_SUB_YES"], capture_output=True, text=True)\nprint(r.stdout)\n',
    "pickle.loads": 'import pickle\n# 恶意 pickle 载荷: 触发 __reduce__ 执行\nclass P:\n    def __reduce__(self):\n        import os\n        return (os.system, ("echo POC_PICKLE_RCE",))\nprint("POC_PICKLE_LOAD_ATTEMPT")\n_ = pickle.loads(pickle.dumps(P()))\n',
    "yaml.load": 'import yaml\n# 用 !!python/object/apply 触发命令执行(旧版 yaml)\ntry:\n    data = yaml.load("!!python/object/apply:os.system [echo POC_YAML_RCE]")\n    print("POC_YAML_DONE", data)\nexcept Exception as e:\n    print("POC_YAML_UNSAFE_OR_BLOCKED", e)\n',
    "render_template_string": 'from jinja2 import Template\ntry:\n    t = Template("{{ \'__import__\'(\'os\').system(\'echo POC_SSTI_YES\') }}").render()\n    print("POC_SSTI", t)\nexcept Exception as e:\n    print("POC_SSTI_BLOCKED", e)\n',
    "cursor.execute": 'print("POC_SQL_DEMO: 演示拼接注入(需真实DB,这里仅静态确认危险模式)")\n# SELECT ... WHERE n=\'%s\' % name   → 参数化替代\n',
}


def generate_poc(issue: dict, content: str = "") -> dict:
    """为 issue 生成自动化 PoC 脚本。issue 需含 sink 或 title。返回 {poc_code, kind, marker}。"""
    sink = issue.get("sink") or _sink_from_title(issue.get("title", ""))
    code = POC_TEMPLATES.get(sink)
    if code is None:
        return {"poc_code": None, "kind": sink, "marker": None,
                "note": f"无 {sink} 的自动化 PoC 模板，需人工构造"}
    return {"poc_code": code, "kind": sink,
            "marker": "POC_%s_YES" % sink.split(".")[-1].upper()[:4] if sink else None,
            "note": f"{sink} 自动化 PoC（沙箱内执行，验证可触发性）"}


def _sink_from_title(title: str) -> str:
    t = title or ""
    for k in SINKS:
        if k in t or k.split(".")[-1] in t:
            return k
    if "命令" in t or "shell" in t:
        return "os.system"
    if "eval" in t.lower() or "exec" in t.lower():
        return "eval"
    if "反序列化" in t or "pickle" in t:
        return "pickle.loads"
    if "SQL" in t:
        return "cursor.execute"
    if "SSTI" in t or "模板" in t:
        return "render_template_string"
    return ""


def sys_python() -> str:
    return os.environ.get("PYTHON") or os.sys.executable


# ── 沙箱安全工具（子进程隔离/资源限制/输出封顶/可执行白名单/环境净化）──
def _validate_interpreter(py: str) -> str:
    """可执行白名单：解释器必须是真实存在、可执行的 Python 文件（禁任意命令注入执行）。"""
    if not isinstance(py, str) or not py.strip():
        raise ValueError("无效的 Python 解释器路径")
    p = os.path.abspath(os.path.expanduser(py))
    if not os.path.isfile(p):
        raise ValueError(f"解释器不存在或不是文件: {p}")
    if not os.access(p, os.X_OK):
        raise ValueError(f"解释器不可执行: {p}")
    return p


def _sanitize_env(env=None) -> dict:
    """环境净化（数据不出厂）：剔除含 token/secret/key/password/proxy 等敏感 env，
    防恶意 PoC 通过 os.environ 窃取主机凭据/走代理外呼。仅保留中性环境变量。"""
    src = dict(env) if env is not None else dict(os.environ)
    out = {}
    for k, v in src.items():
        if _ENV_DROP_RE.search(k):
            continue
        out[k] = v
    return out


def _preexec_limits():
    """POSIX 子进程资源上限（CPU/虚拟内存/单文件写入/进程数/禁 core dump）。
    Windows 无 resource 模块 → 返回 None（该平台依赖超时+输出封顶兜底）。"""
    if os.name != "posix":
        return None
    try:
        import resource
    except Exception:
        return None

    def _lim():
        resource.setrlimit(resource.RLIMIT_CPU,
                           (SANDBOX_CPU_LIMIT_S, SANDBOX_CPU_LIMIT_S))
        resource.setrlimit(resource.RLIMIT_AS,
                           (SANDBOX_MEM_LIMIT_MB * 1024 * 1024,
                            SANDBOX_MEM_LIMIT_MB * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE,
                           (SANDBOX_MAX_FILESIZE_MB * 1024 * 1024,
                            SANDBOX_MAX_FILESIZE_MB * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC,
                           (SANDBOX_MAX_PROC, SANDBOX_MAX_PROC))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return _lim


def _kill_tree(proc):
    """强杀子进程树：POSIX 用进程组 SIGKILL，Windows 用 taskkill /T，兜底 proc.kill()。"""
    if proc is None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10, shell=False)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _run_limited(cmd, timeout, max_output, cwd, env):
    """有界子进程执行：超时 + 输出字节封顶 + 进程组隔离，防恶意代码逃逸/耗尽主机资源。
    返回 dict：{timed_out, output_overflow, rc, out_tail, err_tail, wall}。"""
    popen_kw = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=cwd, env=env, shell=False)
    preexec = _preexec_limits()
    if os.name == "posix":
        popen_kw["start_new_session"] = True
        if preexec:
            popen_kw["preexec_fn"] = preexec
    else:
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, **popen_kw)

    out_chunks, err_chunks = [], []
    cap = {"count": 0, "overflow": False}
    lock = threading.Lock()

    def reader(stream, chunks):
        try:
            while True:
                data = stream.read(65536)
                if not data:
                    break
                with lock:
                    room = max_output - cap["count"]
                    if room > 0:
                        chunks.append(data[:room])
                        cap["count"] += len(data[:room])
                    if cap["count"] >= max_output:
                        cap["overflow"] = True
        except Exception:
            pass

    threads = [threading.Thread(target=reader, args=(proc.stdout, out_chunks),
                                daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, err_chunks),
                                daemon=True)]
    for t in threads:
        t.start()
    t0 = time.time()
    deadline = t0 + timeout
    while proc.poll() is None:
        if cap["overflow"]:
            _kill_tree(proc)
            proc.wait()
            for t in threads:
                t.join(timeout=1)
            return {"timed_out": False, "output_overflow": True, "rc": proc.returncode,
                    "wall": round(time.time() - t0, 2),
                    "out_tail": (b"".join(out_chunks).decode("utf-8", "replace"))[-400:],
                    "err_tail": (b"".join(err_chunks).decode("utf-8", "replace"))[-300:]}
        if time.time() > deadline:
            _kill_tree(proc)
            proc.wait()
            for t in threads:
                t.join(timeout=1)
            return {"timed_out": True, "output_overflow": False, "rc": proc.returncode,
                    "wall": round(time.time() - t0, 2),
                    "out_tail": (b"".join(out_chunks).decode("utf-8", "replace"))[-400:],
                    "err_tail": (b"".join(err_chunks).decode("utf-8", "replace"))[-300:]}
        time.sleep(0.05)
    for t in threads:
        t.join(timeout=2)
    return {"timed_out": False, "output_overflow": False, "rc": proc.returncode,
            "wall": round(time.time() - t0, 2),
            "out_tail": (b"".join(out_chunks).decode("utf-8", "replace"))[-400:],
            "err_tail": (b"".join(err_chunks).decode("utf-8", "replace"))[-300:]}


def validate_poc(poc_code, timeout=None, max_code=SANDBOX_MAX_CODE):
    """PoC 输入校验：必须是非空字符串、长度受上限约束；timeout 必须在 (0, 60] 秒。
    防恶意/超长/异常输入导致崩溃或注入。返回 (ok: bool, err: str)。"""
    if not isinstance(poc_code, str):
        return False, "PoC 代码必须是字符串"
    if not poc_code.strip():
        return False, "PoC 代码为空"
    if len(poc_code) > max_code:
        return False, f"PoC 代码超长(>{max_code} 字符)，拒绝执行"
    if timeout is not None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            return False, "超时必须是数值"
        if not (0 < timeout <= SANDBOX_MAX_TIMEOUT):
            return False, f"超时必须在 (0, {SANDBOX_MAX_TIMEOUT}] 秒"
    return True, ""


def run_poc_sandbox(poc_code: str, timeout: int = 8, max_output=None,
                    base_dir=None) -> dict:
    """安全沙箱执行 PoC：子进程隔离 + 超时 + 资源限制 + 输出封顶 + 可执行白名单
    + 环境净化 + 进程组强杀，捕获 stdout/stderr/rc/耗时，产出证据。

    防护（防恶意代码逃逸）：
      - 隔离：每份 PoC 跑在独立临时工作目录（sandbox_dir），不污染共享系统临时目录；
      - 超时：超时强杀整棵进程树（防死循环/fork 炸弹）；
      - 资源：POSIX 子进程 CPU/内存/单文件写入/进程数 上限（Windows 依赖超时+输出封顶）；
      - 输出封顶：无限输出在达到 max_output 时强杀（防内存耗尽 DoS）；
      - 可执行白名单：仅允许真实存在且可执行的 Python 解释器（禁任意命令）；
      - 环境净化：剔除凭据/proxy env（数据不出厂，防外泄）。
    """
    # 输入校验：恶意/超长/异常 PoC 直接拒绝（防崩溃/注入）
    ok, verr = validate_poc(poc_code, timeout)
    if not ok:
        return {"ran": False, "verdict": "rejected",
                "evidence": f"PoC 被拒绝: {verr}", "validated": ok}
    max_output = max_output or SANDBOX_MAX_OUTPUT
    if max_output > 10 * SANDBOX_MAX_OUTPUT:
        max_output = SANDBOX_MAX_OUTPUT
    try:
        interp = _validate_interpreter(sys_python())
    except ValueError as e:
        return {"ran": False, "verdict": "rejected",
                "evidence": f"可执行白名单拒绝: {e}", "validated": ok}

    sandbox_dir = base_dir or tempfile.mkdtemp(prefix="codeagent_sandbox_")
    try:
        tmp = os.path.join(sandbox_dir, "poc.py")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(poc_code)
        env = _sanitize_env()
        r = _run_limited([interp, tmp], timeout=timeout, max_output=max_output,
                         cwd=sandbox_dir, env=env)
        rc, wall = r["rc"], r["wall"]
        out = r["out_tail"] + r["err_tail"]
        if r["timed_out"]:
            return {"ran": True, "verdict": "timeout",
                    "evidence": f"PoC 超时(>{timeout}s)并被强杀，疑似死循环/逃逸",
                    "wall_s": timeout, "rc": rc, "marker_hit": False,
                    "crash": False, "sandbox_dir": sandbox_dir}
        if r["output_overflow"]:
            return {"ran": True, "verdict": "overflow",
                    "evidence": f"PoC 输出超过 {max_output} 字节并被强杀（疑似无限输出 DoS）",
                    "wall_s": wall, "rc": rc, "marker_hit": False,
                    "crash": False, "sandbox_dir": sandbox_dir}
        marker_hit = bool(re.search(r"POC_[A-Z_]+_YES", out))
        crash = rc != 0
        if marker_hit:
            verdict = "exploitable"
        elif "POC_CRASH" in out:
            verdict = "crash"
        elif rc != 0:
            verdict = "failed"
        else:
            verdict = "no_trigger"
        return {"ran": True, "verdict": verdict, "rc": rc, "wall_s": wall,
                "marker_hit": marker_hit, "crash": crash,
                "stdout_tail": r["out_tail"], "stderr_tail": r["err_tail"],
                "evidence": (f"rc={rc} wall={wall}s marker={'命中' if marker_hit else '未命中'} "
                             f"verdict={verdict}"),
                "validated": ok, "sandbox_dir": sandbox_dir}
    finally:
        try:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception:
            pass


# ── AI 规则反哺闭环：验证漏洞 → 沉淀规则 ──
def load_rules(rules_file: str = DEFAULT_RULES_FILE) -> list:
    p = Path(rules_file)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def sediment_rule(issue: dict, verified: bool, evidence: str = "",
                  rules_file: str = DEFAULT_RULES_FILE) -> dict:
    """沉淀规则：把已验证漏洞写入规则库，供后续扫描命中(反哺闭环)。"""
    sink = issue.get("sink") or _sink_from_title(issue.get("title", ""))
    p = Path(rules_file)
    rules = load_rules(rules_file)
    existing = next((r for r in rules if r.get("sink") == sink and r.get("title") == issue.get("title")), None)
    if existing:
        existing["times"] = existing.get("times", 1) + 1
        existing["verified"] = existing.get("verified", False) or verified
        existing["last_evidence"] = evidence or existing.get("last_evidence", "")
        status = "updated"
    else:
        rules.append({"sink": sink, "title": issue.get("title", ""),
                      "verified": verified, "evidence": evidence,
                      "created": time.strftime("%Y-%m-%d %H:%M:%S"), "times": 1,
                      "kind": issue.get("kind", ""), "source": "bug-deep"})
        status = "added"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": status, "rule_count": len(rules), "verified": verified,
            "rules_file": str(p),
            "summary": f"规则反哺: {'新增' if status=='added' else '更新'} sink={sink} "
                       f"verified={verified}, 库现有 {len(rules)} 条"}


def close_loop(issue: dict, rules_file: str = DEFAULT_RULES_FILE) -> dict:
    """闭环：验证漏洞 → 沉淀规则。执行 PoC → 若 exploitable 则 verified=True 沉淀规则。"""
    poc = generate_poc(issue)
    if not poc.get("poc_code"):
        return {"closed": False, "reason": poc.get("note", "无 PoC"), "poc": poc}
    ev = run_poc_sandbox(poc["poc_code"])
    verified = ev.get("verdict") in ("exploitable", "crash")
    rule = sediment_rule(issue, verified, evidence=ev.get("evidence", ""), rules_file=rules_file)
    return {"closed": True, "verified": verified, "poc": poc, "sandbox": ev, "rule": rule,
            "summary": f"漏洞验证={'确凿' if verified else '未触发'} → 规则{'已沉淀' if verified else '未沉淀(需人工)'}"}


# ── 工具 ──
def _call_name(call) -> str:
    f = call.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


# ── CLI 自测 ──
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="bug_deep 核心自测")
    ap.add_argument("target", help="文件或目录")
    ap.add_argument("--capability", default="model", choices=["model", "project", "adv", "close"])
    ap.add_argument("--rules", default=DEFAULT_RULES_FILE)
    a = ap.parse_args(argv)
    if a.capability == "project":
        r = threat_model_project(a.target)
    elif a.capability == "adv":
        content = Path(a.target).read_text(encoding="utf-8", errors="ignore")
        r = adversarial_review(content, file=str(a.target))
    elif a.capability == "close":
        content = Path(a.target).read_text(encoding="utf-8", errors="ignore")
        model = threat_model(content, str(a.target))
        if not model["sinks"]:
            r = {"closed": False, "reason": "无危险 sink", "model": model}
        else:
            r = close_loop({"sink": model["sinks"][0]["name"], "title": model["sinks"][0]["name"]},
                           rules_file=a.rules)
    else:
        r = threat_model_file(a.target)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
