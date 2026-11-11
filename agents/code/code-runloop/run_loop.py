#!/usr/bin/env python3
"""run_loop.py — code-runloop 真跑反馈闭环 harness 核心(纯 stdlib, 自包含)。

设计 §2/§3: 模型一次 generate 后用真工具执行+观测回喂, 循环到:
  1) 该 turn 无 tool_call(仅收尾文本) 且 verify_cmd DoD 硬校验绿 → 完成;
  2) 或 max_iterations 硬停。
失败把真实输出当 user 消息回喂重来; 工具输出截断保尾防爆 context;
捕获 FinishReasonLength → prefill 续写提示。
"""
import json
import os
import re
import sys

from prompts import (build_system_prompt, build_task_message,
                     FINISH_REASON_LENGTH_HINT)
import tools

# 失败要点抽取（统一口径，见 render_digest.py）；仓库根在本文件三级之上
try:
    _RR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _RR not in sys.path:
        sys.path.insert(0, _RR)
    import render_digest
except ImportError:  # fail-open: 抽要点只是回喂辅助，模块缺了不挡主流程（如实退化为无要点）
    render_digest = None

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
# DeepSeek 系模型(deepseek-chat / deepseek-v4-flash)不吐本引擎约定的 <tool_call>JSON 协议,
# 而吐官方 DSML 工具调用标记: 标签形如 '<{MARK} invoke name="read_file"> ... </{MARK} invoke>',
# MARK = 两个全角竖线(U+FF5C)+DSML+两个全角竖线。解析前剥掉 MARK 并把 '< ' 归并成 '<'。见 codeagent-issue-register。
# 闭合标签必须容错：DeepSeek 系偶尔把闭合标签写成 </parameter name="b" string="false">，
# 旧正则只认 </parameter>，于是 (.*?) 一路吞到**下一个**闭合标签 —— 实测 a/b/path 三个参数糊成一条，
# 表现为 read_file 缺 path / grep 缺 pattern，白烧好几轮。所以闭合处允许带任意属性。
XML_INVOKE_RE = re.compile(r'<invoke\s+name="([^"]+)"\s*>(.*?)(?:</invoke\s*[^>]*>|</>)', re.S)
XML_PARAM_RE = re.compile(r'<parameter\s+name="([^"]+)"(?P<attrs>[^>]*)>(.*?)</parameter\s*[^>]*>', re.S)
MAX_USER_OBS_CHARS = 30000   # 单条回喂消息字节帽, 防 context 爆
MAX_FORMAT_FAILURES = 3      # 工具调用格式连续坏到不可解析的上限（到顶如实报格式问题，不报成 DoD 失败）

FORMAT_FIX_HINT = (
    "[harness] 你这一轮输出的工具调用**没法解析**（标签结构不合法，不是「任务做完了」）。\n"
    "正确写法（每个 invoke 必须自己闭合，且只能有一对 calls 标签）：\n"
    "<calls>\n"
    "  <invoke name=\"read_file\">\n"
    "    <parameter name=\"path\" string=\"true\">/abs/path.py</parameter>\n"
    "  </invoke>\n"
    "</calls>\n"
    "常见错法：闭合写成 </>；或参数标签之间错位；或末尾多一个 </calls>。\n"
    "请用正确格式重新发一次工具调用。"
)

_DSML_MARK = "\uff5c\uff5cDSML\uff5c\uff5c"


def _normalize_dsml(text):
    """剥掉 DeepSeek DSML 标记, 并把 '< invoke' / '</ parameter' 归并成 '<invoke' / '</parameter'。

    还要修一种实测畸形：模型把**闭合标签和下个参数声明合并**写成一个标签 ——
        <parameter name="a">1</parameter name="b">120</parameter name="path">D:/x
    （只有 a 有开标签，b/path 的"开"被打在闭合标签上）。旧正则只认 </parameter>，
    于是 a 的值一路吞到最后一个闭合标签，b/path 直接丢失 → read_file 缺 path、grep 缺 pattern。
    这里把 `</parameter name="X" ...>` 还原成 `</parameter><parameter name="X" ...>`（invoke 同理）。
    """
    t = (text or "").replace(_DSML_MARK, "")
    t = re.sub(r"<\s+", "<", t)
    t = re.sub(r"</\s+", "</", t)
    t = re.sub(r'</\s*parameter\s+name=', "</parameter><parameter name=", t)
    t = re.sub(r'</\s*invoke\s+name=', "</invoke><invoke name=", t)
    return _repair_bare_close(t)


# 结构标签 or 裸闭合 `</>`；值里出现的 `<` 只要不是这两个标签名就不会被误伤
_TAG_OR_BARE = re.compile(r"<(?P<open>invoke|parameter)\b[^>]*>"
                          r"|</(?P<close>invoke|parameter)\s*[^>]*>"
                          r"|</>")


def _tag_step(tok, open_tag, close_tag, stack):
    """处理一个标签 token：返回该写回的文本（裸闭合 → 还原成最内层开标签的闭合）。"""
    if open_tag:
        if not tok.rstrip().endswith("/>"):
            stack.append(open_tag)
        return tok
    if close_tag:
        if stack and stack[-1] == close_tag:
            stack.pop()
        return tok
    if not stack:
        return tok                    # 没有可闭合的开标签：原样留着，绝不瞎补
    return "</%s>" % stack.pop()


