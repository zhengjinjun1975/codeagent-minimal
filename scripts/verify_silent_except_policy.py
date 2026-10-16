#!/usr/bin/env python3
"""静默吞异常策略门：AST 扫 except/pass、except/continue、except/return-空值 三类静默吞异常点，与基线比对。

口径（2026-10-15 扩）与 scripts/constitution_gate.py 的"静默吞错"对齐：
  except 体只有 pass / continue / return <空值>（None/False/0/""/[]/{}） ⇒ 要求同一处带标记理由。

默认只读判定 C1-C4；仅 --write-baseline 时写 config/silent_except_baseline.json。
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

# CI/子进程里 stdout 是管道，默认 codepage 可能是 gbk ⇒ 直接 print ✓ 会 UnicodeEncodeError（实测）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # fail-open: 旁路：失败不影响主流程
    pass

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "config" / "silent_except_baseline.json"
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "vendor", "_store", "_quarantine",
    "_repair_backup", "_impl_output", "_archive", "artifacts", "lab_data",
}
EXCEPT_RE = re.compile(r"^\s*except\b[^:]*:\s*(#.*)?$")
PASS_RE = re.compile(r"^\s*pass\s*(#.*)?$")
# 标记分两类（两类都必须在同一行里带非空理由）：
#   fail-open: <理由>    —— 吞掉的是意外错，仅旁路（不影响主流程）
#   expected-exc: <理由> —— 吞掉的是自己断言要的那类异常（测试里的"必须拒绝/必须抛"写法）
MARK_RE = re.compile(r"fail[-_]open|expected[-_]exc", re.IGNORECASE)


def iter_py_files(root: Path):
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _is_empty_return(value):
    """return / return None/False/True/0/0.0/"" / [] / {} / () 都算"空返回"。"""
    if value is None:
        return True
    if isinstance(value, ast.Constant):
        return value.value in (None, False, True, 0, 0.0, "")
    if isinstance(value, (ast.List, ast.Tuple)):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys
    return False


def _is_silent(handler):
    """handler 体（忽略字符串文档语句）只剩 pass/continue/return-空值 ⇒ 静默。"""
    stmts = [s for s in handler.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                     and isinstance(s.value.value, str))]
    if not stmts:
        return False
    for s in stmts:
        if isinstance(s, (ast.Pass, ast.Continue)):
            continue
        if isinstance(s, ast.Return) and _is_empty_return(s.value):
            continue
        return False
    return True


def scan_text(text: str):
    """返回 [(行号, 是否已标记)]，行号 1-based，指向 except 行；语法错时退回行级扫描。"""
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _scan_text_linewise(lines)
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not _is_silent(node):
            continue
        end = node.end_lineno or node.lineno
        seg = "\n".join(lines[node.lineno - 1:end])
        sites.append((node.lineno, bool(MARK_RE.search(seg))))
    return sorted(sites)


def _scan_text_linewise(lines):
    """语法解析失败时的退化口径：老的行级 except+pass 扫描。"""
    sites = []
    for i, ln in enumerate(lines):
        if not EXCEPT_RE.match(ln):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or not PASS_RE.match(lines[j]):
            continue
        combined = (ln + "\n" + lines[j]).lower()
        sites.append((i + 1, bool(MARK_RE.search(combined))))
    return sites


def scan_repo(root: Path):
    unmarked, marked = [], []
    for p in iter_py_files(root):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # fail-open: scan_repo 里出错就跳过该条继续扫
            continue
        rel = p.relative_to(root).as_posix()
        for lineno, is_marked in scan_text(text):
            site = f"{rel}:{lineno}"
            (marked if is_marked else unmarked).append(site)
    return unmarked, marked


def load_baseline():
    if not BASELINE.exists():
        return None
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # fail-open: load_baseline 里出错就返回空值，调用方按缺失处理
        return None


def self_check():
    """C4 分辨力自检：内存夹具，不碰仓库。三类形状各给正反例。"""
    bad = "try:\n    x()\nexcept Exception:\n    pass\n"
    good = "try:\n    x()\nexcept Exception:  # fail-open: 保持可用\n    pass\n"
    bad2 = "for i in y:\n    try:\n        z(i)\n    except ValueError:\n        continue\n"
    good2 = "def f():\n    try:\n        z()\n    except ValueError:  # fail-open: 单条坏了跳过\n        return []\n"
    bk = len(scan_text(bad)) == 1 and scan_text(bad)[0][1] is False
    gk = len(scan_text(good)) == 1 and scan_text(good)[0][1] is True
    bk2 = len(scan_text(bad2)) == 1 and scan_text(bad2)[0][1] is False
    gk2 = len(scan_text(good2)) == 1 and scan_text(good2)[0][1] is True
    return bk and gk and bk2 and gk2


def run_check():
    unmarked, marked = scan_repo(ROOT)
    print(f"未标记静默吞异常点: {len(unmarked)}")
    print(f"已标记静默吞异常点: {len(marked)}")
    for s in unmarked:
        print(f"  未标记 {s}")

    baseline = load_baseline()
    if baseline is None:
        print("✗ 基线文件不存在或损坏，请先运行 --write-baseline")
        print("GATE_EXIT=1")
        return 1

    ok = True

    # C1 未标记总数 <= 基线 count
    base_count = baseline.get("count", -1)
    if len(unmarked) <= base_count:
        print(f"✓ C1 未标记总数 {len(unmarked)} <= 基线 {base_count}")
    else:
        print(f"✗ C1 未标记总数 {len(unmarked)} > 基线 {base_count}，多出 {len(unmarked) - base_count} 个")
        ok = False

    # C2 每个未标记点必须在基线 sites 里
    base_sites = set(baseline.get("sites", []))
    new_sites = [s for s in unmarked if s not in base_sites]
    if not new_sites:
        print("✓ C2 无新增未标记点")
    else:
        print(f"✗ C2 新增未标记点 {len(new_sites)} 个:")
        for s in new_sites:
            print(f"  新增 {s}")
        ok = False

    # C3 基线自洽
    if base_count == len(base_sites):
        print(f"✓ C3 基线自洽 count={base_count} sites={len(base_sites)}")
    else:
        print(f"✗ C3 基线不自洽 count={base_count} sites={len(base_sites)}")
        ok = False

    # C4 分辨力自检
    if self_check():
        print("✓ C4 分辨力自检通过")
    else:
        print("✗ C4 分辨力自检失败")
        ok = False

    print("GATE_EXIT=0" if ok else "GATE_EXIT=1")
    return 0 if ok else 1


def write_baseline(allow_grow: bool):
    unmarked, _ = scan_repo(ROOT)
    new_count = len(unmarked)
    old = load_baseline()
    old_count = old.get("count") if isinstance(old, dict) else None

    if old_count is not None and new_count > old_count and not allow_grow:
        print(f"✗ 拒绝写入：新 count {new_count} > 现有 {old_count}（只降不涨，需 --allow-grow）")
        print("GATE_EXIT=1")
        return 1

    data = {
        "count": new_count,
        "sites": sorted(unmarked),
        "note": "静默吞异常基线：未标记的 except/pass、except/continue、except/return-空值 点，只降不涨",
    }
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ 已写入基线 count={new_count} sites={len(data['sites'])}")
    print("GATE_EXIT=0")
    return 0


def main():
    ap = argparse.ArgumentParser(description="静默吞异常策略门")
    ap.add_argument("--write-baseline", action="store_true", help="重新生成基线")
    ap.add_argument("--allow-grow", action="store_true", help="允许基线增长")
    args = ap.parse_args()
    if args.write_baseline:
        return write_baseline(args.allow_grow)
    return run_check()


if __name__ == "__main__":
    sys.exit(main())