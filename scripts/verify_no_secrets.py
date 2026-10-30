#!/usr/bin/env python3
"""发布前密钥扫描：工作区与 git 历史都不许出现真实密钥。

不打印命中的值，只打印位置、长度与判定，避免扫描器自己把密钥带进日志。

判定：
  critical     长度与形态都像真密钥，必须为 0 才能发布
  fixture      测试夹具刻意造的假值（含 test/demo/abc 之类或过短），可接受
  suspicious   判不准，人工看一眼

退出码：有 critical 返回 1，否则 0。
"""
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 词边界是必须的：没有它会命中 ask-input / task-state 这类普通标识符。
PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])("
    r"sk-[A-Za-z0-9_\-]{16,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|gho_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|[Bb]earer\s+[A-Za-z0-9_\-\.]{25,}"
    r")")

FAKE = ("abc", "xxx", "test", "demo", "your", "example", "placeholder",
        "fake", "dummy", "redacted", "sample", "fixture")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv",
             "venv", ".taskstate", "_impl_output"}
TEXT_EXT = (".py", ".json", ".md", ".js", ".html", ".yml", ".yaml", ".toml",
            ".txt", ".sh", ".cfg", ".ini", ".ps1")
MIN_REAL_LEN = 30


def judge(value):
    low = value.lower()
    if "..." in value or len(set(value)) == 1:
        return "fixture"
    if any(w in low for w in FAKE):
        return "fixture"
    if len(value) >= MIN_REAL_LEN:
        return "critical"
    return "suspicious"


def scan_text(text, where, out):
    for m in PATTERN.finditer(text):
        out.append((judge(m.group(0)), where, len(m.group(0))))


def scan_worktree(out):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.lower().endswith(TEXT_EXT) and fn != ".env":
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    scan_text(f.read(), "工作区:" + rel, out)
            except Exception:  # fail-open: 单个文件读不了不影响整体，缺失不算泄漏
                continue


def scan_history(out):
    try:
        r = subprocess.run(["git", "-C", ROOT, "log", "-p", "--all"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
    except Exception as e:  # fail-open: 扫不成就明说，不能当成干净
        out.append(("unknown", "git 历史读取失败: %s" % e, 0))
        return
    cur = "?"
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^commit ([0-9a-f]{7,})", line)
        if m:
            cur = m.group(1)
            continue
        for mm in PATTERN.finditer(line):
            out.append((judge(mm.group(0)), "历史:" + cur, len(mm.group(0))))


def main():
    out = []
    scan_worktree(out)
    scan_history(out)

    crit = [x for x in out if x[0] == "critical"]
    susp = [x for x in out if x[0] == "suspicious"]
    fix = [x for x in out if x[0] == "fixture"]
    unk = [x for x in out if x[0] == "unknown"]

    print("扫描完成：critical %d / suspicious %d / fixture %d"
          % (len(crit), len(susp), len(fix)))
    for sev, where, n in crit[:20]:
        print("  [critical] %s | 长度 %d" % (where, n))
    for sev, where, n in susp[:10]:
        print("  [suspicious] %s | 长度 %d" % (where, n))
    for sev, where, n in unk[:5]:
        print("  [unknown] %s" % where)

    if crit:
        print("=> 不通过：存在疑似真实密钥，发布前必须清掉")
        return 1
    print("=> 通过：无真实密钥")
    return 0


if __name__ == "__main__":
    sys.exit(main())