def _repair_bare_close(text):
    """把裸闭合 `</>` 按开标签栈补成正确闭合（`</parameter>` / `</invoke>`）。

    实测 2026-10-24（真实编辑型任务，5 次真跑里 3 次失败全栽在这）：
    模型把**每个**参数闭合都写成 `</｜｜DSML｜｜>`，剥掉标记只剩 `</>`。
    参数正则只认 `</parameter>` → 三个参数一个都没解析出来 → 工具报「缺必需参数 path/old/new」
    → 模型原地重发同样的东西 → 熔断中止。账面看是「模型不会用工具」，真因是闭合标签退化。

    为什么按栈修而不是再叠一条正则：跟着上下文，`</>` 该闭合谁是**唯一确定**的（最近一个没闭合的
    开标签），所以这是还原、不是猜。
    """
    text = text or ""
    out, stack, pos = [], [], 0
    for m in _TAG_OR_BARE.finditer(text):
        out.append(text[pos:m.start()])
        out.append(_tag_step(m.group(0), m.group("open"), m.group("close"), stack))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def extract_tool_calls(text):
    """从模型文本提取 <tool_call> 块并 json 解析。返回 (calls, bad_chunks)。
    calls: list[{"name","args","raw"}]。bad_chunks: list[str] 无法解析的原文。"""
    calls, bad = [], []
    text = _normalize_dsml(text)
    for m in TOOL_CALL_RE.finditer(text or ""):
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError("tool_call 须为 JSON 对象")
            calls.append({"name": str(obj.get("name", "")), "args": obj.get("args", {}),
                          "raw": raw})
        except Exception as e:  # noqa
            bad.append(f"<tool_call> 无法解析({type(e).__name__}: {e}): {raw[:160]}")
    if not calls:                      # 回退: 兼容模型自发 XML 工具调用
        calls = _extract_xml_calls(text)
    return calls, bad


def _looks_like_tool_call(text):
    """文本里有没有「想调工具」的痕迹（哪怕格式已经坏掉）。

    这个判断专治一种最贵的误判：把「解析失败」当成「模型收尾」。
    两者后果完全不同 —— 前者该回喂格式纠正，后者才该跑 DoD。

    必须走 _normalize_dsml，不能只剥标记：DSML 退化时原文是
    `<｜｜DSML｜｜ invoke name="x">`，只剥标记剩 `< invoke`（中间有空格），
    判 `<invoke` 就是 False —— 坏文本被当成收尾，直接拿去跑 DoD，
    于是报「达到 max_iterations 仍未完成」而产物其实是对的。
    这正是 2026-10-29 那次 enterprise_decision 假红的真因。
    """
    t = _normalize_dsml(text)
    return "<invoke" in t or "<parameter" in t


def _reject_bad_format(content, history, trace, turn, n):
    """「想调工具但格式坏」→ 回喂格式纠正，不当收尾。返回 (新计数, 需终止时的 result)。

    为什么不直接算 DoD 失败：那会把「模型其实在干活，只是标签写坏了」记成
    「任务没做对」—— 事后看账面是能力不足，真因是解析/协议。这条规矩是从一次
    真实编辑型任务上 0/3、且三次账面原因全都指向 DoD 才定下来的。
    """
    n += 1
    history.append({"role": "assistant", "content": content})
    history.append({"role": "user", "content": FORMAT_FIX_HINT})
    if trace is not None:
        trace.append({"turn": turn, "stage": "tool_format_error", "n": n,
                      "raw": content[:400]})
    if n >= MAX_FORMAT_FAILURES:
        return n, {"ok": False, "verify_passed": False,
                   "reason": "工具调用格式连续 %d 次不可解析（模型在发调用，但标签结构坏），"
                             "已中止：这不是任务难度问题，是输出协议问题" % n}
    return n, None


def _xml_args(body):
    """把 <parameter name="k" ...>v</parameter> 解析成 args dict。string="false" → JSON 还原类型。"""
    args = {}
    for pm in XML_PARAM_RE.finditer(body or ""):
        k, attrs, v = pm.group(1), (pm.group(2) or ""), (pm.group(3) or "")
        k = k.strip()
        if not k or any(c in k for c in "<>=\"' \t\n"):     # 名字被糊坏 → 丢掉，别污染入参
            continue
        v = v.strip()
        if 'string="false"' in attrs:
            try:
                v = json.loads(v)
            except Exception:  # noqa  # fail-open: tool_call 参数解析失败就当字符串
                pass
        args[k] = v
    return args


def _extract_xml_calls(text):
    """解析 <invoke name="tool">...</invoke> 块。

    闭合容错分两层：`</｜｜DSML｜｜>` 这类整段退化由 _repair_bare_close 在归一化时按栈还原
    （无损、无歧义，故不额外回喂提示）；这里保留 `</>` 作兜底，防开标签缺失等边角。
    """
    calls = []
    for m in XML_INVOKE_RE.finditer(text or ""):
        calls.append({"name": m.group(1), "args": _xml_args(m.group(2)),
                      "raw": m.group(0)[:200]})
    return calls


