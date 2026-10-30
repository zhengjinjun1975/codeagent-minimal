"""发布前密钥扫描的 pytest 入口。

脚本本身能独立跑，这里再包一层，让 `pytest tests/` 也覆盖它。
开源件不许带着真实密钥发出去，这条要自动执行，不能只写在文档里。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "verify_no_secrets.py")


def test_no_real_secrets():
    r = subprocess.run([sys.executable, SCRIPT], cwd=ROOT,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, "发现疑似真实密钥：\n" + (r.stdout or "") + (r.stderr or "")


def test_scanner_detects_real_key():
    """扫描器必须能真的抓到密钥，否则它只是一句话。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import verify_no_secrets as v

    assert v.judge("sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6") == "critical"
    assert v.judge("sk-test-key-placeholder") == "fixture"
    assert v.judge("sk-abc...7890") == "fixture"


def test_scanner_ignores_normal_identifiers():
    """ask-input、task-state 这类普通标识符不能被当成密钥。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import verify_no_secrets as v

    hits = []
    v.scan_text('<input id="ask-input"><span id="task-state">', "probe", hits)
    assert hits == [], hits
