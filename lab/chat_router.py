#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_router.py — 原生对话入口（FR5，意图路由：关键词优先 + LLM 兜底，纯 stdlib）。

- 关键词意图表（确定性，优先）：审查/测试/调试/报告/影响/模型/浏览/原子库/历史/不支持
- LLM 兜底：关键词未命中且模型配置可达 → 让模型从意图清单里选；不可达/不可解析 →
  回退关键词表；再兜底 → 诚实"未识别 + 可用能力清单"，绝不编造
- 会话上下文：历史注入最近 N 轮（context-compact 语义，取最近 8 条）
- 返回 {reply, intent, action?, action_data?}，action 由 web_viz 内部执行并格式化回复
"""
import re

# 意图表：keyword → (intent, action)
_INTENTS = [
    (("报告", "report", "审查报告"), ("report", "report")),
    (("审查", "review ", "审查文件"), ("review", "review_file")),
    (("测试", "跑测试", "test"), ("test", "test_file")),
    (("调试", "debug"), ("debug", "debug")),
    (("影响", "impact", "影响了哪些", "影响面"), ("impact", "impact")),
    (("模型", "切换模型", "model"), ("models", "models")),
    (("树", "文件树", "浏览", "browse", "tree"), ("tree", "tree")),
    (("原子", "调色板", "原子库"), ("atoms", "atoms")),
    (("历史", "调试历史", "history"), ("debug_history", "debug_history")),
    (("图", "依赖图", "分层图", "调用链"), ("graph", "graph")),
    (("保存", "编辑", "改代码", "修改文件"), ("edit", "edit")),
    (("配置", "目标仓库", "切换目录", "切到", "切换", "换目录", "target"), ("config", "config")),
    (("部署", "发布到生产", "上生产", "生产环境", "公网"), ("unsupported", "unsupported")),
    (("mcp", "skill", "技能"), ("unsupported", "unsupported")),
]

_PATH_RE = re.compile(r"[\\w./\\\\-]+\\.py")
# 目录路径提取：绝对路径（C:/… E:\\… /…）或相对目录
_DIR_RE = re.compile(r"([A-Za-z]:[\\\\/][^\\s,，。;；]+|[\\\\/][^\\s,，。;；]+)")


def extract_dir_path(message):
    m = _DIR_RE.search(message or "")
    return m.group(1).rstrip("/\\") if m else None


def extract_path(message):
    m = _PATH_RE.search(message or "")
    return m.group(0) if m else None


def route(message, history=None, llm=False):
    """关键词优先。命中 → (intent, action, data)。未命中 → llm 兜底或空。"""
    msg = (message or "").strip()
    if not msg:
        return None
    for kws, (intent, action) in _INTENTS:
        if any(k.lower() in msg.lower() for k in kws):
            data = {"message": msg}
            p = extract_path(msg)
            if p:
                data["path"] = p
            return intent, action, data
    return None


def llm_route(message):
    """LLM 兜底：从意图清单选（模型不可达 → None）。"""
    from model_config import chat_completion
    intents = ["review", "test", "debug", "report", "impact", "models", "tree",
               "atoms", "debug_history", "graph", "edit", "config"]
    prompt = (
        f"用户输入: {message[:500]}\n"
        f"可从下列意图选一个: {intents}\n"
        "仅输出意图名（无其他文字）。若都不匹配输出 unsupported。"
    )
    r = chat_completion(None, [{"role": "user", "content": prompt}], timeout=30)
    if not r.get("ok"):
        return None
    ans = (r.get("content") or "").strip().lower()
    for i in intents + ["unsupported"]:
        if i in ans[:40]:
            return i, "llm"
    return None


def reply_for(intent, message, action_result=None):
    """按意图生成对话回复（诚实：失败带证据，不支持明说）。"""
    a = action_result or {}
    ok = a.get("ok")
    if intent == "review":
        if ok:
            d = a.get("data") or {}
            return (f"✅ 审查完成: {d.get('summary', '')} | P0={d.get('p0', a.get('p0'))} "
                    f"问题 {a.get('count', '')}")
        return f"❌ 审查失败: {a.get('error', '')}"
    if intent == "test":
        if ok:
            d = a.get("data") or {}
            rg = d.get("red_green") or {}
            return ("✅ 测试全绿" if rg.get("green") else
                    f"❌ 测试红: {str(d.get('summary', ''))[:120]}")
        return f"❌ 测试失败: {a.get('error', '')}"
    if intent == "debug":
        if ok:
            return (f"🔧 调试回路已启动 run_id={a.get('run_id')}，"
                    f"状态={a.get('status')}（进度见调试台/事件流）")
        return f"❌ 调试启动失败: {a.get('error', '')}"
    if intent == "report":
        if ok:
            return (f"📋 报告生成已启动 report_id={a.get('report_id')}，"
                    f"完成事件见报告页进度条")
        return f"❌ 报告启动失败: {a.get('error', '')}"
    if intent == "impact":
        if ok:
            d = a.get("data") or {}
            n = len(d.get("nodes") or [])
            e = len(d.get("edges") or [])
            return f"🔗 影响图: {n} 节点 / {e} 边（渲染于代码工作区）"
        return f"❌ 影响分析失败: {a.get('error', '')}"
    if intent == "models":
        d = a.get("data") or {}
        provs = d.get("providers") or []
        if ok:
            return (f"🧠 模型配置: {len(provs)} 个 provider, "
                    f"降级链 {d.get('chain') or '（未配置）'}")
        return f"❌ 模型配置异常: {a.get('error', '')}"
    if intent == "tree":
        d = a.get("data") or {}
        if ok:
            return (f"🌳 文件树: {len(d.get('dirs') or [])} 目录 / "
                    f"{len(d.get('files') or [])} 文件（代码工作区已刷新）")
        return f"❌ 浏览失败: {a.get('error', '')}"
    if intent == "atoms":
        d = a.get("data") or {}
        return (f"🧩 原子库: {d.get('core_count')} 核心 + {d.get('ext_count')} 扩展 "
                f"= {d.get('count')} 原子，冲突 {len(d.get('conflicts') or [])} 条")
    if intent == "debug_history":
        hits = (a.get("data") or {}).get("hits") or []
        if ok:
            return (f"🕘 调试历史 {len(hits)} 条（可点『重试上次调试』复用回路）" if hits
                    else "🕘 调试历史为空（尚未沉淀过失败/修复）")
        return f"❌ 历史读取失败: {a.get('error', '')}"
    if intent == "graph":
        d = a.get("data") or {}
        if ok:
            return (f"📊 {d.get('source', '图')}: {len(d.get('nodes') or [])} 节点 / "
                    f"{len(d.get('edges') or [])} 边")
        return f"❌ 图生成失败: {a.get('error', '')}"
    if intent == "edit":
        if ok:
            if a.get("saved"):
                return f"✏️ 已保存 {a.get('path')}（py_compile 通过，已入审计）"
            return f"✏️ 已定位文件 {a.get('path')} — 请在代码工作区编辑器修改后保存（对话不编造代码）"
        return f"❌ 保存失败: {a.get('error', '')}"
    if intent == "config":
        if ok:
            d = a.get("data") or {}
            if a.get("switched"):
                return (f"✅ 目标仓库已切换为: {a.get('new_target')}\n"
                        f"原子库: {d.get('codeagent_root')}（文件树已刷新）")
            return (f"⚙️ 目标仓库: {d.get('target_root')}\n原子库: {d.get('codeagent_root')}"
                    f"\n提示: 说『切换到 <目录路径>』可实时切换审查目标")
        return f"❌ 配置操作失败: {a.get('error', '')}"
    if intent == "answer":
        if ok:
            d = a.get("data") or {}
            ans = str(d.get("answer") or a.get("answer") or "")
            prov = d.get("provider") or ""
            model = d.get("model") or ""
            suffix = f"（{prov}/{model}）" if prov else ""
            return ans + suffix if ans else "模型已回复（空内容）"
        return (f"🤖 无法识别意图，且模型兜底不可用: {a.get('error', '')}。"
                f"可用指令: 审查/测试/调试/报告/影响/模型/浏览/原子库/历史/切换到 <目录>")
    if intent == "unsupported":
        return ("⚠️ 该能力不在本系统范围内（本系统=代码浏览/编辑/黑箱调试/审查报告/"
                "模型配置/原子编排，全部本地运行）。明确不支持，不编造。")
    return f"🤖 未识别意图: {message[:60]}。可用: 审查/测试/调试/报告/影响/模型/浏览/原子库/历史"


def handle(message, history=None, db=None, events=None):
    """完整对话处理：路由 → 返回 (reply, intent, action, action_data)。"""
    from lab_config import get_config
    cfg = get_config()
    r = route(message)
    intent, action, data = None, None, None
    source = "keyword"
    if r:
        intent, action, data = r
    else:
        lr = llm_route(message)
        if lr:
            intent, action_name = lr
            action = action_name if action_name in _ACTIONS else intent
            data = {"message": message}
            source = "llm"
        else:
            # ⑤ 模型兜底：未知意图 → 让模型直接回答/执行（而非只语义匹配）。
            # 数据不出私域：仅发往用户配置的本地/云端端点。
            intent, action, data, source = "answer", "answer", {"message": message}, "llm"
    # 会话注入（context-compact：最近 8 条）
    ctx = []
    if db:
        for s in db.recent_sessions(8):
            ctx.append({"role": s["role"], "content": s["content"]})
    ctx.append({"role": "user", "content": message})
    if db:
        db.add_session("user", message)
    if intent and action and action in _ACTIONS:
        try:
            ar = _ACTIONS[action](data or {}, cfg)
        except Exception as e:  # noqa: BLE001
            ar = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    else:
        ar = None
    reply = reply_for(intent, message, ar) if intent else \
        reply_for(None, message, None)
    if db:
        db.add_session("assistant", reply)
    if events:
        events.emit("chat.reply", {"intent": intent or "unknown", "source": source,
                                   "reply": reply[:200]})
    return {"reply": reply, "intent": intent or "unknown", "source": source,
            "action": action, "action_data": ar}


# ── 动作实现（web_viz 内实际端点同构，这里供对话直接调用）─────────
def _a_review_file(data, cfg):
    from file_viz import read_file
    p = data.get("path")
    if not p:
        return {"ok": False, "error": "请给出目标 .py 文件（如: 审查 bad_sample.py）"}
    absf = os.path.join(cfg.target_root(), p)
    rel = p
    from atom_runner import run_capability
    from atom_loader_ext import merged_registry
    env = run_capability("code-review", "codereview.review", {"path": absf},
                         registry=merged_registry(cfg.codeagent_root()),
                         codeagent_root=cfg.codeagent_root())
    return env


def _a_test_file(data, cfg):
    p = data.get("path")
    if not p:
        return {"ok": False, "error": "请给出目标文件（如: 测试 sample.py）"}
    from atom_runner import run_capability
    from atom_loader_ext import merged_registry
    env = run_capability("code-test", "test.run",
                         {"path": os.path.join(cfg.target_root(), p)},
                         registry=merged_registry(cfg.codeagent_root()),
                         codeagent_root=cfg.codeagent_root())
    return env


def _a_debug(data, cfg):
    from debug_orchestrator import start_debug
    p = data.get("path")
    return start_debug(data.get("message") or "调试请求", p or "", "", None, 180)


def _a_report(data, cfg):
    from report_gen import start_report
    p = data.get("path") or "."
    return start_report(p)


def _a_impact(data, cfg):
    from graph_viz import impact_graph
    p = data.get("path")
    if not p:
        return {"ok": False, "error": "请给出文件（如: 影响 method_impact.py）"}
    g = impact_graph(cfg.target_root(), p,
                     codeagent_root=cfg.codeagent_root())
    return {"ok": g.get("ok", False), "data": g, "error": g.get("error")}


def _a_models(data, cfg):
    from model_config import public
    return {"ok": True, "data": public()}


def _a_tree(data, cfg):
    from file_viz import tree
    return {"ok": True, "data": tree(cfg.target_root(), recursive=False,
                                     codeagent_root=cfg.codeagent_root())}


def _a_atoms(data, cfg):
    from atom_loader_ext import merged_registry
    return {"ok": True, "data": merged_registry(cfg.codeagent_root())}


def _a_debug_history(data, cfg):
    from history_db import get_db
    return {"ok": True, "data": {"hits": get_db().list_debug("", 20)}}


def _a_graph(data, cfg):
    from graph_viz import deps_graph
    p = data.get("path")
    if p:
        g = deps_graph(cfg.target_root(), p)
    else:
        g = deps_graph(cfg.target_root())
    return {"ok": True, "data": g, "error": g.get("error")}


def _a_edit(data, cfg):
    p = data.get("path")
    content = data.get("content")
    if content is not None and p:
        # P1-9 真实能力：对话带 content 时真正保存到目标仓库
        import file_viz
        r = file_viz.save_file(cfg.target_root(), p, content)
        if r.get("ok"):
            return {"ok": True, "data": {"path": p}, "saved": True, "path": p}
        return r
    if not p:
        return {"ok": False, "error": "请指定要编辑的文件（如: 编辑 bad_sample.py），或在编辑器修改后保存"}
    return {"ok": True, "data": {"path": p}, "saved": False, "path": p,
            "note": "请在代码工作区编辑器修改后保存（对话不编造代码）"}


def _a_config(data, cfg):
    msg = data.get("message") or ""
    # P1-9 真实切换目标仓库：含 切换/切到/换目录/target 意图且带目录路径 → 真正 set_target_root
    if any(k in msg for k in ("切换", "切到", "换目录", "切目录", "target", "目标仓库")):
        p = extract_dir_path(msg)
        if p:
            ok, real = cfg.set_target_root(p)
            if not ok:
                return {"ok": False, "error": real}
            return {"ok": True, "data": cfg.public(), "switched": True, "new_target": real}
        # 有切换意图但没解析出路径 → 诚实提示
        return {"ok": False, "error": "请给出要切换到的目录路径（如: 切换到 C:/some/repo）"}
    return {"ok": True, "data": cfg.public()}


def _a_answer(data, cfg):
    """模型兜底：未知意图 → 直接让模型回答/执行（数据不出私域：仅发往用户配置的端点）。"""
    from model_config import chat_completion
    r = chat_completion(None, [{"role": "user", "content": data.get("message", "")}], timeout=40)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "")}
    return {"ok": True, "data": {"answer": r.get("content", ""),
                                 "provider": r.get("provider"), "model": r.get("model")},
            "answer": r.get("content", "")}


def _a_unsupported(data, cfg):
    return {"ok": False, "error": "unsupported"}


import os  # noqa: E402

_ACTIONS = {
    "review_file": _a_review_file, "test_file": _a_test_file, "debug": _a_debug,
    "report": _a_report, "impact": _a_impact, "models": _a_models, "tree": _a_tree,
    "atoms": _a_atoms, "debug_history": _a_debug_history, "graph": _a_graph,
    "edit": _a_edit, "config": _a_config, "unsupported": _a_unsupported,
    "answer": _a_answer,
}