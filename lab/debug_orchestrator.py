#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""debug_orchestrator.py — 黑箱自动化调试状态机（FR3，全自动，纯 stdlib）。

状态机：IDLE → REPRO → MINIMIZE → RETRIEVE → LOCATE → FIX → REGRESS → SEDIMENT → DONE

黑箱铁律：SUT 只测不读；唯一 oracle = 测试执行结果（codeagent test / 用户 test_cmd）。
每个状态迁移都发事件（debug.state）→ 前端进度条/日志（事件驱动无死角）。
轮次上限 N=3 防死循环；超时/超轮 → 诚实失败带证据，绝不虚构"修好了"。

- REPRO    真实复现：py_compile（语法）→ 语法错误证据；否则跑 codeagent test 或用户 test_cmd
- MINIMIZE  ddmin 最小化失败输入（黑箱纯算法）
- RETRIEVE  debug_history 同形命中 → 命中直接复用历史补丁
- LOCATE   method-impact/AST + 失败文本符号 → 候选根因清单
- FIX      LLM（model_config 配置可达时）生成补丁 / 历史补丁复用；否则诚实报告无法自动修复
- REGRESS  备份 → 应用补丁 → 回归（真实重跑）→ 全绿保留 / 红回滚重试（≤3 轮）
- SEDIMENT 调试历史入库 + 成功规则沉淀 lab_data/known_defects_extra.json（反哺闭环，不碰核心）
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

from events import emit
from lab_config import get_config

MAX_ROUNDS = 3
_runs = {}
_runs_lock = threading.Lock()
_seq = 0

# 快速语法修复：仅做"可证明安全"的文本级修复（当前为演示用规则集，LLM 缺席时的兜底）
_QUICK_FIXES = []


def new_run_id():
    global _seq
    with _runs_lock:
        _seq += 1
        return f"dbg-{time.strftime('%Y%m%d%H%M%S')}-{_seq}"


def start_debug(failure, rel_file, test_cmd="", model=None, timeout=180):
    """启动异步调试回路。返回 {run_id, status}。参数 file 为 target_root 内相对路径。"""
    cfg = get_config()
    run_id = new_run_id()
    with _runs_lock:
        _runs[run_id] = {"status": "running", "failure": failure, "file": rel_file,
                         "test_cmd": test_cmd, "model": model, "steps": [],
                         "started": time.time(), "rounds": 0, "patch": None,
                         "result": None, "ok": None, "hit": None}
    emit("debug.started", {"run_id": run_id, "file": rel_file})
    t = threading.Thread(target=_worker, args=(run_id, failure, rel_file, test_cmd,
                                               model, timeout), daemon=True)
    t.start()
    return {"ok": True, "run_id": run_id, "status": "running"}


def get_state(run_id):
    with _runs_lock:
        r = _runs.get(run_id)
        return dict(r) if r else None


def _add_step(run_id, state, evidence, detail="", elapsed=0.0):
    with _runs_lock:
        if run_id in _runs:
            _runs[run_id]["steps"].append({"state": state, "evidence": evidence,
                                           "detail": detail, "elapsed": elapsed})
    emit("debug.state", {"run_id": run_id, "state": state, "detail": detail,
                         "evidence": (evidence or "")[-300:]})
    return evidence


