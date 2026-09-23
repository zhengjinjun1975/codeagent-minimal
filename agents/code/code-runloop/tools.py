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
    """search-replace 字面量唯一匹配; dry_run=True 内存模拟不落盘。

    行尾必须原样保留（newline="" 关掉换行翻译）：Windows 上默认文本模式读会把 CRLF 翻成 LF、
    写时又把 LF 翻成 os.linesep(CRLF) —— 于是一次小改就把整个文件的行尾换掉，git diff 变成
    "整文件重写"，没法审阅（实测：CodeAgent 改一次 security_scan.py，diff 454 增/391 删全是行尾噪音）。
    """
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        raw = f.read()
    # 统一到 \n 上做匹配（模型给的 old/new 常带 \r\n，而文件可能是 LF —— 直接字面量比会"找不到"），
    # 匹配完再按文件**原本的**行尾写回，保证行尾不变。
    nl = "\r\n" if "\r\n" in raw else "\n"
    content = raw.replace("\r\n", "\n")
    old_m = (old or "").replace("\r\n", "\n")
    new_m = (new or "").replace("\r\n", "\n")
    idxs = [m.start() for m in re.finditer(re.escape(old_m), content)]
    n = len(idxs)
    if n == 0:
        return {"status": "error",
                "reason": f"0 blocks failed to match(找不到该 old 字面量): {old[:120]!r}"}
    if n > 1:
        return {"status": "error",
                "reason": f"{n} blocks matched, 需唯一锚点; 请加更多上下文(引用上一行/缩进): {old[:120]!r}"}
    new_content = content[:idxs[0]] + new_m + content[idxs[0] + len(old_m):]
    if nl == "\r\n":
        new_content = new_content.replace("\n", "\r\n")
    if not dry_run:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)
    before = content[idxs[0] - 0: idxs[0]]
    return {"status": "ok", "path": path, "replaced": 1, "dry_run": dry_run,
            "preview": (old[:60] + " → " + new[:60]).replace("\n", "\\n")}


def _as_bool(v, default=True):
    """布尔参数容错：模型常有把 dry_run 传成字符串 "false"/"False" 的（bool("false") 是 True → 该落盘的
    只做了 dry-run，改动静默没落盘，实测一次派活里连吞 3 处编辑）。
    ponytail: 只认这几个字面量，其它字符串一律用 default。"""
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("false", "0", "no", "n", "off", ""):
            return False
        if s in ("true", "1", "yes", "y", "on"):
            return True
        return default
    if v is None:
        return default
    return bool(v)


def _unescape_newlines(s):
    """把"整条里一个真换行都没有、却写了字面 \\n"的参数还原成真换行。

    实测（2026-09-21 派 G3 三次）：模型把 old 传成 '# 注释\\ndef f():\\n    x' 这种**字面反斜杠 n**
    → 字面量匹配必然失败，报"找不到该 old"，白烧轮次。只在"没有真换行"时才还原，
    避免动到本来含真换行的正常参数。
    ponytail: 只处理 \\n；\\t/\\" 这类没见模型写错过，先不猜。
    """
    if "\n" not in s and "\\n" in s:
        return s.replace("\\n", "\n")
    return s


def edit(path, old, new, roots=None, dry_run=False):
    """edit(path, old, new): 字面量唯一匹配替换，**默认直接落盘**；失败精确回显只重发失败块。

    为什么默认落盘（2026-09-21 改）：原来默认"先 dry-run 校验、再重发 dry_run=false 落盘"的两步设计，
    实测模型三次栽在这上面（把 dry_run 传成字符串 "false"、干脆不传 → 只干跑不落盘、看着改了其实没写，
    白烧到上限）。落盘安全性已由 roots 白名单 + 循环自己的 DoD 复跑兜住，不需要再多一道默认干跑。
    要预览（明确的干跑）才显式传 dry_run=true。
    """
    if not old:
        return {"status": "error", "reason": "edit 的 old(被替换字面量)不能为空"}
    try:
        ap = ensure_inside(path, roots) if roots else os.path.abspath(path)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}
    if not os.path.isfile(ap):
        return {"status": "error", "reason": f"文件不存在: {path} (edit 需先 write 创建)"}
    dry_run = _as_bool(dry_run, default=False)
    old, new = _unescape_newlines(old), _unescape_newlines(new)
    r = _apply_edit(ap, old, new, dry_run=dry_run)
    if r["status"] == "ok" and not dry_run:
        r["msg"] = "已落盘替换 1 处"
    elif r["status"] == "ok" and dry_run:
        r["msg"] = "dry-run 唯一匹配通过(未落盘)"
    return r


def apply_edits(path, edits, roots=None):
    """定点替换：一串 old→new 一次性应用到同一个文件，**全成或全不成**（零模型调用）。

    为什么要有它（2026-09-21 用户拍板）：改一两行的活走 run_loop 要 5~6 轮云端调用，实测模型还把
    轮次全烧在读文件上（连派 4 次零改动）；而这种活调用方**本来就确切知道改什么**，不需要模型。
    这里只做三件事：预检（每条都能唯一匹配）→ 落盘 → 交调用方自己跑 DoD。复用 `_apply_edit`，
    所以行尾保留、字面 \\n 还原、唯一锚点三条纪律全继承。
    """
    if not path:
        return {"ok": False, "error": "缺少 path", "applied": []}
    p = os.path.abspath(str(path))
    if roots:
        # 注意 ensure_inside 的契约：**成功返回规范化路径、越界抛 ValueError**
        # （第一版把它当成"返回错误串"，结果每次都当越界拒掉 —— 是 verify_code_patch.py 抓出来的）
        try:
            p = ensure_inside(p, roots)
        except ValueError as e:
            return {"ok": False, "error": "路径越界被拒: %s" % e, "applied": []}
    if not os.path.exists(p):
        return {"ok": False, "error": "目标文件不存在: %s" % p, "applied": []}
    if not edits:
        return {"ok": False, "error": "缺少 edits（[{old,new},...] 或 [[old,new],...]）", "applied": []}

    items = []
    for i, e in enumerate(edits):
        if isinstance(e, (list, tuple)) and len(e) == 2:
            old, new = e
        elif isinstance(e, dict):
            old, new = e.get("old"), e.get("new")
        else:
            return {"ok": False, "error": "第 %d 条 edit 形状不对（要 {old,new} 或 [old,new]）" % i,
                    "applied": []}
        old, new = _unescape_newlines(old), _unescape_newlines(new or "")
        if not old:
            return {"ok": False, "error": "第 %d 条缺 old（必须给要替换掉的原文）" % i, "applied": []}
        items.append({"index": i, "old": old, "new": new})

    # 预检：每条都必须唯一匹配 —— 任一不匹配就一条也不落盘（不留改一半的文件）
    for it in items:
        r = _apply_edit(p, it["old"], it["new"], dry_run=True)
        if r.get("status") != "ok":
            return {"ok": False, "applied": [],
                    "error": "第 %d 条预检失败（未落盘）: %s" % (it["index"], r.get("reason"))}

    applied = []
    for it in items:
        r = _apply_edit(p, it["old"], it["new"], dry_run=False)
        applied.append({"index": it["index"], "ok": r.get("status") == "ok",
                        "reason": r.get("reason", ""), "preview": r.get("preview", "")})
    return {"ok": all(a["ok"] for a in applied), "path": p, "applied": applied,
            "count": len(applied)}


def write(path, content, roots=None, overwrite=True):
    overwrite = _as_bool(overwrite, default=True)
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