def _trunc_obs(text, cap=MAX_USER_OBS_CHARS):
    text = text or ""
    return text if len(text) <= cap else "[obs 超长截断 ...] " + text[-cap:]


    # 宪法例外: 格式化单个工具回执；status/exit_code/截断三件事拆开会分散到三处，改一处忘一处（P2 拆门面一并处理）
def _fmt_result(name, res):
    """把单个工具返回 dict 格式化为 <tool_result> 文本(前缀状态/exit code, 后缀 exit_code)。"""
    st = res.get("status", "ok")
    if st != "ok":
        reason = res.get("reason") or res.get("output") or str(res)
        return (f"<tool_result name={name} status=error>\n{_trunc_obs(str(reason))}\n"
                "</tool_result>")
    code = res.get("exit_code")
    if "exit_code" in res and code is not None:
        body = (res.get("output") or
                f"[{name} 完成 exit_code={code}]")
        suffix = f"\n[exit code {code}]"
    else:
        body = (res.get("content") or res.get("msg") or
                f"[{name} 完成: " + json.dumps({k: v for k, v in res.items()
                                                if k not in ("status",)}, ensure_ascii=False) + "]")
        suffix = ""
    if res.get("timeout"):
        body = "(超时, 哨兵 124)\n" + body
    return f"<tool_result name={name} status=ok>\n{_trunc_obs(body)}\n{suffix}\n</tool_result>"


# 每个工具的必需参数：缺了就回"缺参数"，别让它冒泡成底层 TypeError。
# 为什么：模型（尤其 DeepSeek 系）偶尔会发不带 path 的 read_file，工具抛
# "TypeError: _path_normpath: path should be string..." —— 这条错**看不出该补什么**，
# 实测模型会原地重发同一个坏调用，白烧 2~3 轮。回一句"缺 path，请带上 path 重发"就能纠偏。
_TOOL_REQUIRED = {
    "run": ("cmd",),
    "read_file": ("path",),
    "grep": ("pattern",),
    "write": ("path", "content"),
    "edit": ("path", "old", "new"),
    "verify_cmd": ("cmd",),
}


    # 宪法例外: 工具分发器；表驱动会丢掉每个工具各自的入参归一化差异（path/roots/超时），P2 一并处理
def _read_root(cwd):
    """只读工具的边界：项目根，不是写白名单。

    写白名单（roots）只该约束 write / edit：那几个工具会改磁盘，越界就是事故。
    read_file / grep 只是看，把写白名单套在它们身上，模型就看不到依赖的真实签名
    （本轮实测：想 grep foundations/data/scoped.py 确认 ScopedDB 的参数，被判越界，
    于是只能照着猜，改出来的东西缺参数反复不过 verify）。

    边界仍然要有，只是换成项目根：往上找到带 .git / pyproject.toml / package.json
    的那层。找不到就退回 cwd，与旧行为一致。
    """
    try:
        cur = os.path.abspath(str(cwd or "."))
    except Exception:  # noqa  # fail-open: 路径怪就用当前目录
        return None
    for _ in range(12):
        for marker in (".git", "pyproject.toml", "package.json", "setup.py"):
            if os.path.exists(os.path.join(cur, marker)):
                return [os.path.realpath(cur)]
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def execute_tool(name, args, roots, cwd):
    """按名分发到 tools.* 真执行。返回工具 dict。未知工具/越界/缺参 → status error。"""
    args = dict(args or {})
    miss = [k for k in _TOOL_REQUIRED.get(name, ()) if args.get(k) in (None, "")]
    if miss:
        return {"status": "error",
                "reason": ("%s 缺必需参数 %s（本次调用未执行）。请带上这些参数重发；"
                           "read_file 还要给行范围 a/b（如 a=1, b=120）。"
                           % (name, "/".join(miss)))}
    # 读与写用两套边界：读用项目根（看得见依赖），写用白名单（改不了别处）。
    read_roots = _read_root(cwd)
    if "path" in args and roots:
        # path 越界由各工具内 ensure_inside 拒绝, 这里只需透传 roots
        args["roots"] = roots
    try:
        if name == "run":
            return tools.run(args.get("cmd", ""), cwd=args.get("cwd") or cwd,
                             timeout=args.get("timeout", tools.DEFAULT_TIMEOUT))
        if name == "read_file":
            return tools.read_file(args.get("path"), args.get("a"), args.get("b"), read_roots)
        if name == "grep":
            return tools.grep(args.get("pattern"), args.get("path"), read_roots)
        if name == "write":
            return tools.write(args.get("path"), args.get("content", ""), roots)
        if name == "edit":
            return tools.edit(args.get("path"), args.get("old", ""),
                              args.get("new", ""), roots,
                              dry_run=args.get("dry_run", False))
        if name == "verify_cmd":
            return tools.verify_cmd(args.get("cmd", ""), cwd=args.get("cwd") or cwd)
        return {"status": "error", "reason": f"未知工具: {name}"}
    except Exception as e:  # noqa
        return {"status": "error", "reason": f"工具执行异常: {type(e).__name__}: {e}"}


    # 宪法例外: 按项目特征探测测试命令，纯 if 链；拆开要把探测结果反复传递，没有可读性收益
