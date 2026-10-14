import importlib.util
import os
import sys
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = os.path.join(_ROOT, "agents", "llm", "llm-router", "main.py")

_spec = importlib.util.spec_from_file_location("llm_router_main", _MAIN)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _set_env():
    old = {k: os.environ.get(k) for k in ("CODEAGENT_HTTP_RETRIES", "CODEAGENT_HTTP_BACKOFF")}
    os.environ["CODEAGENT_HTTP_RETRIES"] = "2"
    os.environ["CODEAGENT_HTTP_BACKOFF"] = "0"
    return old


def _restore_env(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _run(stub):
    calls = {"n": 0}

    def fake_urlopen(*a, **kw):
        calls["n"] += 1
        return stub(calls["n"])

    old_env = _set_env()
    old_open = m.urllib.request.urlopen
    m.urllib.request.urlopen = fake_urlopen
    try:
        try:
            result = m._post_json("http://x", {})
            err = None
        except Exception as e:
            result = None
            err = e
    finally:
        m.urllib.request.urlopen = old_open
        _restore_env(old_env)
    return result, err, calls["n"]


def test_retries_on_5xx():
    def stub(n):
        raise m.urllib.error.HTTPError("http://x", 500, "boom", {}, None)

    result, err, n = _run(stub)
    assert err is not None
    assert n == 3


def test_no_retry_on_400():
    def stub(n):
        raise m.urllib.error.HTTPError("http://x", 400, "bad", {}, None)

    result, err, n = _run(stub)
    assert err is not None
    assert n == 1


def test_retries_on_429():
    def stub(n):
        raise m.urllib.error.HTTPError("http://x", 429, "slow", {}, None)

    result, err, n = _run(stub)
    assert err is not None
    assert n == 3


def test_success_after_one_failure():
    def stub(n):
        if n == 1:
            raise m.urllib.error.HTTPError("http://x", 500, "boom", {}, None)
        return _FakeResponse(b'{"ok": true}')

    result, err, n = _run(stub)
    assert err is None
    assert result == {"ok": True}
    assert n == 2


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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