def _worker(run_id, failure, rel_file, test_cmd, model, timeout):
    from atom_loader_ext import merged_registry
    from atom_runner import run_capability
    import debug_history as dh
    from history_db import get_db
    cfg = get_config()
    db = get_db()
    registry = merged_registry(cfg.codeagent_root())
    t0 = time.time()
    evidence = ""
    patch = None
    ok_final = False
    rounds = 0
    symbols = []
    hit = None
    errsig = []
    try:
        # ── REPRO：真实失败证据 ──────────────────────────
        if not rel_file:
            evidence = _add_step(run_id, "REPRO", "缺目标文件：无法自动复现，"
                                                  "请提供失败文件（target_root 内相对路径）",
                                 "黑箱无 oracle，放弃执行")
            ok_final = False
            return _finish(run_id, db, ok_final, evidence, patch, rounds, errsig,
                           symbols, hit)
        real = os.path.realpath(os.path.join(cfg.target_root(), rel_file))
        root = cfg.target_root()
        if os.path.commonpath([real, root]) != root or not os.path.isfile(real):
            evidence = _add_step(run_id, "REPRO", f"文件不存在或越界: {rel_file}", "拒绝执行")
            ok_final = False
            return _finish(run_id, db, ok_final, evidence, patch, rounds, errsig,
                           symbols, hit)
        # 1) 语法检查（真实 py_compile）
        comp = _py_compile(real)
        if comp["ok"] is False:
            evidence = _add_step(run_id, "REPRO",
                                 f"语法错误: line {comp['line']}: {comp['detail'][:200]}",
                                 "py_compile 真实编译失败", 0)
            errsig = dh.extract_errsig(comp["detail"]) or ["SyntaxError"]
            # 语法失败：跳最小化（已最小：单错误行）→ 检索 → 定位 → 修复
            errsig = _minimize_step(run_id, evidence, errsig, db, failure)
        else:
            # 2) 测试 oracle 复现（真实执行）
            if test_cmd:
                r = _run_cmd(test_cmd, cwd=os.path.dirname(real), timeout=timeout)
                failed = r["code"] != 0
                evidence = _add_step(run_id, "REPRO",
                                     (f"测试失败(rc={r['code']}): {r['out'][-400:]}"
                                      if failed else f"测试通过(rc=0): {r['out'][-200:]}"),
                                     f"test_cmd: {test_cmd[:120]}", r["elapsed"])
            else:
                env = run_capability("code-test", "test.run", {"path": real},
                                     timeout=timeout, registry=registry,
                                     codeagent_root=cfg.codeagent_root())
                if env.get("ok"):
                    d = env.get("data") or {}
                    rg = d.get("red_green") or {}
                    failed = bool(rg.get("red"))
                    evidence = _add_step(run_id, "REPRO",
                                         (f"测试红: {str(d.get('summary'))[:200]}"
                                          if failed else
                                          f"测试绿: {str(d.get('summary'))[:200]}"),
                                         "codeagent test.run 真实执行", env.get("elapsed", 0))
                else:
                    failed = True
                    evidence = _add_step(run_id, "REPRO",
                                         f"测试原子失败: {env.get('error')}", "oracle 不可用")
            if not failed:
                evidence = _add_step(run_id, "REPRO",
                                     "未复现失败（oracle 全绿）。请补充能稳定触发的测试/用例，"
                                     "黑箱回路无法在无失败证据下继续。", "诚实：不虚构失败")
                ok_final = False
                return _finish(run_id, db, ok_final, evidence, patch, rounds, errsig,
                               symbols, hit)
            symbols = dh.extract_symbols(failure, _read_file(real))
        # ── MINIMIZE：ddmin 最小化失败输入 ────────────────
        if failure:
            errsig = dh.extract_errsig(failure)
        if failure and errsig:
            import ddmin
            mini = ddmin.lines_keep_signature(failure, errsig)
            _add_step(run_id, "MINIMIZE",
                      f"ddmin: {len(failure.splitlines())} 行 → {len(mini.splitlines())} 行"
                      f"（保持错误签名 {errsig[:3]}）", mini[:300], 0)
        else:
            _add_step(run_id, "MINIMIZE", "失败输入无错误签名可保持，跳过（文件级失败）", "", 0)
        # ── RETRIEVE：过去调试结果同形命中 ────────────────
        errsig_list = errsig
        errsig_str = ",".join(errsig_list[:8])
        hits = dh.find_hits(db, failure or evidence, rel_file, symbols, limit=5)
        if hits:
            # 复用取"带可复用补丁"的最高相似命中；若无补丁命中则保留顶部命中作展示，
            # _fix 对无补丁 hit 不会复用——避免命中"上一次也失败的运行"自身
            # 造成历史补丁复用断链/回滚。
            hit = next((h["hit"] for h in hits if h["hit"].get("patch")), hits[0]["hit"])
            sim = hits[0]["similarity"]
            _add_step(run_id, "RETRIEVE",
                      f"同形命中历史 #{hit['id']}（相似度 {sim}，errsig: "
                      f"{hit['errsig'][:80]}）", f"历史补丁: {(hit.get('patch') or '无')[:150]}", 0)
        else:
            _add_step(run_id, "RETRIEVE", "历史未命中（无同形失败记录）",
                      f"errsig: {errsig_str[:120]}", 0)
        # ── LOCATE：候选根因清单（黑箱：AST + 失败文本符号）──
        candidates = _locate(real, rel_file, failure, evidence)
        _add_step(run_id, "LOCATE", f"候选根因 {len(candidates)} 个: "
                                    f"{[c['symbol'] for c in candidates][:8]}",
                  json.dumps(candidates[:5], ensure_ascii=False)[:400], 0)
        # ── FIX + REGRESS（≤ MAX_ROUNDS 轮）────────────────
        while rounds < MAX_ROUNDS:
            rounds += 1
            patch, patch_src = _fix(run_id, failure, rel_file, real, evidence,
                                    candidates, model, hit, timeout, registry)
            if patch is None:
                _add_step(run_id, "FIX",
                          "无可用补丁生成路径：未配置可达模型且无历史补丁可复用 → "
                          "诚实报告无法自动修复（附根因候选与建议）",
                          "可在模型配置页接入本地/云端 OpenAI 兼容端点后自动修复", 0)
                ok_final = False
                break
            _add_step(run_id, "FIX", f"补丁来源: {patch_src}", patch[:400], 0)
            # REGRESS：备份 → 应用 → 真实回归
            orig = _read_file(real)
            backup = _apply_patch(real, patch, cfg, db)
            if backup is None:
                _add_step(run_id, "REGRESS", "补丁应用失败（内容不匹配/语法校验拒绝）→ 已回滚",
                          "本次未改文件", 0)
                ok_final = False
                break
            comp2 = _py_compile(real)
            if comp2["ok"] is False:
                db.add_edit_log(rel_file, "debug_patch", 0, False,
                                f"补丁后语法错误: {comp2['detail'][:200]}")
                _restore(real, backup, cfg)
                _add_step(run_id, "REGRESS",
                          f"补丁后语法错误(第{rounds}轮) → 回滚重试",
                          comp2["detail"][:200], 0)
                continue
            if test_cmd:
                r = _run_cmd(test_cmd, cwd=os.path.dirname(real), timeout=timeout)
                green = r["code"] == 0
                reg_evidence = (f"回归通过(rc=0): {r['out'][-300:]}" if green else
                                f"回归仍红(rc={r['code']}): {r['out'][-300:]}")
            else:
                env2 = run_capability("code-test", "test.run", {"path": real},
                                      timeout=timeout, registry=registry,
                                      codeagent_root=cfg.codeagent_root())
                d2 = env2.get("data") or {}
                rg2 = d2.get("red_green") or {}
                green = bool(rg2.get("green"))
                reg_evidence = (f"回归全绿: {str(d2.get('summary'))[:200]}" if green else
                                f"回归仍红: {str(d2.get('summary'))[:200]}（第{rounds}轮）")
            _add_step(run_id, "REGRESS", reg_evidence,
                      f"round {rounds}/{MAX_ROUNDS}", 0)
            if green:
                ok_final = True
                db.add_edit_log(rel_file, "debug_patch", 0, True,
                                f"调试补丁已应用并通过回归({patch_src})")
                break
            # 红 → 回滚，下一轮
            _restore(real, backup, cfg)
        if not ok_final and rounds >= MAX_ROUNDS:
            _add_step(run_id, "DONE", f"已达轮次上限 {MAX_ROUNDS}，仍需人工干预（附证据与候选）",
                      "诚实失败，绝不虚构修好了", 0)
        _finish(run_id, db, ok_final, evidence, patch, rounds, errsig_str, symbols, hit)
    except Exception as e:  # noqa: BLE001 绝不吞错
        _add_step(run_id, "DONE", f"调试回路异常: {type(e).__name__}: {e}", "", 0)
        _finish(run_id, db, False, evidence, None, rounds, errsig, symbols, hit)