def detect_test_cmd(cwd):
    """找项目**自带的测试命令**，当零配置 DoD 用。

    为什么：DoD 是"能编译"只能挡住语法错，挡不住逻辑错；真正的验收是项目自己的测试。
    最简单又能拿最高能力的做法，就是把"跑测试"设成默认验收（写码必须真跑过、绿了才算完成）。
    返回找到的命令；找了不给出 None（由调用方退化成"能编译"）。
    """
    cwd = cwd or "."
    try:
        # Python: 有 tests/ 或 test_*.py → pytest 全跑，-x 一红就停（防长尾拖死）
        if os.path.isdir(os.path.join(cwd, "tests")) or \
                any(n.startswith("test_") and n.endswith(".py") for n in os.listdir(cwd)):
            # DoD 必须跑在 harness 自己的解释器里：PATH 上的 python 可能是另一个环境（实测落在 3.14 无 pytest）
            return '"%s" -m pytest -q -x' % sys.executable
        # Node: package.json 里真写了 test 脚本才用（默认的 echo 报错脚本不算）
        pkg = os.path.join(cwd, "package.json")
        if os.path.isfile(pkg):
            import json as _json
            with open(pkg, encoding="utf-8") as f:
                scripts = (_json.load(f) or {}).get("scripts") or {}
            t = str(scripts.get("test") or "")
            if t and "no test specified" not in t:
                return "npm test --silent"
    except Exception:  # fail-open: detect_test_cmd 里出错就返回空值，调用方按缺失处理
        return None
    return None


def _recite(task, verify_cmd):
    """T2 recitation：把目标与 DoD 复述到上下文**尾部**（Manus 的 todo 机制）。

    为什么：长上下文的"lost-in-the-middle"会让模型在多轮之后偏离目标；每轮回喂时把
    目标/验收标准重念一遍，等于把全局计划推进模型的近期注意力里。这里刻意只念一行，
    且**只追加不改写历史**（前缀稳定 → KV-cache 命中率不塌，见 P2 成本账本口径）。
    """
    first = (task or "").strip().splitlines()[0] if (task or "").strip() else "(未给任务描述)"
    if len(first) > 200:
        first = first[:200] + "…"
    dod = (verify_cmd or "").strip() or "（未指定：按 harness 兜底——写出的 .py 必须真改动且能编译）"
    return f"\n\n[harness·复述] 目标: {first} ／ DoD: {dod}"


    # 宪法例外: 6 个入参是循环状态的显式传递；打包成对象会让写时副本语义变隐蔽（宁可参数多）
def execute_calls(calls, roots, cwd, files_written, tool_log, turn):
    """真执行一轮里所有 tool_call(顺序), 收集观测文本; 记录已写文件与轨迹。"""
    parts = []
    for i, call in enumerate(calls, 1):
        name, args = call["name"], call["args"]
        # 解析失败的块: 转成错误观测回喂, 不让模型静默失败
        if call.get("parse_error"):
            parts.append(_fmt_result(name or "?",
                                     {"status": "error",
                                      "reason": call["parse_error"]}))
            continue
        res = execute_tool(name, args, roots, cwd)
        # 记录成功写盘文件 —— 只认**真改动**：write，或 edit 且 dry_run=false。
        # 曾经的写法是"任何带 path 的成功结果"，于是 read_file 也被算成写过文件，
        # 让下面的谎报守卫（收尾但没真改动 → 不许判完成）完全失效。
        # edit 默认落盘（dry_run 缺省=False），只有显式 dry_run=true 才是预览、不算改动
        _mutating = (name == "write") or (name == "edit" and tools._as_bool(args.get("dry_run"), False) is False)
        if _mutating and res.get("status") == "ok" and res.get("path"):
            files_written.add(os.path.normpath(res["path"]))
        # 记录 edit dry-run 通过但未落盘 → 提示模型落盘
        if name == "edit" and res.get("status") == "ok" and res.get("dry_run"):
            res["msg"] = (res.get("msg", "") + " | dry-run 通过: 如需落盘请把 dry_run 设为 false 重发")
        tool_log.append({"turn": turn, "tool": name, "status": res.get("status"),
                         "arg": str(args)[:120]})
        parts.append(_fmt_result(name, res))
    return "\n".join(parts)


# run_loop 主函数体在下方由分片续写填入
    # 宪法例外: 真跑闭环主循环（320 行/cc96）。拆分须与状态机、trace、回喂顺序同步重构，已排 P2 拆门面
_ROOT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
                 "package.json", "go.mod", "Cargo.toml", ".git")


