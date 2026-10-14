import os
import sys


def root_dir():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "agents",
        "code",
        "code-runloop",
    )


target = root_dir()
sys.path.insert(0, target)
if "tools" in sys.modules:
    del sys.modules["tools"]
import tools


def test_forward_slash_root_accepted():
    root = root_dir().replace("\\", "/")
    result = tools.ensure_inside(os.path.join(root, "tools.py"), [root])
    assert isinstance(result, str)


def test_backslash_root_accepted():
    root = root_dir()
    result = tools.ensure_inside(os.path.join(root, "tools.py"), [root])
    assert isinstance(result, str)


def test_uppercase_root_accepted():
    root = root_dir().upper()
    result = tools.ensure_inside(os.path.join(root, "tools.py"), [root])
    assert isinstance(result, str)


def test_nested_file_accepted():
    root = root_dir()
    result = tools.ensure_inside(os.path.join(root, "main.py"), [root])
    assert isinstance(result, str)


def test_sibling_prefix_rejected():
    root = root_dir()
    evil = root + "-evil" + os.sep + "x.py"
    try:
        tools.ensure_inside(evil, [root])
    except ValueError:
        return
    raise AssertionError("expected ValueError for sibling-prefix path")


def test_unrelated_root_rejected():
    root = root_dir()
    other = os.path.join(
        os.environ.get("SystemRoot", "C:/Windows"),
        "System32",
        "drivers",
        "etc",
        "hosts",
    )
    try:
        tools.ensure_inside(other, [root])
    except ValueError:
        return
    raise AssertionError("expected ValueError for unrelated path")


def test_empty_roots_rejected():
    try:
        tools.ensure_inside(os.path.join(root_dir(), "tools.py"), [])
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty roots")


if __name__ == "__main__":
    names = sorted(n for n in globals() if n.startswith("test_"))
    failed = []
    for name in names:
        try:
            globals()[name]()
            print("PASS", name)
        except Exception as e:
            failed.append(name)
            print("FAIL", name, repr(e))
    if failed:
        print("SELFCHECK FAIL:", failed)
        sys.exit(1)
    print("SELFCHECK PASS")
    sys.exit(0)