def _finish(run_id, db, ok, evidence, patch, rounds, errsig, symbols, hit):
    with _runs_lock:
        if run_id not in _runs:
            return
        r = _runs[run_id]
        r["status"] = "success" if ok else "failed"
        r["ok"] = ok
        r["patch"] = patch
        r["rounds"] = rounds
        r["result"] = evidence
        r["hit"] = hit
        steps = list(r["steps"])
        failure = r["failure"]
        file = r["file"]
        test_cmd = r["test_cmd"]
    # 沉淀：调试历史入库（成功/失败都记）
    db.add_debug(failure, file, test_cmd, (errsig if isinstance(errsig, str)
                                           else ",".join(errsig[:8])), symbols,
                 steps, patch, ok, evidence, rounds, run_id)
    if ok and patch:
        _sediment_rule(errsig, file, patch, evidence)
    emit("debug.finished", {"run_id": run_id, "ok": ok, "rounds": rounds,
                            "patch_applied": bool(patch and ok)})


def _sediment_rule(errsig, file, patch, evidence):
    """成功规则沉淀（lab 侧 known_defects_extra，反哺闭环，不碰核心 known_defects.py）。"""
    from lab_config import get_config
    cfg = get_config()
    path = cfg.get("known_defects_extra")
    rules = []
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                rules = json.load(f)
            if not isinstance(rules, list):
                rules = []
        except Exception:  # noqa: BLE001
            rules = []
    rules.append({"errsig": errsig, "file": file, "patch": patch,
                  "evidence": (evidence or "")[:300],
                  "ts": time.time()})
    rules = rules[-200:]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    emit("debug.sedimented", {"errsig": errsig, "file": file})


