#!/usr/bin/env python3
"""tools.py — code-runloop 工具集执行本体(纯 stdlib)。设计 §4。

run / read_file / grep / edit / write / verify_cmd。
每个工具返回 dict; run_loop 把 dict 统一格式化为 <tool_result> 文本回喂模型。
- run: subprocess 分列捕获 + 截断保尾 + 超时哨兵 124。
- edit: search-replace 字面量, 唯一匹配, 内存 dry-run 才落盘。
- write/edit: FS 白名单(root) 约束, 越界拒绝回理由。
- 全程不解析模型, 由 run_loop 把真实输出原样当 user 消息回喂。
"""
import os
import re
import subprocess

DEFAULT_TIMEOUT = 60
OUT_MAX_LINES = 250      # 截断保尾: 最多保留的行数(前缀 [truncated N lines])
OUT_MAX_CHARS = 20000    # 字节帽


def resolve_roots(*paths):
    """FS 白名单: 收集允许写入/读取的绝对根(realpath)。返回列表。"""
    roots = []
    seen = set()
    for p in paths:
        if not p:
            continue
        try:
            rp = os.path.realpath(os.path.abspath(p))
        except Exception:  # noqa
            continue
        if rp not in seen:
            seen.add(rp)
            roots.append(rp)
    return roots


def ensure_inside(path, roots):
    """返回规范化绝对路径; 若不在任一 root 下 → 抛 ValueError(越界拒绝)。"""
    if not roots:
        raise ValueError("未配置 FS 白名单根(root)")
    ap = os.path.realpath(os.path.abspath(path))
    for r in roots:
        if ap == r or ap.startswith(r + os.sep):
            return ap
    raise ValueError(f"路径越界被拒(不在白名单 root 内): {path}\n  roots={roots}")


def truncate_tail(text, max_lines=OUT_MAX_LINES, max_chars=OUT_MAX_CHARS):
    """截断保尾: 保行数 + 字节帽, 前缀 [truncated ...]。"""
    if text is None:
        return ""
    lines = text.splitlines()
    dropped = 0
    if len(lines) > max_lines:
        dropped = len(lines) - max_lines
        lines = lines[-max_lines:]
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[-max_chars:]
        dropped = max(dropped, 1)
    if dropped:
        body = f"[truncated {dropped} lines ...]\n" + body
    return body


def run(cmd, cwd=None, timeout=DEFAULT_TIMEOUT, max_lines=OUT_MAX_LINES,
        max_chars=OUT_MAX_CHARS, env=None):
    """run(cmd, cwd, timeout): 真执行; 分列捕获; 截断保尾; exit_code/超时哨兵124。"""
    if not cmd or not str(cmd).strip():
        return {"status": "error", "reason": "cmd 为空", "exit_code": 1}
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    timeout = int(timeout or DEFAULT_TIMEOUT)
    try:
        p = subprocess.run(str(cmd), shell=True, cwd=cwd, env=full_env,
                           capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        code = p.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        code = 124          # 超时哨兵(与设计一致)
        timed_out = True
    except Exception as e:  # noqa
        return {"status": "error", "reason": f"{type(e).__name__}: {e}",
                "exit_code": -1, "output": "", "timeout": False, "truncated": False}
    stdout = truncate_tail(getattr(p, "stdout", ""), max_lines, max_chars)
    stderr = truncate_tail(getattr(p, "stderr", ""), max_lines, max_chars)
    combined = (stdout + ("\n" + stderr if stderr else "")).strip()
    return {"status": "ok", "exit_code": code, "stdout": stdout,
            "stderr": stderr, "output": combined,
            "timeout": timed_out,
            "truncated": len(stdout) != len(getattr(p, "stdout", ""))}


def read_file(path, a=None, b=None, roots=None):
    """read_file(path, a, b): 行范围读(1-indexed); 报错行自动附带上下文。"""
    try:
        ap = ensure_inside(path, roots) if roots else os.path.abspath(path)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}
    if not os.path.isfile(ap):
        return {"status": "error", "reason": f"文件不存在: {path}"}
    try:
        with open(ap, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return {"status": "error", "reason": f"读取失败: {type(e).__name__}: {e}"}
    total = len(lines)
    a = int(a) if a is not None else 1
    b = int(b) if b is not None else total
    a, b = max(1, a), min(total, b)
    if a > b:
        return {"status": "error", "reason": f"越界: a={a} > 文件总行数 {total}",
                "total_lines": total}
    chunk = []
    for i in range(a, b + 1):
        chunk.append(f"{i:>6}| {lines[i - 1].rstrip()}")
    head = f"# read_file {ap}  lines {a}-{b} / {total}"
    return {"status": "ok", "path": ap, "total_lines": total,
            "content": head + "\n" + "\n".join(chunk)}


def grep(pattern, path, roots=None):
    """grep(pattern, path): 正则定位符号/调用点, 返回带行号匹配(上限截断)。"""
    try:
        ap = ensure_inside(path, roots) if roots else os.path.abspath(path)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}
    if os.path.isdir(ap):
        return {"status": "error", "reason": f"grep 需指向文件: {path} 是目录"}
    if not os.path.isfile(ap):
        return {"status": "error", "reason": f"文件不存在: {path}"}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"status": "error", "reason": f"非法正则: {type(e).__name__}: {e}"}
    matches = []
    try:
        with open(ap, encoding="utf-8", errors="replace") as f:
            for ln, line in enumerate(f, 1):
                if rx.search(line):
                    matches.append(f"{ln:>6}| {line.rstrip()}")
                    if len(matches) >= 200:
                        matches.append("... (截断, 已超 200 匹配)")
                        break
    except OSError as e:
        return {"status": "error", "reason": f"读取失败: {type(e).__name__}: {e}"}
    return {"status": "ok", "path": ap, "matches": matches, "count": len(matches)}


