import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import netguard


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_router():
    return _load(os.path.join(ROOT, "agents", "llm", "llm-router", "main.py"), "llm_router_main")


def _load_engine_llm():
    try:
        import requests  # noqa: F401
    except ImportError:
        print("跳过：requests 不可用")
        pytest.skip("requests 不可用")
    return _load(os.path.join(ROOT, "engine_llm.py"), "engine_llm")


def _set_env(mode, allow):
    old = {k: os.environ.get(k) for k in ("CODEAGENT_EGRESS_MODE", "CODEAGENT_EGRESS_ALLOW")}
    os.environ["CODEAGENT_EGRESS_MODE"] = mode
    if allow is None:
        os.environ.pop("CODEAGENT_EGRESS_ALLOW", None)
    else:
        os.environ["CODEAGENT_EGRESS_ALLOW"] = allow
    return old


def _restore_env(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_audit_allows_and_records():
    old = _set_env("audit", None)
    try:
        netguard.reset_egress_stats()
        r = netguard.egress_check("https://evil.example/x")
        assert r["allowed"] is True
        assert "审计" in r["reason"]
        assert netguard.egress_stats()["denied"] == 1
    finally:
        _restore_env(old)


def test_enforce_blocks_at_model_layer():
    old = _set_env("enforce", None)
    try:
        m = _load_router()
        assert m._egress_gate("https://evil.example/x")["allowed"] is False
        with pytest.raises(RuntimeError) as ei:
            m._post_json("https://evil.example/x", {"a": 1})
        assert "出网被拒" in str(ei.value)
    finally:
        _restore_env(old)


def test_allowlist_is_consumed():
    old = _set_env("enforce", "evil.example")
    try:
        m = _load_router()
        assert m._egress_gate("https://evil.example/x")["allowed"] is True
        assert "evil.example" in netguard.allowed_hosts()
    finally:
        _restore_env(old)


def test_engine_llm_retry_gated():
    if not os.path.isfile(os.path.join(ROOT, "engine_llm.py")):
        print("跳过：本仓无 engine_llm.py（开源副本不带该件）")
        return
    old = _set_env("enforce", None)
    try:
        el = _load_engine_llm()
        with pytest.raises(RuntimeError) as ei:
            el._post_retry("https://evil.example/x")
        assert "出网被拒" in str(ei.value)
    finally:
        _restore_env(old)


def test_local_still_allowed():
    old = _set_env("enforce", None)
    try:
        m = _load_router()
        assert m._egress_gate("http://127.0.0.1:8791/run")["allowed"] is True
    finally:
        _restore_env(old)


if __name__ == "__main__":
    tests = [
        test_audit_allows_and_records,
        test_enforce_blocks_at_model_layer,
        test_allowlist_is_consumed,
        test_engine_llm_retry_gated,
        test_local_still_allowed,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as e:
            print("FAIL", t.__name__, repr(e))
            failed.append(t.__name__)
    if failed:
        print("SELFCHECK FAIL:", failed)
        sys.exit(1)
    print("SELFCHECK PASS")
    sys.exit(0)