def _find_project_root(path):
    """从 path 往上找项目根（含 pyproject.toml / .git 那类标识的目录）。

    为什么要找：run_loop 的 cwd 默认取目标文件所在目录，而 DoD 命令几乎都是相对
    项目根写的。实测 `python -m domains.enterprise_decision.wiring` 在
    domains/enterprise_decision/ 里跑会报 ModuleNotFoundError: No module named 'domains'，
    在项目根跑才 OK。于是产物对了、DoD 却红，调用方看到 verify_passed=false 只能手工复验。

    两条边界，都是实测踩出来的：
      一、不跨进用户家目录。tempfile.mkdtemp() 造在 Temp 下，上溯会走到 C:/Users/<user>，
          而家目录常有 .git 或 package.json，于是临时目录被认成"项目子目录"，换根后
          pytest 收集不到测试，把一次真失败报成了别的失败（tests/test_code_runloop.py
          里 test_project_test_failing_blocks_completion 就是这么红的）。
      二、找不到标识就返回 None，让调用方退回原目录，不猜。
    """
    if not path:
        return None
    home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    cur = os.path.abspath(path if os.path.isdir(path) else os.path.dirname(path))
    for _ in range(12):          # 深到 12 层足够，再往上就是盘符了
        if os.path.normcase(cur) == home:
            return None
        for mk in _ROOT_MARKERS:
            if os.path.exists(os.path.join(cur, mk)):
                return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


# 只有模块式命令依赖 sys.path 上的项目根。相对路径与 py_compile 这类在哪个目录跑都一样，
# 对它们换目录只会改变语义（临时目录的 pytest 被挪到家目录去跑，收集不到测试）。
_MODULE_FORM = re.compile(r'(^|\s)-m\s+[\w./]+|python\S*\s+-c\s+["\']?\s*import', re.I)


def _run_verify(cmd, cwd, target_path=None):
    """跑 DoD：先在 cwd 跑，失败且是模块式命令时在项目根重试一次。

    先 cwd 再根，不是直接换根：有些 DoD 是相对目标目录写的（py_compile "同名文件.py"），
    直接换根会让这类命令失效。两次都失败才是真失败。
    """
    v = tools.verify_cmd(cmd, cwd=cwd)
    if v.get("ok"):
        return v
    if not _MODULE_FORM.search(str(cmd)):
        return v
    root = _find_project_root(target_path or cwd)
    if root and os.path.normcase(root) != os.path.normcase(os.path.abspath(cwd)):
        v2 = tools.verify_cmd(cmd, cwd=root)
        # 换了目录这件事要留给调用方看见，否则事后分不清"本来就绿"与"换目录才绿"
        v2["verify_cwd_shifted"] = root
        if v2.get("ok"):
            return v2
        # 两次都失败：报项目根那次，它更接近 DoD 想表达的语义
        return v2
    return v


