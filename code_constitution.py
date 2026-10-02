#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""code_constitution.py — 代码宪法加载/注入（单一真相源，fail-loud）。

真相源：docs/代码宪法.md 里 `<!-- CODE-CONSTITUTION:BEGIN -->` 与 `<!-- CODE-CONSTITUTION:END -->`
之间的正文。两条写码路径（engine_llm.PONYTAIL_SYSTEM 与 code-runloop 的 build_system_prompt）
都通过这里注入，代码里不另抄一份，避免漂。

设计取舍：
  * 缺文件 / 缺标记 / 正文为空 → **抛 CodeConstitutionError**，不返回空串（静默少注入一段是最坏的失败形态）。
  * `inject()` 幂等：已含正文则原样返回，重复调用不会塞两遍。
  * 读盘结果缓存；`reset_cache()` 供测试/改文档后重载。

用法：
    from code_constitution import inject
    SYS = inject(SYS_PROMPT)          # 追加宪法（幂等）
自检：
    python code_constitution.py --selftest
"""
import os
import sys

BEGIN = "<!-- CODE-CONSTITUTION:BEGIN -->"
END = "<!-- CODE-CONSTITUTION:END -->"
ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(ROOT, "docs", "代码宪法.md")
HEADER = "\n\n──── 代码宪法（docs/代码宪法.md，写码硬口径，逐条可判定）────\n"
# 体积预算：宪法自己不能变成新的复杂度。超预算即视为膨胀，由 verify 门判红。
MAX_CHARS = 6000

_cache = {}


class CodeConstitutionError(RuntimeError):
    """宪法缺失/损坏。调用方应当让人看到，别吞。"""


def constitution_path(path=None):
    return os.path.abspath(path or DEFAULT_PATH)


def constitution_text(path=None, use_cache=True):
    """取注入正文（标记之间），两端去空白。缺失即抛错。"""
    p = constitution_path(path)
    if use_cache and p in _cache:
        return _cache[p]
    try:
        with open(p, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError as e:
        raise CodeConstitutionError("代码宪法文件不存在: %s" % p) from e
    if BEGIN not in raw or END not in raw:
        raise CodeConstitutionError("代码宪法缺少注入标记(%s / %s): %s" % (BEGIN, END, p))
    body = raw.split(BEGIN, 1)[1].split(END, 1)[0].strip()
    if not body:
        raise CodeConstitutionError("代码宪法注入标记之间是空的: %s" % p)
    if len(body) > MAX_CHARS:
        raise CodeConstitutionError(
            "代码宪法注入正文 %d 字符，超过预算 %d —— 宪法自己膨胀了，先删再加" % (len(body), MAX_CHARS))
    if use_cache:
        _cache[p] = body
    return body


def inject(system_prompt, path=None):
    """把宪法追加到系统提示末尾；幂等（已含则不重复追加）。

    失败即抛 CodeConstitutionError —— 调用方要么让它冒出来，要么在提示里留下显式缺失标记，
    绝不允许"看起来注入过了但其实是空的"。
    """
    body = constitution_text(path)
    prompt = system_prompt or ""
    if body[:80] in prompt:                     # 已注入过（按正文开头判定）
        return prompt
    return prompt.rstrip() + HEADER + body + "\n"


def inject_or_mark(system_prompt, path=None):
    """给"不能因宪法缺失而整体崩溃"的调用点用：失败时在提示里留**显式**缺失标记（不是静默降级）。"""
    try:
        return inject(system_prompt, path)
    except CodeConstitutionError as e:
        return (system_prompt or "") + "\n\n[代码宪法未加载: %s —— 本次任务的写码口径缺少宪法约束]" % e


def reset_cache():
    _cache.clear()


def _selftest():
    ok = True

    def chk(name, cond, extra=""):
        nonlocal ok
        print("[%s] %s %s" % ("OK" if cond else "FAIL", name, extra))
        ok = ok and bool(cond)

    try:
        body = constitution_text()
        chk("宪法可取且非空", len(body) > 500, "%d 字符" % len(body))
        chk("正文带总纲", "极简是硬指标" in body)
        chk("条文数 ≥14", body.count("\n") > 20)
    except CodeConstitutionError as e:
        chk("宪法可取", False, str(e))
        return 1

    p1 = inject("SYS")
    chk("注入生效", "极简是硬指标" in p1 and p1.startswith("SYS"))
    chk("注入幂等", inject(p1) == p1)
    chk("注入不动原提示", p1.split(HEADER)[0] == "SYS")

    # 缺文件 → 必须抛错，不许静默
    try:
        constitution_text(path=os.path.join(ROOT, "_no_such_constitution.md"), use_cache=False)
        chk("缺文件必抛错", False, "竟然没抛")
    except CodeConstitutionError:
        chk("缺文件必抛错", True)

    # 缺标记 → 必须抛错
    tmp = os.path.join(ROOT, "_tmp_constitution_markerless.md")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("# 没有标记的文件\n")
        try:
            constitution_text(path=tmp, use_cache=False)
            chk("缺标记必抛错", False, "竟然没抛")
        except CodeConstitutionError:
            chk("缺标记必抛错", True)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    # inject_or_mark 的失败形态必须是"显式标记"，不是空
    marked = inject_or_mark("SYS", path=os.path.join(ROOT, "_no_such_constitution.md"))
    chk("缺失时留显式标记", "代码宪法未加载" in marked)

    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(constitution_text())
