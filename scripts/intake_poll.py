# -*- coding: utf-8 -*-
"""intake_poll.py — CodeAgent 接单线：领单 → 能力链/选链 → 执行 → 回写 Result（幂等）

为什么有它：派活不该每次现想"怎么调用 CodeAgent"，而 CodeAgent 自己有能力做内部编排（选链、跑能力序列）。
所以派活变成**一本队列**：派单方只写"要什么"，CodeAgent 侧自己领单、自己定能力序列、自己跑、自己回写。

队列：默认 `<仓库根>/docs/intake/tasks.md`；可用环境变量 `CODEAGENT_INTAKE_QUEUE` 或 `--queue` 覆盖。

任务块两种执行方式（优先级从上到下）：
  1. **显式能力链**：块里写一行
        能力链: [{"capability":"security.project","inputs":{"path":"."}}, {"capability":"deadcode.scan","inputs":{"path":"."}}]
     → 逐条调运行时跑（`get_runtime().run_capability`），`$task` / `$upstream_files` 会被替换。
       多能力任务（审计/测试/安全/审查串）用这个 —— 这是"CodeAgent 自己做编排"的正路。
  2. **规则层选链**（`chain_selector` 关键词命中 `chain_templates.json`）→ 落回 `code.runloop` 写码闭环。

用法：
    python scripts/intake_poll.py [--max 2] [--dry-run] [--fake-model smoke] [--queue <路径>]

纪律（写死在实现里）：
  · 只领 `Status: 待办`（无 Status 行时：有结果段视为已完成，否则算待办）
  · 领单先落 `Status: 进行中 <时间>` 再执行 —— 崩了也不会无限重领
  · 有锁文件（intake_poll.lock），同一时刻只跑一个，避免 15 分钟 cron 叠跑
  · **不做 git push / 不碰远端**
  · 任一步失败都如实写进结果段，状态写「阻塞」，不假装完成

退出码：0 = 无单可领或全成功；1 = 有单失败
创建：2026-10-05
"""
import argparse
import importlib.util
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
QUEUE = os.environ.get("CODEAGENT_INTAKE_QUEUE") or os.path.join(REPO, "docs", "intake", "tasks.md")
LOCK = os.path.join(HERE, "intake_poll.lock")
CHAIN_DIR = os.path.join(REPO, "agents", "dispatch", "code-dispatch")


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── 队列读写 ─────────────────────────────────────────────
def split_tasks(text):
    idx = [m.start() for m in re.finditer(r"(?m)^## Task-ID:\s*(.+)$", text)]
    out = []
    for i, s in enumerate(idx):
        e = idx[i + 1] if i + 1 < len(idx) else len(text)
        blk = text[s:e]
        out.append((re.match(r"## Task-ID:\s*(.+)", blk).group(1).strip(), blk))
    return out


def status_of(blk):
    m = re.search(r"(?m)^Status:\s*(.+)$", blk)
    if m:
        return m.group(1).strip()
    return "已完成" if re.search(r"(?m)^###\s*Result", blk) else "待办"


def field_of(blk, label):
    m = re.search(r"(?m)^\s*(?:\d+\.\s*)?%s[:：]\s*(.+)$" % re.escape(label), blk)
    if not m:
        return None
    v = m.group(1).strip()
    m2 = re.search(r"`([^`]+)`", v)          # 优先取反引号里的纯净值
    if m2:
        return m2.group(1).strip()
    return re.split(r"[（(]", v)[0].strip().strip("`").strip()


def chain_of(blk):
    """块里声明的能力链（JSON 数组，可跨行到行尾）。"""
    m = re.search(r"(?m)^\s*能力链[:：]\s*(\[.*?\])[ \t]*$", blk, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(1))
        return v if isinstance(v, list) and v else None
    except Exception:
        return None


def claim(blk):
    if re.search(r"(?m)^Status:", blk):
        return re.sub(r"(?m)^Status:.*$", "Status: 进行中 %s" % stamp(), blk, count=1)
    return re.sub(r"(?m)^(From:.*)$", r"\1\nStatus: 进行中 %s" % stamp(), blk, count=1)


# ── 选链（规则层，确定性，不烧模型）──────────────────────
def pick_chain(task):
    try:
        if CHAIN_DIR not in sys.path:
            sys.path.insert(0, CHAIN_DIR)
        import chain_selector as cs
        best, bs = None, 0
        for tpl in cs.load_templates():
            s = cs.score_template(task, tpl).get("score", 0)
            if s > bs:
                best, bs = tpl, s
        if best:
            return {"name": best["name"], "chain": best["chain"], "score": bs, "src": "chain_selector"}
    except Exception as e:
        return {"name": None, "chain": [], "score": 0, "src": "chain_selector 失败: %r" % e}
    return {"name": None, "chain": [], "score": 0, "src": "无命中模板（落回默认 runloop）"}


# ── 执行 A：显式能力链（走运行时）────────────────────────
def _load_runtime():
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    import agent_runtime
    return agent_runtime.get_runtime()


def _brief(r, limit=420):
    if not isinstance(r, dict):
        return str(r)[:limit]
    d = r.get("data") if isinstance(r.get("data"), dict) else {}
    keep = {}
    for k in list(d)[:8]:
        v = d[k]
        keep[k] = v if isinstance(v, (int, float, bool, type(None))) else str(v)[:200]
    return json.dumps({"ok": r.get("ok"), "error": r.get("error"), "data": keep},
                      ensure_ascii=False)[:limit]