def run_loop(task, *, target_path=None, output_dir=None, verify=None,
             language="python", llm_generate=None, max_iterations=8,
             temp=0.3, max_tokens=32000, config_path=None,
             system_prompt=None, trace=None, write_root=None,
             allow_skip_verify=False, max_verify_failures=4,
             require_write=None):
    """设计 §2/§3 主循环。llm_generate 若省略 → 读 config 的纯 stdlib generate。
    trace(可选 list) 每轮记录 {turn, content[:120], n_calls, obs[:200]} 供回读/冒烟。"""
    from llm import generate as _std_generate  # lazy: fake 注入时零网络

    gen_fn = llm_generate or (lambda msgs: _std_generate(
        msgs, temp=temp, max_tokens=max_tokens, config_path=config_path))

    # require_write：给了目标文件 = 这活是要改文件的，默认要求真改动；
    # 纯分析任务（如"看一遍这份代码"）传 require_write=False。None → 按有没有 target_path 自动。
    require_write = bool(target_path) if require_write is None else bool(require_write)

    cwd = output_dir or (os.path.dirname(os.path.abspath(target_path))
                         if target_path else os.getcwd())
    os.makedirs(cwd, exist_ok=True)
    roots = tools.resolve_roots(write_root or output_dir or cwd)

    verify_cmd = (verify or "").strip()
    verify_defaulted = False
    verify_source = "给定" if verify_cmd else ""
    if not verify_cmd:
        # 零配置时**优先跑项目自带测试**（真实验收），没有测试才退化成"目标文件能编译"。
        detect_dir = os.path.dirname(os.path.abspath(target_path)) if target_path else cwd
        found = detect_test_cmd(detect_dir)
        if found:
            verify_cmd, verify_defaulted, verify_source = found, True, "探测到项目自带测试"
        elif target_path and str(target_path).endswith(".py"):
            verify_cmd = '"%s" -m py_compile "%s"' % (sys.executable, target_path)
            verify_defaulted, verify_source = True, "退化为目标文件能编译"

    sys_p = system_prompt or build_system_prompt()
    try:
        _rr = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if _rr not in sys.path:
            sys.path.insert(0, _rr)
        import code_project_rules as _cpr
        import project_root as _pr
        _res = _pr.resolve(write_root=write_root, path=target_path)   # 根单点解析（显式 > git 边界 > none）
        sys_p = sys_p + "\n\n" + _cpr.for_root(_res.get("root"))
    except Exception as _e:  # 加载器坏了 → 显式写进提示，不静默
        sys_p = sys_p + "\n\n[项目规则加载失败：%s: %s]" % (type(_e).__name__, _e)
    task_msg = build_task_message(task, target_path=target_path,
                                  white_root=roots[0] if roots else cwd,
                                  verify_cmd=verify_cmd, language=language)
    history = [{"role": "system", "content": sys_p},
               {"role": "user", "content": task_msg}]
    if trace is not None:
        trace.append({"turn": 0, "stage": "task", "verify_source": verify_source or "无",
                      "require_write": require_write,
                      "target_path": target_path, "verify_cmd": verify_cmd or None,
                      "roots": roots, "output_dir": cwd})

    files_written = set()
    tool_log = []
    verify_failures = 0
    verify_no_write = 0
    format_failures = 0
    last_model_text = ""
    result = {"ok": False, "reason": "未进入循环"}

    def _u(text):
        """所有回喂给模型的 user 消息统一出口 → 一律以复述收尾(T2)。"""
        history.append({"role": "user", "content": text + _recite(task, verify_cmd)})

    # 🔴 读预算（2026-09-21 加）：连着 READ_BUDGET 轮只 read_file/grep、一个文件没改，
    #    就不再执行只读工具 —— 逼它动手或如实收尾。实测代价：派"改一个函数的一处调用"的小活，
    #    连派 4 次、每次 6 轮全烧在读文件上、零改动；需求里已写明"不要再读"也拦不住。
    READ_TOOLS = {"read_file", "grep"}
    READ_BUDGET = 2
    read_only_streak = 0
    read_budget_blocked = 0

    # 重复熔断：模型工具调用格式退化时会把同一个"缺参数"错原样重发，
    # 实测可烧满 30 轮、零落盘。连续 3 次同一错误即中止，并把原始输出留在 trace 里当证据。
    last_err_sig = None
    err_streak = 0
    # 成本台账（P1-3）：逐轮累加模型 usage，供 bench 算 token/题与 $/题。
    # 缺字段/脏值一律按 0 记（不猜），最终在 result["usage"] 如实报出，调用方据此判断可信度。
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for turn in range(1, int(max_iterations) + 1):
        if trace is not None:
            trace.append({"turn": turn, "stage": "generate_start",
                          "hist_msgs": len(history)})
        resp = gen_fn(history)
        _usage = resp.get("usage") or {}
        for _k in usage_total:
            try:
                usage_total[_k] += int(_usage.get(_k) or 0)
            except (TypeError, ValueError):  # fail-open: 通道返回脏 usage 值时按 0 记，成本栏少算不吃掉整轮结果
                pass
        if resp.get("error"):
            _u(f"[harness] 模型调用失败: {resp['error']} —— 若为 API/网络问题请换 llm 通道后重试")
            if trace is not None:
                trace.append({"turn": turn, "stage": "llm_error", "error": resp["error"]})
            result = {"ok": False, "reason": f"模型调用失败: {resp['error']}",
                      "llm_error": resp["error"]}
            break

        content = resp.get("content") or ""
        last_model_text = content
        calls, bad = extract_tool_calls(content)

        # 有 tool_call 块 → 真执行真回喂
        if calls or bad:
            names = {str(c.get("name") or "") for c in calls}
            only_reads = bool(calls) and names <= READ_TOOLS

            # 读预算用尽：不再执行只读工具，直接要它落盘（否则轮次全被读文件吃光）
            if only_reads and read_only_streak >= READ_BUDGET:
                read_budget_blocked += 1
                history.append({"role": "assistant", "content": content})
                _u("[harness] 读预算已用尽（已连续 %d 轮只读、零文件改动）：本轮**不再执行** %s。"
                   "请立刻用 edit/write 落盘改动；若确实无需改动，直接说明原因收尾 —— 不许再读文件。"
                   % (read_only_streak, "/".join(sorted(names))))
                if trace is not None:
                    trace.append({"turn": turn, "stage": "read_budget_blocked", "n": len(calls),
                                  "names": sorted(names)})
                continue

            for b in bad:
                history.append({"role": "user", "content": b})
            _before_written = len(files_written)
            obs = execute_calls(calls, roots, cwd, files_written, tool_log, turn)

            # 同一"缺参数"错误连续重复 → 熔断，别把 30 轮全烧在格式退化上
            _sig = obs[:80] if ("缺必需参数" in obs or "缺参数" in obs) else None
            if _sig and _sig == last_err_sig:
                err_streak += 1
            elif _sig:
                last_err_sig, err_streak = _sig, 1
            else:
                last_err_sig, err_streak = None, 0
            if err_streak >= 3:
                _u("[harness] 同一工具参数缺失已连续 %d 次（疑似工具调用格式退化），中止本块。" % err_streak)
                if trace is not None:
                    trace.append({"turn": turn, "stage": "repeat_guard_abort",
                                  "sig": last_err_sig, "raw_content": content[:4000]})
                result = {"ok": False, "repeat_guard": True,
                          "reason": "连续 %d 次同一工具参数缺失，已中止（疑似模型工具调用格式退化）；"
                                    "最后一次原始输出见 trace.repeat_guard_abort.raw_content" % err_streak}
                break

            history.append({"role": "assistant", "content": content})
            _u(obs if obs.strip() else "[harness] 本轮无有效工具观测, 请继续/收尾")
            if trace is not None:
                trace.append({"turn": turn, "stage": "tool_calls", "n": len(calls),
                              "content": content[:120], "obs": obs[:300]})
            if len(files_written) > _before_written:
                read_only_streak = 0                      # 真改了 → 预算重置
            elif only_reads:
                read_only_streak += 1
                if read_only_streak >= READ_BUDGET:
                    _u("[harness] 提醒：已经连续 %d 轮只读、零改动。下一轮起只读工具会被拒绝，"
                       "请直接 edit/write 落盘。" % read_only_streak)
            continue

        # 无 tool_call → 收尾候选; FinishReasonLength → prefill 续写
        if resp.get("finish_reason") == "length":
            history.append({"role": "assistant", "content": content})
            _u(FINISH_REASON_LENGTH_HINT)
            if trace is not None:
                trace.append({"turn": turn, "stage": "finish_length", "len": len(content)})
            continue
        if not content.strip():
            _u("[harness] 模型返回空文本, 请重试(输出 tool_call 或收尾总结)")
            continue
        # 想调工具但格式坏 ≠ 收尾：必须回喂纠正，不能拿去跑 DoD。
        # （实测：这条被漏掉时，4 轮「零改动 + 校验失败」被记成 DoD 连续失败，真因是解析。）
        if _looks_like_tool_call(content):
            format_failures, _fmt_result = _reject_bad_format(
                content, history, trace, turn, format_failures)
            if _fmt_result:
                result = _fmt_result
                break
            continue

        # 完成判据: DoD 硬校验
        history.append({"role": "assistant", "content": content})
        if not verify_cmd:
            # 零配置兜底 DoD：任务没给 verify 也没给 path 时，用"写出来的 .py 必须能编译"当最低标准。
            # 刻意不用"无 DoD 也放行"：那等于让模型自称完成（我们的红线：静默/假绿不许）。
            pys = sorted(f for f in files_written if str(f).endswith(".py"))
            if pys:
                verify_cmd = " && ".join('%s -m py_compile "%s"' % (sys.executable, f) for f in pys)
                verify_defaulted = True
                verify_source = "退化为写出的文件能编译"
                if trace is not None:
                    trace.append({"turn": turn, "stage": "verify_defaulted_from_writes",
                                  "files": pys})
        if not verify_cmd:
            if allow_skip_verify:
                result = {"ok": True, "summary": content,
                          "verify_passed": None, "verify_cmd": None,
                          "verify_output": "", "verify_defaulted": verify_defaulted}
                break
            result = {"ok": False, "reason": "模型已收尾但无 DoD verify_cmd, 且 allow_skip_verify=False",
                      "summary": content, "verify_passed": None,
                      "verify_cmd": None, "verify_defaulted": verify_defaulted}
            break
        v = _run_verify(verify_cmd, cwd, target_path)
        # P1-2 谎报守卫: 若 verify 是自动 py_compile(defaulted) 且整轮没真写过任何文件
        # (纯 read/dry-run edit 后收尾), 判定为"未真改动"的疑似谎报 → 回喂强制真写, 不直接 ok。
        if v["ok"] and verify_defaulted and not files_written:
            if trace is not None:
                trace.append({"turn": turn, "stage": "verify_no_write",
                              "files_written": sorted(files_written)})
            _u("[harness] 校验通过但本轮未真写任何文件(仅 read/dry-run)。"
               "本任务要求改动文件, 请用 edit/write 真正落盘改动后再收尾"
               "(edit 默认就落盘; 只有你显式传 dry_run=true 时才是预览); "
               "若确无文件需改动请说明, 否则视为未完成。")
            continue
        if v["ok"] and require_write and not files_written:
            # 假绿守卫（调用方给了 DoD 的场合）：DoD 绿 ≠ 干过活。实测有一次模型只读不写、
            # DoD（现成的测试）本来就绿 → 返回 ok=true 却没改任何文件，等于骗调用方。
            if trace is not None:
                trace.append({"turn": turn, "stage": "verify_ok_no_write",
                              "files_written": sorted(files_written)})
            verify_no_write += 1
            _u("[harness] DoD 过了，但整轮没有任何文件改动 —— 本任务给了目标文件，必须真改。"
               "请用 write 新建 / edit 真正落盘后再收尾（edit 默认落盘，别传 dry_run 或传 false）；"
               "若确实无需改动请说明理由。")
            if verify_no_write >= 3:
                result = {"ok": False, "summary": content,
                          "reason": "连续 3 次收尾都没有任何文件改动（要求改动却没改）",
                          "verify_passed": True, "verify_cmd": verify_cmd,
                          "verify_output": v.get("output", "")}
                break
            continue
        if v["ok"]:
            result = {"ok": True, "summary": content, "verify_passed": True,
                      "verify_cmd": verify_cmd, "verify_defaulted": verify_defaulted,
                      "verify_output": v.get("output", "")}
            if trace is not None:
                trace.append({"turn": turn, "stage": "verify_ok",
                              "verify_cmd": verify_cmd})
            break
        # DoD 失败 → 真实输出当新消息回喂重来
        verify_failures += 1
        fail_out = (v.get("output") or f"exit_code={v.get('exit_code')} "
                    f"timeout={v.get('timeout')}").strip()
        # H2（2026-10-15）：失败要点结构化（失败测试名 / 断言行 / 异常行）放在原文之前，
        # 让下一轮的提示里有"可直接改的点"，而不是只丢一大段 stdout。
        # 实测依据：max_iter=6 时 B 组失败几乎全是"轮数内没收敛"，其中 21 题 one-shot 一次就对
        # ⇒ 反馈质量比轮数更可能成为下一层瓶颈（基线：max_iter=8 时 156/164 = 95.12%）。
        # 2026-10-16 刀 7：抽取规则收敛到 render_digest 一处（与审查/覆盖率等消费点同口径），
        # 本文件不再各写一份 —— 模块缺失时如实降级为"无要点"，不挡主流程。
        _digest = render_digest.digest(v.get("output") or "") if render_digest else ""
        _u(f"[DoD 校验失败 #{verify_failures}] 命令: {verify_cmd}\n"
           + (_digest + "\n" if _digest else "")
           + f"真实输出:\n{fail_out}\n\n请依据以上真实失败修改到绿(别假装通过)。")
        if trace is not None:
            trace.append({"turn": turn, "stage": "verify_fail", "n": verify_failures,
                          "out": fail_out[:200],
                          # 走到这里的都是「这一轮没有工具调用」的收尾候选。
                          # 不带原文时，事后只能看到「4 次校验失败」，看不出模型到底说了什么 ——
                          # 2026-10-24 在真实编辑型任务上就吃了这个亏（零改动 + 4 次失败，原因不可见）。
                          "model_said": content[:400]})
        if verify_failures >= int(max_verify_failures):
            result = {"ok": False,
                      "reason": f"DoD 校验连续失败 {verify_failures} 次达到上限",
                      "verify_cmd": verify_cmd, "verify_output": fail_out,
                      "verify_passed": False}
            break
        continue

    if not result.get("ok"):
        # 注意：result 初始就带了 reason="未进入循环"，setdefault 永远命中不了它 ——
        # 所以这里要**覆盖**，否则跑到上限时会报"未进入循环"（实测误导过调用方）。
        if result.get("verify_passed") and not files_written:
            result["reason"] = ("DoD 已绿，但整轮没有任何文件改动 → 判未完成。"
                                "两种可能：①需求已由他人完成（那就直接复核，不必派活）；"
                                "②DoD 没覆盖新需求（那要换 DoD）。")
        elif not files_written and read_budget_blocked:
            result["reason"] = ("达到 max_iterations(%d) 仍未完成：全程零文件改动，"
                                "其中 %d 次只读工具被读预算拦下（连着 %d 轮只读就要动手，"
                                "拦下后模型仍未落盘）。这活对当前循环太重或需求没说清 —— 拆小或显式放宽轮数。"
                                % (int(max_iterations), read_budget_blocked, READ_BUDGET))
        elif (result.get("reason") or "").strip() in ("", "未进入循环"):
            result["reason"] = ("达到 max_iterations(%d) 仍未完成（最后一次 stage 见 trace）"
                                % int(max_iterations))
    # ── 到顶收尾：轮数用尽 ≠ 任务没完成 ────────────────────────────────
    # 实测动机（2026-10-16）：编辑型任务在 6 轮内真把目标文件改对了、DoD 事后跑是绿的，
    # 但当时直接判 ok=false（保守误报，方向与假绿相反）。这里补跑一次 DoD，绿了且
    # "确实改过文件 或 调用方未要求改动" 才判完成 —— require_write 的假绿守卫照旧生效。
    if not result.get("ok") and verify_cmd and not result.get("verify_passed"):
        _v_end = _run_verify(verify_cmd, cwd, target_path)
        if _v_end.get("ok") and (files_written or not require_write):
            result["ok"] = True
            result["verify_passed"] = True
            result["verify_output"] = _v_end.get("output", "")
            result["reason"] = ("轮数到顶(%d)，但收尾补跑的 DoD 已绿 → 按结果判完成"
                                "（注意：这是到顶后补验，不是循环内收敛）。" % int(max_iterations))
            if trace is not None:
                trace.append({"turn": min(turn, int(max_iterations)) if locals().get("turn") else 0,
                              "stage": "verify_ok_after_exhaust", "verify_cmd": verify_cmd})
        else:
            result["verify_passed"] = False
    result["ok"] = bool(result.get("ok"))
    result["turns_used"] = min(turn, int(max_iterations)) if locals().get("turn") else 0
    result["max_iterations"] = int(max_iterations)
    result["verify_failures"] = verify_failures
    result["format_failures"] = format_failures   # 工具调用格式坏了几次（与 DoD 失败分开记，别混账）
    result["verify_passed"] = result.get("verify_passed", False)
    result["files"] = sorted(files_written)
    result["changed"] = bool(files_written)      # ok=DoD 绿；changed=真改过文件，两者分开报
    result["require_write"] = require_write
    result["read_budget_blocked"] = read_budget_blocked
    result["tool_log"] = tool_log
    result["last_model_text"] = last_model_text
    result["output_dir"] = os.path.normpath(cwd)
    result["verify_source"] = verify_source or ("给定" if verify_cmd else "无")
    result["engine"] = "code-runloop.run_loop (agentic, 真跑反馈闭环)"
    result["usage"] = dict(usage_total)
    return result