# ── 工具函数 ──────────────────────────────────
def _py_compile(absf, timeout=60):
    r = subprocess.run([sys.executable, "-m", "py_compile", absf],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        return {"ok": True}
    m = re.search(r"line (\d+)", r.stderr or r.stdout or "")
    line = int(m.group(1)) if m else 0
    msgs = [ln.strip() for ln in (r.stderr or r.stdout or "").splitlines()
            if "Error" in ln or "error" in ln]
    return {"ok": False, "line": line,
            "detail": (msgs[0] if msgs else (r.stderr or r.stdout or "")[:300])}


def _run_cmd(cmd, cwd=None, timeout=120):
    t0 = time.time()
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return {"code": r.returncode, "out": (r.stdout or "") + (r.stderr or ""),
                "elapsed": round(time.time() - t0, 2)}
    except subprocess.TimeoutExpired:
        return {"code": -1, "out": f"超时(>{timeout}s)", "elapsed": timeout}
    except Exception as e:  # noqa: BLE001
        return {"code": -1, "out": f"{type(e).__name__}: {e}", "elapsed": 0}


def _read_file(absf):
    try:
        with open(absf, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _minimize_step(run_id, evidence, errsig, db, failure):
    import ddmin
    if failure and errsig:
        mini = ddmin.lines_keep_signature(failure, errsig)
        _add_step(run_id, "MINIMIZE",
                  f"ddmin: {len(failure.splitlines())} 行 → {len(mini.splitlines())} 行",
                  mini[:300], 0)
    else:
        _add_step(run_id, "MINIMIZE", "语法错误已最小（单错误行），跳过 ddmin", "", 0)
    return errsig


def _locate(real, rel_file, failure, evidence):
    """候选根因：AST 符号（失败文本命中的函数优先）。黑箱证据 + 符号交集。"""
    import graph_viz
    candidates = []
    try:
        g = graph_viz.impact_graph(os.path.dirname(real), os.path.basename(real),
                                   codeagent_root=None)
        for n in g.get("nodes", []):
            if n.get("kind") == "symbol":
                candidates.append({"symbol": n["label"], "line": "",
                                   "reason": "文件内函数/调用链"})
    except Exception:  # noqa: BLE001
        pass
    # 失败文本/证据里出现的符号优先
    text = f"{failure or ''} {evidence or ''}"
    for c in candidates:
        if c["symbol"] in text:
            c["reason"] += "；命中失败文本"
    if not candidates:
        candidates.append({"symbol": "（无符号可定位）", "line": "",
                           "reason": "黑箱无符号线索，建议补充失败输出中包含函数名/行号"})
    return candidates[:12]


_PATCH_RE = re.compile(r"```(?:patch|diff)?\s*(.*?)```", re.S)


def _fix(run_id, failure, rel_file, real, evidence, candidates, model, hit, timeout,
         registry):
    """生成补丁。优先级：历史补丁复用 > 快速修复规则 > LLM。返回 (patch, source)。"""
    # 1) 历史补丁复用（同形命中且有补丁）
    if hit and hit.get("patch"):
        patch = hit["patch"]
        if _validate_patch(real, patch):
            return patch, f"历史补丁复用(#{hit.get('id')})"
    # 2) LLM 修复（model_config 配置可达时）
    from model_config import chat_completion, models_active as _ma
    from lab_config import get_config
    cfg = get_config()
    if model or _ma():
        content = _read_file(real)
        prompt = (
            "你是黑箱调试修复器。目标文件内容如下（用 <FILE> ... </FILE> 包裹）。\n"
            f"<FILE>\n{content[:8000]}\n</FILE>\n"
            f"失败描述: {str(failure or evidence)[:2000]}\n"
            f"候选根因: {json.dumps(candidates[:5], ensure_ascii=False)}\n"
            "请输出 仅一个 JSON 对象（不要任何其他文字），格式:\n"
            '{"replacements": [{"line_start": 1-based行号, "line_end": 行号,'
            '"new_lines": "替换后的完整行文本(可多行，\\n分隔)"}], "reason": "简述"}'
        )
        try:
            r = chat_completion(model, [{"role": "user", "content": prompt}],
                                timeout=timeout)
            if r.get("ok"):
                parsed = _parse_patch_json(r["content"])
                if parsed and _validate_patch(real, parsed):
                    return parsed, f"LLM补丁({(model or 'active')})"
                return None, "LLM 未返回可解析补丁"
            return None, f"模型不可达: {r.get('error')}"
        except Exception as e:  # noqa: BLE001
            return None, f"LLM 调用异常: {type(e).__name__}: {e}"
    return None, "无模型配置/无历史补丁"


def _parse_patch_json(text):
    """从模型回复提取最后一个 JSON 对象（容忍前后缀/代码围栏）。"""
    import json as _j
    for fence in _PATCH_RE.finditer(text):
        frag = fence.group(1)
        try:
            obj = _j.loads(frag)
            if isinstance(obj, dict) and "replacements" in obj:
                return obj
        except Exception:  # noqa: BLE001
            continue
    idx = text.rfind("{")
    while idx >= 0:
        try:
            obj = _j.loads(text[idx:])
            if isinstance(obj, dict) and "replacements" in obj:
                return obj
        except Exception:  # noqa: BLE001
            pass
        idx = text.rfind("{", 0, idx)
    return None


def _validate_patch(real, patch):
    """校验补丁可应用：行号在范围内；应用后 py_compile 通过（temp 文件真实编译）。"""
    try:
        new_content = _apply_text(_read_file(real), patch)
        import tempfile
        fd, tmpf = tempfile.mkstemp(suffix=".py", prefix=".lab_patch_",
                                    dir=os.path.dirname(real))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new_content)
            r = subprocess.run([sys.executable, "-m", "py_compile", tmpf],
                               capture_output=True, timeout=60)
            return r.returncode == 0
        finally:
            try:
                os.remove(tmpf)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        return False


def _apply_text(content, patch):
    """按 replacements（1-based 行号，闭区间）替换。返回新内容；越界抛 ValueError。"""
    if isinstance(patch, str):
        _j = json.loads(patch)
        if isinstance(_j, dict) and "replacements" in _j:
            patch = _j
        else:
            raise ValueError("补丁格式非法")
    lines = content.split("\n")
    reps = sorted(patch["replacements"], key=lambda r: r["line_start"])
    for rp in reps:
        s, e = int(rp["line_start"]), int(rp.get("line_end", rp["line_start"]))
        if s < 1 or e > len(lines) or s > e:
            raise ValueError(f"行号越界: {s}-{e} (共{len(lines)}行)")
        new_lines = (rp.get("new_lines") or "").split("\n")
        lines[s - 1:e] = new_lines
    return "\n".join(lines)


def _apply_patch(real, patch, cfg, db):
    """备份 + 应用补丁。成功返回备份路径；失败返回 None（不改原文件）。"""
    try:
        content = _read_file(real)
        new_content = _apply_text(content, patch)
    except Exception as e:  # noqa: BLE001
        return None
    backup_dir = cfg.get("backup_dir")
    os.makedirs(backup_dir, exist_ok=True)
    rel = os.path.relpath(real, cfg.target_root()).replace(os.sep, "_")
    backup = os.path.join(backup_dir, f"{rel}.{time.time_ns()}.bak")
    try:
        with open(backup, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except OSError:
        return None
    tmp = real + f".lab_patch.{time.time_ns()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_content)
        os.replace(tmp, real)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    return backup


def _restore(real, backup, cfg):
    try:
        with open(backup, encoding="utf-8", errors="replace") as f:
            content = f.read()
        tmp = real + f".lab_restore.{time.time_ns()}"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp, real)
    except OSError:
        pass