def run_chain(steps, task, fake_model=None):
    t0 = time.time()
    try:
        rt = _load_runtime()
    except Exception as e:
        return {"ok": False, "steps": [], "sec": 0, "err": "运行时加载失败: %r" % e,
                "cmd": "in-process: run_chain（未起）"}
    out, upstream, allok = [], [], True
    for s in steps:
        cap = s if isinstance(s, str) else s.get("capability")
        ins = {} if isinstance(s, str) else dict(s.get("inputs") or {})
        for k, v in list(ins.items()):
            if v == "$task":
                ins[k] = task
            elif v == "$upstream_files":
                ins[k] = upstream
        if fake_model and str(cap).startswith("code."):
            ins.setdefault("fake_model", fake_model)
        try:
            r = rt.run_capability(cap, **ins)
        except Exception as e:
            r = {"ok": False, "error": repr(e)[:300]}
        ok = bool(r.get("ok"))
        allok = allok and ok
        out.append({"capability": cap, "ok": ok, "brief": _brief(r)})
        d = r.get("data") if isinstance(r.get("data"), dict) else {}
        if isinstance(d.get("files"), list):
            upstream = list(d["files"])
    return {"ok": allok, "steps": out, "sec": round(time.time() - t0, 1),
            "cmd": "in-process: run_chain(%d 步)" % len(steps)}


# ── 执行 B：落回 code.runloop（写码闭环）─────────────────
def _load_runloop():
    p = os.path.join(REPO, "agents", "code", "code-runloop", "main.py")
    d = os.path.dirname(p)
    if d not in sys.path:
        sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("code_runloop_main", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_runloop(task, path, verify, fake_model, lang="python", max_iter=8):
    t0 = time.time()
    try:
        m = _load_runloop()
        ag = m.agent
        ag.load()
        r = ag.run(_capability="code.runloop", task=task, path=path, output_dir=None,
                   verify=verify, language=lang, max_iterations=max_iter, fake_model=fake_model)
    except Exception as e:
        return {"ok": False, "steps": [], "err": repr(e)[:300], "sec": round(time.time() - t0, 1),
                "cmd": "in-process: agent.run(code.runloop)"}
    return {"ok": bool(r.get("ok")),
            "steps": [{"capability": "code.runloop", "ok": bool(r.get("ok")), "brief": _brief(r)}],
            "sec": round(time.time() - t0, 1), "err": str(r.get("error") or "")[:300],
            "cmd": "in-process: agent.run(code.runloop, path=%r, verify=%r)" % (path, verify)}


def result_block(res, chain):
    lines = ["", "### Result", "",
             "- 时间：%s" % stamp(),
             "- 执行方式：%s" % res.get("cmd"),
             "- 选中链：%s（%s，得分 %s）" % (chain.get("name"), chain.get("src"), chain.get("score")),
             "- 结果：%s" % ("成功 ✓" if res.get("ok") else "失败/部分失败 ✗"),
             "- 耗时：%ss" % res.get("sec")]
    for st in res.get("steps") or []:
        lines.append("- `%s` → %s：%s" % (st["capability"], "ok" if st["ok"] else "FAIL", st["brief"]))
    if res.get("err"):
        lines.append("- 失败信息：%s" % res["err"])
    lines += ["- 备注：本单由 intake_poll.py 领单执行；**未做任何 git 推送**。", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="CodeAgent 接单线（领单→能力链/选链→执行→回写）")
    ap.add_argument("--queue", default=QUEUE)
    ap.add_argument("--max", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fake-model", default=None, choices=[None, "smoke", "none"])
    a = ap.parse_args()

    if not os.path.isfile(a.queue):
        print("队列不存在：%s" % a.queue)
        return 0
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 1800:
        print("已有一次领单在跑（锁），本次跳过")
        return 0

    text = io.open(a.queue, encoding="utf-8").read()
    tasks = split_tasks(text)
    todo = [(t, b) for t, b in tasks if status_of(b).startswith("待办")]
    print("队列 %d 块，其中待办 %d 块" % (len(tasks), len(todo)))
    if not todo:
        return 0
    picked = todo[:max(1, a.max)]
    for t, b in picked:
        c = chain_of(b)
        print("  将领：%s（%s）" % (t, "显式能力链 %d 步" % len(c) if c else "按规则选链"))
    if a.dry_run:
        print("（dry-run，不动）")
        return 0

    io.open(LOCK, "w", encoding="utf-8").write(str(os.getpid()))
    rc = 0
    try:
        for tid, blk in picked:
            task = field_of(blk, "Task") or blk.split("\n")[0]
            steps = chain_of(blk)
            chain = {"name": "显式能力链", "src": "任务块声明", "score": "-"} if steps else pick_chain(task)
            print("\n== 领单 %s\n   方式: %s" % (tid, chain.get("src")))
            claimed = claim(blk)
            text = text.replace(blk, claimed, 1)
            io.open(a.queue, "w", encoding="utf-8", newline="\n").write(text)   # 先落"进行中"
            if steps:
                res = run_chain(steps, task, a.fake_model)
            else:
                res = run_runloop(task, field_of(blk, "目标文件"),
                                  field_of(blk, "DoD 校验命令"), a.fake_model)
            st = "已完成 %s" % stamp() if res.get("ok") else "阻塞 %s（见 Result）" % stamp()
            done = re.sub(r"(?m)^Status:.*$", "Status: %s" % st, claimed, count=1) + result_block(res, chain)
            text = text.replace(claimed, done, 1)
            io.open(a.queue, "w", encoding="utf-8", newline="\n").write(text)
            print("   写回：%s" % st)
            if not res.get("ok"):
                rc = 1
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
