"""纯标准库项目验收核心模块：smoke 导入 + 自带测试/编译检查。"""
import compileall
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    "tests", "_pending_atoms", "lab", "vendor",
}
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _iter_py_files(root, exclude):
    """遍历 root 下 *.py，返回 (待测文件列表, skipped 列表)。"""
    files, skipped = [], []
    ex = set(exclude or [])
    for dirpath, dirnames, filenames in os.walk(root):
        kept = []
        for d in dirnames:
            if d in SKIP_DIRS or d in ex:
                skipped.append({"file": os.path.join(dirpath, d), "reason": "跳过目录: " + d})
            else:
                kept.append(d)
        dirnames[:] = kept
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if fn.startswith("bad_sample"):
                skipped.append({"file": os.path.join(dirpath, fn), "reason": "跳过文件名前缀 bad_sample"})
                continue
            files.append(os.path.join(dirpath, fn))
    return files, skipped


def _smoke_one(path, idx):
    """加载单个文件，返回 (ok, issue)。"""
    name = "pv_%s_%d" % (os.path.splitext(os.path.basename(path))[0], idx)
    d = os.path.dirname(path)
    old_argv = sys.argv
    inserted = d not in sys.path
    if inserted:
        sys.path.insert(0, d)
    try:
        sys.argv = [path]
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return False, "无法构建模块 spec"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not any(callable(getattr(mod, a)) for a in dir(mod) if not a.startswith("__")):
            return False, "未找到顶层可调用函数"
        return True, ""
    except SystemExit as e:
        return False, "SystemExit: %s" % (e,)
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, e)
    finally:
        sys.argv = old_argv
        if inserted and d in sys.path:
            try:
                sys.path.remove(d)
            except ValueError:
                pass


def _run_unit(path, timeout):
    """有测试跑 pytest，否则退而求其次跑 compileall。"""
    has_tests = os.path.isdir(os.path.join(path, "tests"))
    if not has_tests:
        try:
            has_tests = any(
                fn.startswith("test_") and fn.endswith(".py")
                for fn in os.listdir(path)
            )
        except OSError:
            has_tests = False
    if has_tests:
        cmd = [sys.executable, "-m", "pytest", "-q", "--no-header"]
    else:
        cmd = [
            sys.executable, "-c",
            "import compileall,sys; sys.exit(0 if compileall.compile_dir(sys.argv[1], quiet=2) else 1)",
            path,
        ]
    try:
        p = subprocess.run(
            cmd, cwd=path, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        out = p.stdout.decode("utf-8", "replace")
        tail = "\n".join(_CTRL.sub("", out).splitlines()[-15:])
        return {"ran": True, "cmd": " ".join(cmd), "returncode": p.returncode, "tail": tail}
    except subprocess.TimeoutExpired:
        return {"ran": True, "cmd": " ".join(cmd), "returncode": -1, "tail": "超时(%ss)" % timeout}
    except Exception as e:
        return {"ran": True, "cmd": " ".join(cmd), "returncode": -1, "tail": "%s: %s" % (type(e).__name__, e)}


def verify_project(path, timeout=120, exclude=None):
    """验收项目，返回普通 dict，内部绝不抛异常。"""
    try:
        if not path or not os.path.isdir(path):
            return {
                "ok": False, "root": str(path), "files": 0, "smoke_passed": 0,
                "smoke_failed": [], "unit": {"ran": False, "cmd": "", "returncode": -1, "tail": ""},
                "summary": "路径不存在或不是目录: %s" % path, "skipped": [],
            }
        root = os.path.abspath(path)
        files, skipped = _iter_py_files(root, exclude)
        smoke_failed, passed = [], 0
        for i, f in enumerate(files):
            ok, issue = _smoke_one(f, i)
            if ok:
                passed += 1
            else:
                smoke_failed.append({"file": f, "issue": issue})
        unit = _run_unit(root, timeout)
        ok = (not smoke_failed) and (not unit["ran"] or unit["returncode"] == 0)
        if not smoke_failed and (not unit["ran"] or unit["returncode"] == 0):
            summary = "项目验收通过：%d 个文件全部可导入，自带测试通过" % len(files)
        else:
            first = os.path.basename(smoke_failed[0]["file"]) if smoke_failed else "无"
            summary = "%d 个文件导入失败（首个：%s），自带测试 returncode=%d" % (
                len(smoke_failed), first, unit["returncode"],
            )
        return {
            "ok": ok, "root": root, "files": len(files), "smoke_passed": passed,
            "smoke_failed": smoke_failed, "unit": unit, "summary": summary, "skipped": skipped,
        }
    except Exception as e:
        return {
            "ok": False, "root": str(path), "files": 0, "smoke_passed": 0,
            "smoke_failed": [], "unit": {"ran": False, "cmd": "", "returncode": -1, "tail": ""},
            "summary": "验收异常: %s: %s" % (type(e).__name__, e), "skipped": [],
        }


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="pv_selfcheck_")
    try:
        with open(os.path.join(tmp, "sibling.py"), "w", encoding="utf-8") as f:
            f.write("def sib():\n    return 1\n")
        with open(os.path.join(tmp, "mod_a.py"), "w", encoding="utf-8") as f:
            f.write("import sibling\n\ndef a():\n    return sibling.sib()\n")
        with open(os.path.join(tmp, "bad_syntax.py"), "w", encoding="utf-8") as f:
            f.write("def broken(:\n")
        r = verify_project(tmp, timeout=30)
        assert r["ok"] is False, r
        assert any("bad_syntax.py" in x["file"] for x in r["smoke_failed"]), r
        print("SELFCHECK PASS")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)