def _apply_edit(path, old, new, dry_run):
    """search-replace 字面量唯一匹配; dry_run=True 内存模拟不落盘。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()
    idxs = [m.start() for m in re.finditer(re.escape(old), content)]
    n = len(idxs)
    if n == 0:
        return {"status": "error",
                "reason": f"0 blocks failed to match(找不到该 old 字面量): {old[:120]!r}"}
    if n > 1:
        return {"status": "error",
                "reason": f"{n} blocks matched, 需唯一锚点; 请加更多上下文(引用上一行/缩进): {old[:120]!r}"}
    new_content = content[:idxs[0]] + new + content[idxs[0] + len(old):]
    if not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    before = content[idxs[0] - 0: idxs[0]]
    return {"status": "ok", "path": path, "replaced": 1, "dry_run": dry_run,
            "preview": (old[:60] + " → " + new[:60]).replace("\n", "\\n")}


def edit(path, old, new, roots=None, dry_run=True):
    """edit(path, old, new): 内存 dry-run 校验后才落盘; 失败精确回显只重发失败块。"""
    if not old:
        return {"status": "error", "reason": "edit 的 old(被替换字面量)不能为空"}
    try:
        ap = ensure_inside(path, roots) if roots else os.path.abspath(path)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}
    if not os.path.isfile(ap):
        return {"status": "error", "reason": f"文件不存在: {path} (edit 需先 write 创建)"}
    dry_run = bool(dry_run)
    r = _apply_edit(ap, old, new, dry_run=dry_run)
    if r["status"] == "ok" and not dry_run:
        r["msg"] = "已落盘替换 1 处"
    elif r["status"] == "ok" and dry_run:
        r["msg"] = "dry-run 唯一匹配通过(未落盘)"
    return r


def write(path, content, roots=None, overwrite=True):
    """write(path, content): 整写(Add/Overwrite)。FS 白名单越界拒绝回理由。"""
    try:
        ap = ensure_inside(path, roots) if roots else os.path.abspath(path)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}
    d = os.path.dirname(ap)
    try:
        os.makedirs(d, exist_ok=True)
        mode = "w" if overwrite or not os.path.exists(ap) else "x"
        with open(ap, mode, encoding="utf-8", newline="\n") as f:
            f.write(content if content else "")
    except OSError as e:
        return {"status": "error", "reason": f"写入失败: {type(e).__name__}: {e}"}
    return {"status": "ok", "path": ap, "size": len(content or ""), "msg": "已写入"}


def verify_cmd(cmd, cwd=None, timeout=120):
    """verify_cmd: 收尾 DoD 硬校验; ok == exit 0。失败真实输出由 run_loop 回喂重来。

    Windows 下 cmd.exe 无 pipefail, verify 命令若含尾管道(如 'pytest ... | tail')会吞掉
    主命令真实 exit → 假绿。故剥离最后一个 '| xxx' 段, 只执行校验主命令, 使 pytest 失败真实反映。
    """
    main = str(cmd).strip()
    if "|" in main:
        main = main.split("|")[0].strip()  # 剥离尾管道, 保留主校验命令
    r = run(main, cwd=cwd, timeout=timeout)
    ok = r["status"] == "ok" and r.get("exit_code") == 0 and not r.get("timeout")
    return {"status": "ok" if ok else "error", "ok": ok, "exit_code": r.get("exit_code"),
            "output": r.get("output", ""), "timeout": r.get("timeout", False)}
