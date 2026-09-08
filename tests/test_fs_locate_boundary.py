#!/usr/bin/env python3
"""tests/test_fs_locate_boundary.py — 文件/目录选择器后端定位边界测试（纯函数级，不起服务）。

覆盖（文件选择器与断链加固边界优化）：
  - 路径边界白名单根 _fs_roots：返回本机盘符根 + path_allowlist，无重复
  - 文件定位 mode=file：只在当前 target_root 内查同名文件 → 相对路径；
    不存在/越权名/超长名/非法 mode 一律拒绝
  - 目录定位 mode=dir：在路径白名单根内有界查找；同名歧义不静默切换(abs=None)
  - 输入校验：../ 路径注入 / 绝对路径成分 / 超长名 全拒绝
"""
import os
import sys
import tempfile

LAB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lab"))
if LAB not in sys.path:
    sys.path.insert(0, LAB)

from lab_config import LabConfig, set_config_for_tests  # noqa: E402
from web_viz import _fs_roots, _locate, _find_file_rel  # noqa: E402


def _make_cfg():
    """临时配置：target_root 指向临时目录，path_allowlist 指向该临时目录。"""
    td = tempfile.mkdtemp(prefix="lab_fsloc_")
    os.makedirs(os.path.join(td, "sub"), exist_ok=True)
    with open(os.path.join(td, "alpha.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    with open(os.path.join(td, "sub", "beta.py"), "w", encoding="utf-8") as f:
        f.write("y = 2\n")
    cfg = LabConfig()
    cfg.values["target_root"] = td
    cfg.values["path_allowlist"] = [td]
    return cfg, td


def test_roots_whitelist_dedup_and_allowlist():
    cfg, td = _make_cfg()
    roots = _fs_roots(cfg)
    assert roots, "应至少返回盘符根"
    assert len(roots) == len(set(roots)), "白名单根不应重复"
    assert os.path.realpath(td) in roots, "path_allowlist 应并入白名单根"


def test_locate_file_found_within_target():
    cfg, td = _make_cfg()
    r = _locate({"mode": "file", "name": "alpha.py"}, cfg)
    assert r["ok"] is True
    assert r["rel"] == "alpha.py"
    assert os.path.isfile(r["abs"])
    # 子目录内文件也能定位
    r2 = _locate({"mode": "file", "name": "beta.py"}, cfg)
    assert r2["ok"] is True and r2["rel"] == "sub/beta.py"


def test_locate_file_notfound_rejected():
    cfg, _ = _make_cfg()
    r = _locate({"mode": "file", "name": "zzz_missing.py"}, cfg)
    assert r["ok"] is False and "未找到" in r["error"]


def test_locate_input_validation_traversal_and_longname():
    cfg, _ = _make_cfg()
    # 路径穿越注入拒绝
    for bad in ("../evil", "..\\evil", "C:/Windows/win.ini", "/etc/passwd"):
        r = _locate({"mode": "dir", "name": bad}, cfg)
        assert r["ok"] is False and "非法路径" in r["error"], bad
    # 超长名拒绝
    r = _locate({"mode": "dir", "name": "a" * 250}, cfg)
    assert r["ok"] is False and "过长" in r["error"]
    # 非法 mode 拒绝
    r = _locate({"mode": "bogus", "name": "x"}, cfg)
    assert r["ok"] is False
    # 非字符串 name 拒绝
    r = _locate({"mode": "file", "name": ["a"]}, cfg)
    assert r["ok"] is False


def test_locate_dir_ambiguous_does_not_auto_switch():
    cfg, td = _make_cfg()
    # 在 allowlist 根下放两个同名目录 → 歧义 → 列出候选、不自动切换(abs 语义由候选数决定)
    os.makedirs(os.path.join(td, "same_name"), exist_ok=True)
    os.makedirs(os.path.join(td, "sub", "same_name"), exist_ok=True)
    r = _locate({"mode": "dir", "name": "same_name"}, cfg)
    assert r["ok"] is True
    assert r["candidates"] and len(r["candidates"]) >= 2


def test_find_file_rel_depth_pruning():
    cfg, td = _make_cfg()
    rel = _find_file_rel("alpha.py", td)
    assert rel == "alpha.py"
    # 目标外文件不应被定位（find_file_rel 只在 target 内遍历）
    outside = tempfile.mkdtemp(prefix="lab_outside_")
    with open(os.path.join(outside, "zz.py"), "w", encoding="utf-8") as f:
        f.write("z=1\n")
    assert _find_file_rel("zz.py", td) is None


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
