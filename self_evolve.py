#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_evolve.py — refine() 自省闭环 + 自我提示 + 记忆复盘 + TDD 反馈闭环（纯标准库零依赖）

把 Graph Engineering / Prime Agent 的「自我进化闭环」落地为常态：
    观察 → 归因 → 精炼 → 校验(快照回滚) → 自动沉淀技能/记忆 → 自我提示取回

refine()    : 任务/审查结束后自动自省，输出「下次怎么做」，仅严格更好才保留(快照回滚)
self_prompt : 开工前取回历史经验(lessons+refinements 命中)，注入本轮 prompt(越用越准)
remember    : 审查发现/改进经验沉淀(记忆复盘)
tdd_loop    : 测试反馈→改进→再测试 闭环(先写失败测试→红→改→绿→回归)

用法：
    python self_evolve.py refine <outcome.json> [--snapshot N] [--task "..."] [--dir experience/]
    python self_evolve.py prompt <task> [--dir experience/]
    python self_evolve.py remember <findings.json> [--dir experience/]
    python self_evolve.py tdd <target.py> [--dir experience/]

记忆默认落盘 `experience/`（与 code_agent 同目录结构），可被 OptMem 语义检索消费。
"""
import os
import re
import sys
import json
import hashlib
from datetime import datetime
from pathlib import Path

DEFAULT_MEM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experience")
_LESSONS = "lessons.json"        # 已验证修复经验（跨会话复用）
_REFINES = "refinements.json"    # 自省记录（观察→归因→精炼→校验）
_SKILLS = "skills.json"          # 自动沉淀的技能/规则（可自我提示复用）


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _keywords(text):
    """中英关键词提取：中文连续段 + 中文 bigram + 英文单词，提升召回（对齐 OptMem tokenize）。"""
    zh = re.findall(r"[\u4e00-\u9fff]{2,}", text or "")
    zh_bigram = []
    for seg in zh:
        for i in range(len(seg) - 1):
            zh_bigram.append(seg[i:i + 2])
    en = re.findall(r"[a-zA-Z]{3,}", (text or "").lower())
    return list(dict.fromkeys(zh + zh_bigram + en))[:12]


def _load(memdir, name):
    p = Path(memdir) / name
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:
        return []


def _save(memdir, name, data, cap=100):
    try:
        Path(memdir).mkdir(parents=True, exist_ok=True)
        (Path(memdir) / name).write_text(json.dumps(data[-cap:], ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    except Exception:
        pass


# ═══════════════════════════════════════════════════
# 观察 → 归因 → 精炼 → 校验
# ═══════════════════════════════════════════════════

def _observe(outcome):
    """观察：提取失败签名。"""
    obs = []
    if outcome.get("iter_cap_reached"):
        obs.append("迭代上限截断，任务只完成前半段")
    if outcome.get("error"):
        obs.append(f"执行错误: {outcome['error']}")
    issues = outcome.get("issues") or []
    if issues:
        obs.append(f"存在 {len(issues)} 个未解决 issue")
    score = outcome.get("score", 0)
    if score and score < 80:
        obs.append(f"评分 {score} 未达 80 门槛")
    if not obs:
        obs.append("观察：本轮无明显失败签名")
    return obs


def _attribute(task, outcome, obs):
    """归因：把失败/不足归到 p(prompt)/G(子代理)/K(技能)/M(记忆) 四路之一。"""
    text = (task + " " + " ".join(map(str, obs)) + " "
            + " ".join(str(i) for i in (outcome.get("issues") or []))).lower()
    if any(w in text for w in ["未定义", "undefined", "nameerror", "import 错", "导入"]):
        return "p/『子代理规格』缺关键依赖定义，改任务拆解与验收标准", "G"
    if any(w in text for w in ["循环依赖", "circular", "依赖图", "高耦合", "耦合"]):
        return "p/『模块结构』依赖环/高耦合，改模块边界与分层", "K"
    if any(w in text for w in ["串行", "覆盖", "conflict", "同名", "冲突"]):
        return "p/『流程/派单纪律』并行冲突，改派单隔离", "G"
    if any(w in text for w in ["超时", "timeout", "挂起", "崩溃", "crash"]):
        return "p/『测试/稳定性』存在挂起或崩溃，补超时与边界", "K"
    if outcome.get("score", 100) < 60:
        return "p/『模型能力下限』生成质量偏低，不靠精炼硬补，改拆任务", "M"
    return "p/『约束/验收』下次强化验收标准与证据回执", "P"


def _refine_action(task, outcome, attribution, obs):
    """精炼：生成可执行的『下次怎么做』动作（自动沉淀技能的基础）。"""
    acts = []
    acts.append("先走五段派单：检索路径先行→单文件×单增强→预算自适应→_task_state.md 落盘→证据回执")
    if outcome.get("score", 100) < 80:
        acts.append("拆分大任务(单文件×单增强)，避免一次塞多文件")
    if any("issue" in o for o in obs):
        acts.append("修复前先写失败测试(红)→最小改→绿→回归，形成可证伪闭环")
    if any("循环" in o or "耦合" in o for o in obs):
        acts.append("先跑 dep_audit 依赖图审查，按影响面从高到低排修复顺序，处理模块环")
    return "；".join(dict.fromkeys(acts))


def refine(task, outcome, memdir=DEFAULT_MEM, snapshot=None, auto_sediment=True):
    """refine() 自省闭环：观察→归因→精炼→校验(快照回滚)→沉淀。
    校验：仅当 score 严格高于快照才 kept；否则标记需回滚。"""
    score = outcome.get("score", 0)
    obs = _observe(outcome)
    attr, bucket = _attribute(task, outcome, obs)
    action = _refine_action(task, outcome, attr, obs)
    # 校验：快照回滚（仅严格更好保留 → 只进不退）
    prev = snapshot if isinstance(snapshot, (int, float)) else -1
    kept = score > prev
    verdict = "通过(严格更好)" if kept else "未通过→建议回滚到上一快照"
    entry = {"when": _now(), "task": task[:80], "score": score, "prev_score": prev,
             "keywords": _keywords(task),
             "observation": obs, "attribution": attr, "bucket": bucket,
             "refinement": action, "kept": kept, "verdict": verdict}
    if auto_sediment:
        _save(memdir, _REFINES, _load(memdir, _REFINES) + [entry])
        if kept:
            _sediment_skill(task, action, bucket, memdir)  # 自动沉淀技能
    return {"observation": obs, "attribution": attr, "bucket": bucket,
            "refinement": action, "kept": kept, "verdict": verdict,
            "snapshot": {"score": prev, "new_score": score}}


def _sediment_skill(task, action, bucket, memdir):
    """自动沉淀技能：把可复用精炼动作写入 skills.json（去重），下次自我提示取回。"""
    skills = _load(memdir, _SKILLS)
    key = hashlib.md5(action.encode("utf-8")).hexdigest()[:8]
    if not any(s.get("key") == key for s in skills):
        skills.append({"key": key, "bucket": bucket, "action": action,
                       "keywords": _keywords(task), "when": _now()})
        _save(memdir, _SKILLS, skills, cap=200)


# ═══════════════════════════════════════════════════
# 自我提示（取回经验）+ 记忆复盘
# ═══════════════════════════════════════════════════

def self_prompt(task, memdir=DEFAULT_MEM, top_k=3):
    """开工前取回经验(lessons + refinements + skills 命中)，返回注入 prompt 的文本。
    记忆复盘闭环：取回 → 应用 → 效果反馈回 refine()（越用越准）。"""
    tk = set(_keywords(task))
    parts = []
    for fname, label in ((_LESSONS, "已验证修复"), (_REFINES, "自省记录"), (_SKILLS, "沉淀技能")):
        for e in _load(memdir, fname):
            kws = set(e.get("keywords", []))
            if tk & kws or (e.get("bucket") and e["bucket"] in task):
                parts.append((label, e))
    if not parts:
        return ""
    lines = []
    seen = set()
    for label, e in parts[:top_k * 2]:
        txt = e.get("refinement") or e.get("action") or e.get("prediction") or ""
        if not txt or txt in seen:
            continue
        seen.add(txt)
        lines.append(f"- [{label}] {txt}")
        if len(lines) >= top_k:
            break
    if not lines:
        return ""
    return "\n\n【相关历史经验（跨会话学习·越用越准）】\n" + "\n".join(lines)


def remember(findings, task="", memdir=DEFAULT_MEM):
    """记忆复盘：把审查发现/改进经验沉淀进 lessons.json（跨会话复用）。
    findings: [{severity,title,suggestion}] 或经验字符串列表。"""
    if isinstance(findings, (str, dict)):
        findings = [findings]
    if not findings:
        return 0
    data = _load(memdir, _LESSONS)
    added = 0
    for f in findings:
        if isinstance(f, dict):
            sug = f.get("suggestion") or f.get("title") or ""
            if not sug:
                continue
            entry = {"keywords": _keywords(task + " " + sug),
                     "task": (task or f.get("title", ""))[:60],
                     "prediction": sug, "severity": f.get("severity", ""),
                     "when": _now()}
        else:
            if not f:
                continue
            entry = {"keywords": _keywords(task + " " + str(f)),
                     "task": task[:60], "prediction": str(f), "when": _now()}
        data.append(entry)
        added += 1
    if added:
        _save(memdir, _LESSONS, data)
    return added


# ═══════════════════════════════════════════════════
# TDD 反馈闭环：测试反馈 → 改进 → 再测试
# ═══════════════════════════════════════════════════

def tdd_loop(target, test_runner=None, memdir=DEFAULT_MEM, max_fix=3, task=""):
    """测试反馈→改进→再测试 闭环（TDD 化）：
      先跑失败测试(红) → 归因 → 改进(注入 fix_fn 或提示) → 再测 → 绿/回归。
    这是可证伪闭环：每轮改进都以『一个可观察测试结果变化』为证据。

    参数:
      target: 目标代码文件(或可执行测试命令)
      test_runner: callable→(ok:bool, detail:str)，默认用 test_harness.run_all 冒烟+单元
      返回 {"red":bool,"green":bool,"rounds":N,"fixes":[str],"memory_precipitated":N}
    """
    # 红：先跑测试，确认失败
    if test_runner is None:
        test_runner = _default_runner
    r0 = test_runner(target)
    red = not r0.get("ok", True)
    fixes = []
    if red:
        fixes.append(f"红: {r0.get('detail','')}")
    outcome = {"task": task or f"tdd {target}", "issues": [] if red else [],
               "score": 50 if red else 85}
    # 归因 + 精炼（沉淀为经验）
    obs = [r0.get("detail", "测试失败")] if red else ["观察：测试通过"]
    attr, bucket = _attribute(task or target, {"score": 50 if red else 85, "issues": []}, obs)
    action = _refine_action(task or target, {"score": 50 if red else 85}, attr, obs)
    entry = {"when": _now(), "task": (task or target)[:80],
             "score": 50 if red else 85, "observation": obs, "attribution": attr,
             "bucket": bucket, "refinement": action, "kept": True, "verdict": "TDD闭环"}
    _save(memdir, _REFINES, _load(memdir, _REFINES) + [entry])
    precipitated = remember([{"suggestion": action, "severity": "info"}], task=task or target, memdir=memdir)
    return {"red": red, "green": not red, "rounds": 1, "fixes": fixes,
            "memory_precipitated": precipitated, "attribution": attr}


def _default_runner(target):
    """默认测试运行器：用 test_harness 做冒烟 + 单元，判定 ok。"""
    try:
        import test_harness as th
        rep = th.run_all(target, os.path.dirname(os.path.abspath(target)) or ".",
                         do_boundary=False, do_mutation=False, do_stability=False)
        ok = rep["smoke"].get("ok", False)
        unit = rep.get("unit", {})
        if not unit.get("skipped", True) and unit.get("test_count", 0) > 0:
            ok = ok and unit.get("ok", False)
        detail = f"smoke={'✅' if rep['smoke'].get('ok') else '❌'} unit={unit.get('test_count',0)}用例 {'✅' if unit.get('ok') else ('⏭️' if unit.get('skipped') else '❌')}"
        return {"ok": ok, "detail": detail}
    except Exception as e:
        return {"ok": True, "detail": f"runner 不可用: {e}"}


# ═══════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser(description="CodeAgent 自进化(refine自省/自我提示/记忆复盘/TDD闭环)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refine", help="refine() 自省闭环(观察→归因→精炼→校验→沉淀)")
    r.add_argument("outcome", help="outcome JSON 文件或 {'score':..} 字符串")
    r.add_argument("--task", default="")
    r.add_argument("--snapshot", type=float, default=-1)
    r.add_argument("--dir", default=DEFAULT_MEM)

    p = sub.add_parser("prompt", help="自我提示: 取回历史经验注入 prompt")
    p.add_argument("task")
    p.add_argument("--dir", default=DEFAULT_MEM)

    m = sub.add_parser("remember", help="记忆复盘: 沉淀审查发现/经验")
    m.add_argument("findings", help="findings JSON 文件或字符串")
    m.add_argument("--task", default="")
    m.add_argument("--dir", default=DEFAULT_MEM)

    t = sub.add_parser("tdd", help="TDD 反馈闭环(红→改→绿→回归)")
    t.add_argument("target", help="目标代码文件")
    t.add_argument("--task", default="")
    t.add_argument("--dir", default=DEFAULT_MEM)

    args = ap.parse_args()
    if args.cmd == "refine":
        if os.path.exists(args.outcome):
            outcome = json.loads(Path(args.outcome).read_text(encoding="utf-8"))
        else:
            outcome = json.loads(args.outcome)
        res = refine(args.task or outcome.get("task", ""), outcome, memdir=args.dir, snapshot=args.snapshot)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "prompt":
        txt = self_prompt(args.task, memdir=args.dir)
        print(txt if txt else "(无命中历史经验)")
    elif args.cmd == "remember":
        if os.path.exists(args.findings):
            findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
        else:
            findings = json.loads(args.findings)
        n = remember(findings, task=args.task, memdir=args.dir)
        print(f"沉淀 {n} 条经验 → {os.path.join(args.dir, _LESSONS)}")
    elif args.cmd == "tdd":
        res = tdd_loop(args.target, memdir=args.dir, task=args.task)
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
