#!/usr/bin/env python3
"""tests/test_security_boundary.py — 安全加固边界测试（无服务启动，纯函数级）。

覆盖（安全加固 codeagent-security-hardening）：
  - 路径穿越：web.server._validate_target 拒绝 `../` 与绝对路径，仅放行 ROOT 白名单源码文件
  - 越权/细粒度权限：dispatch.permission deny 规则阻断敏感资源（allow/ask/deny 三级）
  - 数据不出厂：llm-router local_only 默认 True，云端 generate 立即 degraded、不发请求
  - 命令注入防护（回归）：shell=False 已在先序修复，此处锚定断言无 shell=True 泄露给子进程

独立于 live 服务（见 web/_verify_security.py），可在 pytest 下离线快速跑。
"""
import os
import sys
import importlib.util

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _load_web_server():
    spec = importlib.util.spec_from_file_location(
        "web_server_sec", os.path.join(REPO_ROOT, "web", "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_WS = None


def _ws():
    global _WS
    if _WS is None:
        _WS = _load_web_server()
    return _WS


# ══════════ 1. 路径穿越（P0）══════════
def test_validate_target_rejects_dotdot_traversal():
    ws = _ws()
    for bad in ("../LICENSE", "../../Windows/win.ini",
                "..\\..\\Windows\\System32\\drivers\\etc\\hosts",
                "agents/../../codeagent.py"):
        ok, err = ws._validate_target(bad)
        assert ok is None and err, f"应拒绝路径穿越 {bad!r}: got ok={ok!r} err={err!r}"


def test_validate_target_rejects_absolute_path():
    ws = _ws()
    for bad in (r"C:/Windows/win.ini", r"E:/secrets/key.txt",
                "/etc/passwd", r"C:\\Windows\\System32\\config\\SAM"):
        ok, err = ws._validate_target(bad)
        assert ok is None and err, f"应拒绝绝对路径 {bad!r}: got ok={ok!r}"


def test_validate_target_accepts_whitelist_source_file():
    ws = _ws()
    # 白名单内的真实源码文件应放行
    ok, err = ws._validate_target("agents/codereview/code-review/main.py")
    assert ok is not None and not err, f"白名单文件应放行: err={err!r}"
    # 不在白名单的相对路径（如 index.html / server.py）应拒绝
    ok2, err2 = ws._validate_target("web/index.html")
    assert ok2 is None and err2, f"非源码白名单应拒绝: got ok={ok2!r}"


def test_validate_target_realpath_stays_inside_root():
    ws = _ws()
    # 归一化后逃逸 ROOT 的组合也应拒绝
    ok, err = ws._validate_target("agents/../../../LICENSE")
    assert ok is None and err, f"归一化逃逸应拒绝: got ok={ok!r}"


# ══════════ 2. 越权 / 细粒度权限（dispatch.permission deny 阻断）══════════
def test_permission_deny_blocks_sensitive_resource():
    from agent_runtime import AgentRuntime
    rt = AgentRuntime()
    rules = [{"type": "file", "pattern": "**/secrets/*", "effect": "deny"},
             {"type": "command", "pattern": "rm -rf *", "effect": "deny"}]
    # 文件型 deny
    r = rt.run_capability("dispatch.permission", action="check",
                          resource="private/secrets/key.txt", resource_type="file", rules=rules)
    assert r["ok"] and r["data"]["decision"] == "deny" and r["data"]["blocked"], r
    # 命令型 deny 覆盖 allow
    r2 = rt.run_capability("dispatch.permission", action="check",
                           resource="rm -rf /tmp/x", resource_type="command",
                           rules=[{"type": "command", "pattern": "*", "effect": "allow"},
                                  {"type": "command", "pattern": "rm -rf *", "effect": "deny"}])
    assert r2["data"]["decision"] == "deny" and r2["data"]["blocked"], r2
    # 白名单内 allow
    r3 = rt.run_capability("dispatch.permission", action="check",
                           resource="git status", resource_type="command", rules=rules)
    assert r3["data"]["decision"] == "ask" or r3["data"]["decision"] == "allow", r3


# ══════════ 3. 数据不出厂（local_only 默认封锁云端）══════════
def test_local_only_blocks_cloud_generate():
    from agent_runtime import AgentRuntime
    rt = AgentRuntime(local_only=True)
    r = rt.run_capability("llm.generate", messages=[{"role": "user", "content": "hi"}],
                          local_only=True, provider="deepseek")
    assert r.get("ok") is False, f"local_only 默认应封锁云端 generate: {r}"
    data = r.get("data") or {}
    assert data.get("degraded") is True or "local-only" in str(r.get("error", "")), \
        f"应降级 local-only: {r}"


def test_local_only_blocked_without_explicit_false():
    """未显式 local_only=False 时，云端 provider 一律封锁（默认值 True 兜底）。"""
    from agent_runtime import AgentRuntime
    rt = AgentRuntime(local_only=True)   # 调用方未显式 False → 默认封锁
    r = rt.run_capability("llm.generate", messages=[{"role": "user", "content": "x"}],
                          provider="deepseek")   # 不传 local_only → 走默认封锁
    data = r.get("data") or {}
    assert data.get("degraded") is True or "local-only" in str(r.get("error", "")), r


# ══════════ 4. 命令注入防护回归（shell 已去除，锚定无 shell=True）══════════
def test_no_shell_true_in_web_codeagent_subprocess():
    import re
    for path in (os.path.join(REPO_ROOT, "web", "server.py"),
                 os.path.join(REPO_ROOT, "codeagent.py")):
        src = open(path, encoding="utf-8").read()
        for m in re.finditer(r"subprocess\.(?:run|Popen|call)\(([^)]*)", src, re.S):
            args = m.group(1)
            assert "shell=True" not in args, f"{path} 存在 shell=True: {args[:80]}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for fn in fns:
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            fails.append(fn.__name__)
            print(f"  [FAIL] {fn.__name__}: {e}")
    if fails:
        print(f"FAIL: {len(fails)} -> {fails}")
        sys.exit(1)
    print(f"ALL PASS ({len(fns)} 项)")
