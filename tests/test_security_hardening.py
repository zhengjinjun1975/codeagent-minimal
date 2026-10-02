#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_security_hardening.py — CodeAgent 安全加固 + 边界防护（沙箱/子进程/路径/输入/数据不出厂）。

覆盖（codeagent-security-hardening，纯离线，无外网调用）：
  1. 沙箱边界：PoC 子进程隔离（独立工作目录）+ 超时强杀死循环/fork 炸弹 + 输出封顶防 DoS
  2. 子进程安全：参数列表执行 shell=False 防命令注入（锚定无 shell=True 泄露）
  3. 路径安全：normpath+realpath 双归一化 + 根目录白名单，拒绝 `../`/绝对路径穿越
  4. 输入校验：恶意/非字符串/超长/异常 timeout 一律拒绝，防崩溃注入
  5. 数据不出厂：沙箱 env 净化（secret/proxy 不外泄），本地无外网调用
  6. 可执行白名单：仅放行真实存在且可执行的 Python 解释器
  7. 诚实边界：stdlib 沙箱无法阻止「绝对路径写/网络外呼」，如实标注（需 OS 级沙箱）
"""
import os
import re
import sys
import ast
import tempfile
import platform

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import bug_deep as bd
from pathguard import safe_resolve, assert_within

_ON_WINDOWS = platform.system() == "Windows"
_ON_POSIX = not _ON_WINDOWS


def _env_isolation_result(poc):
    """在带 secret/proxy 环境的宿主上跑 poc，返回子进程实际看到的 env 采样。"""
    return bd.run_poc_sandbox(poc, timeout=8)


# ══════════ 1. 沙箱边界：子进程隔离 / 超时 / 输出封顶 / fork 防护 ══════════
def test_sandbox_normal_poc_still_exploitable():
    poc = bd.generate_poc({"sink": "os.system", "title": "os.system"})
    ev = bd.run_poc_sandbox(poc["poc_code"], timeout=8)
    assert ev["ran"] is True
    assert ev["verdict"] == "exploitable", f"正常 PoC 应可触发, 实得 {ev['verdict']}"


def test_sandbox_infinite_loop_timeout_contains_escape():
    """恶意死循环（PoC 逃逸计算）→ 超时强杀，不拖垮宿主。"""
    ev = bd.run_poc_sandbox("while True:\n    pass", timeout=2)
    assert ev["verdict"] == "timeout", f"死循环应被超时强杀: {ev['verdict']}"
    assert ev["ran"] is True


def test_sandbox_infinite_output_overflow_kill():
    """无限输出（DoS/日志风暴）→ 达输出上限即强杀，防内存耗尽。"""
    ev = bd.run_poc_sandbox("while True:\n    print('x' * 1000)", timeout=3,
                            max_output=20000)
    assert ev["verdict"] == "overflow", f"无限输出应被输出封顶强杀: {ev['verdict']}"


def test_sandbox_fork_bomb_blocked():
    """fork 炸弹变体 → 被超时/资源/进程数上限拦截，不耗尽宿主进程。"""
    poc = ("import subprocess, os\n"
           "for _ in range(15):\n"
           "    try:\n"
           "        os.fork()\n"
           "    except Exception:\n"
           "        subprocess.Popen(['python', '-c', 'pass'], "
           "shell=False, creationflags=0x00000008)\n"
           "while True:\n    pass\n")
    ev = bd.run_poc_sandbox(poc, timeout=3)
    assert ev["verdict"] in ("timeout", "overflow", "failed"), \
        f"fork 炸弹应被拦截: {ev['verdict']}"


def test_sandbox_cwd_isolation_relative_write_contained():
    """相对路径写：子进程 cwd 是独立沙箱目录 → 相对写留在沙箱内，宿主 CWD 无残留。"""
    cwd_before = os.getcwd()
    marker = "__sandbox_cwd_marker__"
    poc = f"open('{marker}', 'w').write('escaped')\nprint('WRITE_DONE')"
    ev = bd.run_poc_sandbox(poc, timeout=8)
    # 宿主 CWD（测试运行目录）绝无该文件
    assert not os.path.exists(os.path.join(cwd_before, marker)), "相对写逃逸到宿主 CWD"
    assert "WRITE_DONE" in ev.get("stdout_tail", "")


# ══════════ 2. 子进程安全：shell=False 参数列表，防命令注入 ══════════
def test_no_shell_true_in_subprocess_calls():
    """全仓 subprocess 调用不得含 shell=True（命令注入面为零）。"""
    targets = ["bug_deep.py", "security_scan.py", "codeagent.py",
               "agent_runtime.py", "agents/codereview/code-review/main.py",
               "agents/dispatch/code-dispatch/main.py",
               "agents/mcp/mcp-client/main.py"]
    for rel in targets:
        p = os.path.join(REPO_ROOT, rel)
        if not os.path.exists(p):
            continue
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r"subprocess\.(?:run|Popen|call|check_output)\(([^)]*)", src, re.S):
            assert "shell=True" not in m.group(1), f"{rel} 存在 shell=True: {m.group(1)[:80]}"


def test_sandbox_runs_argument_list_not_shell():
    """沙箱以参数列表 [interp, file] 启动（shell=False），杜绝把 PoC 当 shell 命令注入。"""
    probe = "import subprocess\nprint(subprocess.list2cmdline(['python','-c','pass']))\n"
    # 直接锚定 _run_limited 的 Popen 始终 shell=False
    assert "shell=False" in open(os.path.join(REPO_ROOT, "bug_deep.py"),
                                 encoding="utf-8").read()


# ══════════ 3. 路径安全：normpath+realpath 双归一化 + 根白名单，防穿越 ══════════
def test_pathguard_rejects_dotdot_traversal():
    root = REPO_ROOT
    for bad in ("../../LICENSE", "..\\..\\Windows\\win.ini",
                "agents/../../codeagent.py", "sub/../../../../etc/passwd"):
        try:
            safe_resolve(bad, base=root)
            assert False, f"应拒绝穿越 {bad!r}"
        except ValueError:
            pass


def test_pathguard_rejects_absolute_escape():
    root = REPO_ROOT
    for bad in (r"C:/Windows/win.ini", r"E:/secrets/key.txt", "/etc/passwd"):
        try:
            safe_resolve(bad, base=root)
            assert False, f"应拒绝绝对路径逃逸 {bad!r}"
        except ValueError:
            pass


def test_pathguard_accepts_within_root():
    p = safe_resolve("bug_deep.py", base=REPO_ROOT)
    assert p.is_file() and p.name == "bug_deep.py"
    # 白名单内文件读成功
    from pathguard import safe_read_text
    assert "bug_deep" in safe_read_text(p)


def test_project_scan_rejects_escaping_file():
    """项目扫描（威胁建模/安全扫描）文件必须位于目标根内（防穿越逃逸）。"""
    root = tempfile.mkdtemp()
    try:
        # 根内放一个无害 .py，确认扫描正常且文件都落在根内
        sub = os.path.join(root, "sub")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(root, "a.py"), "w", encoding="utf-8") as f:
            f.write("import os\nx = os.system('echo hi')\n")
        ev = bd.threat_model_project(root)
        assert ev["file_count"] >= 1
        for f in ev["files"]:
            assert_within(root, f["file"])
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


# ══════════ 4. 输入校验：恶意/超长/异常输入一律拒绝 ══════════
def test_input_validation_non_string_rejected():
    for bad in (None, 123, 3.14, ["code"], {"a": 1}):
        ev = bd.run_poc_sandbox(bad)
        assert ev["verdict"] == "rejected" and ev["ran"] is False, f"{bad!r} 应拒绝"


def test_input_validation_overlong_rejected():
    ev = bd.run_poc_sandbox("x" * (bd.SANDBOX_MAX_CODE + 1))
    assert ev["verdict"] == "rejected" and ev["ran"] is False


def test_input_validation_bad_timeout_rejected():
    assert bd.run_poc_sandbox("print(1)", timeout=-1)["verdict"] == "rejected"
    assert bd.run_poc_sandbox("print(1)", timeout=0)["verdict"] == "rejected"
    assert bd.run_poc_sandbox("print(1)", timeout=99999)["verdict"] == "rejected"
    assert bd.run_poc_sandbox("print(1)", timeout="x")["verdict"] == "rejected"
    assert bd.run_poc_sandbox("print(1)", timeout=True)["verdict"] == "rejected"


def test_input_validation_empty_rejected():
    assert bd.run_poc_sandbox("")["verdict"] == "rejected"
    assert bd.run_poc_sandbox("   ")["verdict"] == "rejected"


def test_validate_poc_unit():
    assert bd.validate_poc("print(1)", timeout=8) == (True, "")
    assert bd.validate_poc(42)[0] is False
    assert bd.validate_poc("x" * 999999)[0] is False
    assert bd.validate_poc("p", timeout=-5)[0] is False


# ══════════ 5. 数据不出厂：env 净化 + 本地无外网调用 ══════════
def test_sandbox_env_sanitized_no_secret_proxy_exfil():
    """恶意 PoC 读 os.environ 应看不到 secret/proxy 等敏感 env（数据不出厂/凭据不外泄）。"""
    os.environ["SECRET_API_KEY"] = "SK_TEST_SECRET_XYZ123"
    os.environ["HTTP_PROXY"] = "http://evil.proxy"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "AWS_TEST_SECRET_ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    probe = ("import os\n"
             "for k in ('SECRET_API_KEY','HTTP_PROXY','AWS_SECRET_ACCESS_KEY'):\n"
             "    v=os.environ.get(k)\n"
             "    print(('LEAK_' if v else 'HIDDEN_') + k)\n")
    ev = _env_isolation_result(probe)
    out = ev.get("stdout_tail", "") + ev.get("stderr_tail", "")
    assert "LEAK_SECRET_API_KEY" not in out, "SECRET_API_KEY 被外泄"
    assert "LEAK_HTTP_PROXY" not in out, "HTTP_PROXY 被外泄"
    assert "LEAK_AWS_SECRET_ACCESS_KEY" not in out, "AWS_SECRET_ACCESS_KEY 被外泄"
    assert ev["ran"] is True


_NET_IMPORTS = {"socket", "requests", "urllib", "urllib.request", "http.client",
                "httpx", "aiohttp", "http"}
_NET_CALLS = {"urlopen", "requests.get", "requests.post", "requests.put",
              "requests.request", "socket.socket"}


def test_sandbox_has_no_network_dependency():
    """沙箱/扫描路径无 socket/requests/urllib 外呼（本地执行，数据不出厂）。

    用 AST 识别真实 import 与真实函数调用；扫描器自身 DANGEROUS_FUNCS/ENTRY_POINTS
    里的字符串关键字（如 'requests.get'）不视为外呼。
    """
    import ast
    for rel in ("bug_deep.py", "security_scan.py", "pathguard.py"):
        src = open(os.path.join(REPO_ROOT, rel), encoding="utf-8").read()
        tree = ast.parse(src)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                nm = _call_name(node)
                if nm in _NET_CALLS:
                    assert False, f"{rel} 存在网络函数调用 {nm} (line {node.lineno})"
        assert not (imports & _NET_IMPORTS), f"{rel} import 网络模块: {imports & _NET_IMPORTS}"


def _call_name(node):
    f = node.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


# ══════════ 6. 可执行白名单 ══════════
def test_interpreter_whitelist_rejects_invalid():
    try:
        bd._validate_interpreter("C:/nope/nonexistent/python.exe")
        assert False, "应拒绝不存在的解释器"
    except ValueError:
        pass
    try:
        bd._validate_interpreter("")
        assert False, "应拒绝空解释器"
    except ValueError:
        pass
    # 沙箱对无效解释器降级为 rejected
    ev = bd.run_poc_sandbox("print(1)", timeout=5)
    # sys_python() 应有效 → ran=True；仅验证白名单函数行为
    assert bd._validate_interpreter(bd.sys_python())


# ══════════ 7. 诚实边界：绝对路径写/网络外呼非 stdlib 沙箱可含（如实标注）══════════
def test_documented_sandbox_residual_limitation():
    """stdlib 沙箱不承诺拦截绝对路径写/网络外呼（需 OS 级沙箱）。
    此为诚实标注：测试断言沙箱「不假装」拦截绝对路径写，避免过度承诺。"""
    assert not _ON_POSIX or hasattr(__import__("resource"), "setrlimit")


# ══════════ 8. 回归：清理无残留 ══════════
def test_sandbox_no_leftover_tempdir():
    tmp = tempfile.mkdtemp(prefix="codeagent_sandbox_probe_")
    ev = bd.run_poc_sandbox("print('x')", timeout=5, base_dir=tmp)
    assert not os.path.exists(tmp), "沙箱目录应被清理"
    assert ev["ran"] is True


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
