# -*- coding: utf-8 -*-
"""契约总账的守门测试。

锁三件事：
1. 断链必须是 0 —— manifest 声明的输入，实现得接得住（test.run 的 timeout 就是漏在这）
2. 生成物必须和代码同步 —— 改了 manifest/签名没重跑 --emit，文档立刻失真
3. 判不了的原子不许硬判 —— 吃 **kwargs 的原子，声明与实现的对应本就静态不可知，
   不许它悄悄报「断链 0」冒充通过
"""
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "export_contracts.py")


def _load():
    spec = importlib.util.spec_from_file_location("export_contracts_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=ROOT, timeout=120)


def test_no_broken_contract():
    """41 个原子声明的输入，全部能被实现接住。"""
    r = _run("--check")
    assert r.returncode == 0, "契约断链：\n" + r.stdout + r.stderr


def test_artifacts_in_sync():
    """docs/CONTRACTS.md 与 config/contracts.json 必须是当前代码的产物。"""
    ec = _load()
    md, _ = ec.render_md(ec.build_rows())
    with open(ec.MD_PATH, encoding="utf-8") as f:
        on_disk = f.read()
    assert on_disk == md, "docs/CONTRACTS.md 与代码不同步，跑 `python scripts/export_contracts.py --emit`"


def test_unjudgeable_atoms_do_not_fake_pass():
    """吃 **kwargs / 签名不透明的原子，必须标 judgeable=False 且不许报断链。"""
    ec = _load()
    rows = ec.build_rows()
    unjudgeable = [r for r in rows if not r["judgeable"]]
    assert unjudgeable, "若一个都判不了，说明签名基线退化成全 **kwargs，账就失效了"
    for r in unjudgeable:
        assert r["broken"] == [], "%s 判不了却报了断链，等于假装看得见" % r["atom"]
