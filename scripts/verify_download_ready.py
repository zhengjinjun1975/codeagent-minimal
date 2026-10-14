#!/usr/bin/env python3
"""下载即用自检门：只读仓库，逐项打印绿/红，最后总结并 sys.exit(0/1)。"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 120
SKIP_DIRS = {".git", "__pycache__"}
ALLOW_MARKERS = ("UNIFIED_CLIENT_DIR", "OPTMEM_", "CODEMEM_DIR", "EXTERNAL_SAMPLER_DIR")
HARD_MARKERS = ("FLYWHEEL", "采样钩子", "raw_train", "codeagent_sampler",
                "D:/code-agent-lab", "D:\\code-agent-lab", "E:/finetune")


def run(args):
    """跑子进程，返回 (退出码, stdout, stderr)；超时/异常退出码记 -1。"""
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "超时(%ds)" % TIMEOUT
    except Exception as e:  # 启动失败也算红，留痕
        return -1, "", "启动失败: %r" % e


def find_key(obj, key):
    """递归找第一个名为 key 的非字典值。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and not isinstance(v, dict):
                return v
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = find_key(it, key)
            if r is not None:
                return r
    return None


def find_dict_key(obj, key):
    """递归找第一个名为 key 的字典值（capabilities 这种本身就是 dict 的键要用它）。"""
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, dict):
            return v
        for vv in obj.values():
            r = find_dict_key(vv, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = find_dict_key(it, key)
            if r is not None:
                return r
    return None


def count_manifests():
    """数 <root>/agents 下含 manifest.json 的目录数。"""
    base = os.path.join(ROOT, "agents")
    n = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "manifest.json" in filenames:
            n += 1
    return n


def check_atoms():
    """1) 原子能全加载。"""
    code, out, err = run([sys.executable, "codeagent.py", "status", "--json"])
    if code != 0:
        return False, "status --json 退出码 %d: %s" % (code, (err or out).strip()[:200])
    try:
        data = json.loads(out)
    except Exception as e:
        return False, "status 输出非 JSON: %r" % e
    degraded = find_key(data, "degraded")
    if degraded not in ([], None):
        return False, "degraded 非空: %r" % (degraded,)
    conflicts = find_key(data, "conflicts")
    if conflicts not in ([], None):
        return False, "conflicts 非空: %r" % (conflicts,)
    atoms = find_key(data, "atoms")
    if not isinstance(atoms, list):
        return False, "未找到 atoms 列表"
    disk = count_manifests()
    if len(atoms) != disk:
        return False, "atoms 数 %d != 磁盘 manifest 数 %d" % (len(atoms), disk)
    caps = find_dict_key(data, "capabilities")
    if caps is None or len(caps) == 0:
        return False, "capabilities 未找到或为空"
    return True, "atoms=%d capabilities=%d" % (len(atoms), len(caps))


def check_run_capability():
    """2) 真跑一个能力 deadcode.scan。"""
    target = os.path.join(ROOT, "agent_runtime.py")
    inline = (
        "import json, agent_runtime\n"
        "r = agent_runtime.get_runtime().run_capability('deadcode.scan', path=%r)\n"
        "print(json.dumps({'ok': bool(getattr(r, 'ok', r))}))\n" % target
    )
    code, out, err = run([sys.executable, "-c", inline])
    if code != 0:
        return False, "run_capability 退出码 %d: %s" % (code, (err or out).strip()[:200])
    try:
        ok = json.loads(out).get("ok")
    except Exception as e:
        return False, "能力输出非 JSON: %r" % e
    if ok is not True:
        return False, "deadcode.scan ok=%r" % (ok,)
    return True, "deadcode.scan ok"


def check_evals():
    """3) 仓库自带验收能跑。"""
    for script in ("scripts/verify_evals.py", "scripts/run_evals.py"):
        code, out, err = run([sys.executable, script])
        if code != 0:
            return False, "%s 退出码 %d: %s" % (script, code, (err or out).strip()[:200])
    return True, "verify_evals + run_evals 均 0"


def scan_residue():
    """4) 无本机残留，返回命中列表 [(文件:行号, 标记)]。

    本文件自身含标记常量清单（第 12-13 行），必须跳过自己，否则自检永远红。
    """
    hits = []
    me = os.path.abspath(__file__)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.startswith(".bak"):
                continue
            if not fn.endswith((".py", ".md", ".json")):
                continue
            fp = os.path.join(dirpath, fn)
            if os.path.abspath(fp) == me:
                continue
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if any(a in line for a in ALLOW_MARKERS):
                            continue
                        for m in HARD_MARKERS:
                            if m in line:
                                hits.append(("%s:%d" % (os.path.relpath(fp, ROOT), i), m))
                                break
            except OSError:
                continue
    return hits


def check_residue():
    hits = scan_residue()
    if hits:
        return False, "残留 %d 条: %s" % (len(hits), hits[:5])
    return True, "无本机残留"


def check_regression():
    """5) 回归测试在且能跑。"""
    path = os.path.join(ROOT, "tests", "test_ensure_inside_roots.py")
    if not os.path.isfile(path):
        return False, "缺少 tests/test_ensure_inside_roots.py"
    code, out, err = run([sys.executable, "tests/test_ensure_inside_roots.py"])
    if code != 0:
        return False, "回归测试退出码 %d: %s" % (code, (err or out).strip()[:200])
    return True, "回归测试通过"


CHECKS = [
    ("原子能全加载", check_atoms),
    ("真跑一个能力", check_run_capability),
    ("仓库自带验收", check_evals),
    ("无本机残留", check_residue),
    ("回归测试在", check_regression),
]


def main():
    as_json = "--json" in sys.argv[1:]
    results = []
    for name, fn in CHECKS:
        try:
            ok, msg = fn()
        except Exception as e:  # 单项异常算红，留痕
            ok, msg = False, "异常: %r" % e
        results.append({"name": name, "ok": ok, "msg": msg})

    reds = [r for r in results if not r["ok"]]
    if as_json:
        print(json.dumps({"results": results, "red": len(reds), "pass": not reds},
                         ensure_ascii=False, indent=1))
    else:
        for r in results:
            print("%s %s：%s" % ("绿" if r["ok"] else "红", r["name"], r["msg"]))
        if not reds:
            print("下载即用自检：全过")
        else:
            print("下载即用自检：红项 %d 条" % len(reds))
    sys.exit(0 if not reds else 1)


if __name__ == "__main__":
    main()