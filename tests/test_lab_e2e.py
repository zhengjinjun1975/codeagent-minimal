#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_lab_e2e.py — CodeAgent Lab 壳层端到端测试(独立目录, 不碰既有 172 测试)。

覆盖(全部真实执行, 不 mock):
  01 健康/原子库(32核心)         02 浏览(tree/file/methods/graph)
  03 安全回归(路径穿越/跨源/body 413)  04 新原子扩展(scaffold/register/selfcheck)
  05 拖拽编排(单节点/双节点链/环检测)  06 黑箱调试(真实oracle失败→历史沉淀→复用命中→修复)
  07 模型配置(脱敏/诚实连通测试)     08 审查报告(原子聚合/分级/结构/HTML/MD导出)
  09 编辑保存/事件流/审计/管道历史

用法: pytest tests/test_lab_e2e.py -v
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest

LAB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lab"))
if LAB not in sys.path:
    sys.path.insert(0, LAB)

from lab_config import LabConfig, set_config_for_tests  # noqa: E402
from history_db import HistoryDB  # noqa: E402
from web_viz import serve  # noqa: E402
import debug_history as dh  # noqa: E402


@pytest.fixture(scope="module")
def lab():
    """模块级: 临时配置/DB/扩展目录全隔离, 真实起 HTTP 服务(port=0 自选端口)。"""
    td = tempfile.mkdtemp(prefix="lab_e2e_")
    cfg = LabConfig()
    cfg.path = os.path.join(td, "lab_config.json")
    for k in ("db_path", "report_dir", "backup_dir", "models_path",
              "known_defects_extra"):
        cfg.values[k] = os.path.join(td, os.path.basename(cfg.values[k]))
    cfg.values["extensions_dir"] = os.path.join(td, "ext")
    cfg.values["extensions_registry"] = os.path.join(td, "ext_reg.json")
    cfg.values["port"] = 0
    cfg.values["token"] = ""
    cfg.values["models_path"] = os.path.join(td, "models.json")
    set_config_for_tests(cfg)

    db = HistoryDB(cfg.get("db_path"))
    server, port = serve(cfg=cfg, db=db)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    # 独立临时目标仓库(黑箱调试/报告/保存只作用于它)
    tgt = os.path.join(td, "target")
    os.makedirs(tgt, exist_ok=True)
    # 注意: 不在此处 set_target_root —— 浏览测试先看原子库自身(bad_sample.py),
    # test_06 调试前再切换到独立目标仓库(与 smoke 配方一致)
    # 注意: scaffold 名避开 "lab-echo" 前缀 —— atom_loader_ext 会刻意过滤该前缀
    # (演示/回显产物不进生产调色板)。用独立 echo 前缀让测试真实验证 scaffold→register→run。
    ext = f"scaf-echo{int(time.time()) % 100000}"
    cap = f"echo.summarize{int(time.time()) % 100000}"
    yield {"cfg": cfg, "db": db, "server": server, "base": base, "td": td,
           "tgt": tgt, "ext": ext, "cap": cap}
    server.shutdown()


def get(lab, p, timeout=60):
    with urllib.request.urlopen(lab["base"] + p, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_text(lab, p, timeout=60):
    """非 JSON 端点（报告 HTML/MD 导出等自包含文件）原始文本读取。"""
    with urllib.request.urlopen(lab["base"] + p, timeout=timeout) as r:
        return r.read().decode("utf-8")


def post(lab, p, body, timeout=120):
    req = urllib.request.Request(lab["base"] + p,
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_run(lab, kind, rid, limit=160, sleep=0.5):
    st = None
    for _ in range(limit):
        st = get(lab, f"/api/{kind}/{rid}")["data"]
        if st["status"] != "running":
            return st
        time.sleep(sleep)
    return dict(st or {}, status="timeout")


class TestLabE2E:
    # ── 01 健康/原子库 ────────────────────────────
    def test_01_health_atoms(self, lab):
        h = get(lab, "/api/health")
        assert h["ok"] is True
        cfg = get(lab, "/api/config")["data"]
        assert cfg["target_root"]
        reg = get(lab, "/api/atoms")["data"]
        assert reg["core_count"] >= 34, f"core={reg['core_count']}"
        assert reg["count"] >= 34

    # ── 02 浏览 ───────────────────────────────────
    def test_02_browse(self, lab):
        t = get(lab, "/api/tree?recursive=1&badges=1")
        assert t["ok"]
        f = get(lab, "/api/file?path=bad_sample.py&view=methods")
        assert f["ok"] and len(f["data"]["methods"]) >= 3
        g = get(lab, "/api/graph?kind=deps")
        assert g["ok"] and len(g["data"]["nodes"]) > 5

    # ── 03 安全回归 ───────────────────────────────
    def test_03_security(self, lab):
        with pytest.raises(urllib.error.HTTPError) as e1:
            get(lab, "/api/file?path=../registry.json")
        assert e1.value.code in (400, 404)
        with pytest.raises(urllib.error.HTTPError) as e2:
            post(lab, "/api/file/save", {"path": "../evil.py", "content": "x=1"})
        assert e2.value.code in (400, 404)
        req = urllib.request.Request(lab["base"] + "/api/health",
                                     headers={"Origin": "http://evil.example.com"})
        with pytest.raises(urllib.error.HTTPError) as e3:
            urllib.request.urlopen(req, timeout=10)
        assert e3.value.code == 403
        req = urllib.request.Request(lab["base"] + "/api/chat",
                                     data=("x" * (1024 * 1024 + 10)).encode(),
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e4:
            urllib.request.urlopen(req, timeout=10)
        assert e4.value.code == 413

    # ── 04 新原子扩展 ─────────────────────────────
    def test_04_atom_extension(self, lab):
        r = post(lab, "/api/atoms/scaffold", {
            "name": lab["ext"], "domain": "demo", "capability": lab["cap"],
            "inputs": ["input"], "description": "回显测试"})
        assert r["ok"], r.get("error")
        r = post(lab, "/api/atoms/register", {"name": lab["ext"]})
        assert r["ok"], r.get("errors")
        reg2 = get(lab, "/api/atoms")["data"]
        assert reg2["ext_count"] >= 1 and any(a["name"] == lab["ext"]
                                              for a in reg2["atoms"])
        sc = get(lab, "/api/atoms/selfcheck")["data"]
        assert sc["ok"] and any(e["name"] == lab["ext"] and e["registered"]
                                for e in sc["extensions"])
        main_py = os.path.join(lab["cfg"].get("extensions_dir"),
                               lab["ext"], "main.py")
        assert os.path.isfile(main_py)
        assert os.path.isfile(os.path.join(os.path.dirname(main_py),
                                           "manifest.json"))

    # ── 05 拖拽编排 ───────────────────────────────
    def test_05_pipeline(self, lab):
        g1 = {"nodes": [{"id": "n1", "atom": lab["ext"], "capability": lab["cap"],
                         "params": {"input": "hello lab"}, "label": "echo"}],
              "edges": []}
        r = post(lab, "/api/pipeline/run", {"name": "p1", "graph": g1})
        assert r["ok"], r.get("error")
        st = wait_run(lab, "pipeline", r["data"]["run_id"])
        assert st["status"] == "success", st["status"]
        assert "hello lab" in json.dumps(st["results"].get("n1", {}),
                                         ensure_ascii=False)
        # 双节点链 + 边数据流
        g2 = {"nodes": [
            {"id": "a", "atom": lab["ext"], "capability": lab["cap"],
             "params": {"input": "first"}, "label": "a"},
            {"id": "b", "atom": lab["ext"], "capability": lab["cap"],
             "params": {"input": "second"}, "label": "b"}],
            "edges": [{"from": "a", "to": "b", "input_name": "input",
                       "map": "data"}]}
        r = post(lab, "/api/pipeline/run", {"name": "p2", "graph": g2})
        st = wait_run(lab, "pipeline", r["data"]["run_id"])
        assert st["status"] == "success", f"{st['status']} {st.get('error')}"
        b_env = st["results"].get("b", {})
        assert b_env.get("ok") and "first" in json.dumps(b_env.get("data", {}),
                                                         ensure_ascii=False)
        # 核心原子入链(真实能力子进程)
        g3 = {"nodes": [
            {"id": "v1", "atom": "minimalist-style", "capability": "minimal.deps",
             "params": {"path": lab["cfg"].codeagent_root()}, "label": "极简"},
            {"id": "v2", "atom": "code-project", "capability": "project.load",
             "params": {"path": lab["cfg"].codeagent_root()}, "label": "项目"}],
            "edges": []}
        r = post(lab, "/api/pipeline/run", {"name": "p3", "graph": g3})
        st = wait_run(lab, "pipeline", r["data"]["run_id"], limit=200)
        assert st["status"] in ("success", "partial"), st["status"]
        # 依赖环拒绝(不执行)
        loop = {"nodes": [{"id": "x", "atom": lab["ext"], "capability": lab["cap"],
                           "params": {"input": "1"}, "label": "x"},
                          {"id": "y", "atom": lab["ext"], "capability": lab["cap"],
                           "params": {"input": "2"}, "label": "y"}],
                "edges": [{"from": "x", "to": "y", "input_name": "input"},
                          {"from": "y", "to": "x", "input_name": "input"}]}
        from pipeline import validate
        from atom_loader_ext import merged_registry
        ok, errors, _ = validate(loop, merged_registry(lab["cfg"].codeagent_root()))
        assert not ok and any("环" in e for e in errors)

    # ── 06 黑箱调试 ───────────────────────────────
    def test_06_debug_blackbox(self, lab):
        # 切换到独立目标仓库（与 smoke 配方一致；调试/报告/保存都只作用于它，
        # 并为后续 test_09 保存测试提供一致的 target_root）
        assert lab["cfg"].set_target_root(lab["tgt"])[0]
        demo = os.path.join(lab["tgt"], "demo_bug.py")
        with open(demo, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("def multiply(a, b):\n    return a + b  # bug: 应为 a*b\n")
        failure_txt = "demo_bug.multiply(2,3) 断言失败 AssertionError: 期望 6 实际 5"
        oracle = "python -c \"import demo_bug; assert demo_bug.multiply(2,3)==6\""
        r2 = post(lab, "/api/debug", {"failure": failure_txt, "file": "demo_bug.py",
                                      "test_cmd": oracle})
        assert r2["ok"]
        st2 = wait_run(lab, "debug", r2["data"]["run_id"], limit=120)
        steps2 = {s["state"] for s in st2["steps"]}
        # 无模型: 诚实失败(绝不虚构"修好了"), 且 REPRO 有真实失败证据
        assert st2["status"] == "failed", st2["status"]
        assert "REPRO" in steps2 and "FIX" in steps2
        assert any("rc=1" in s["evidence"] or "测试失败" in s["evidence"]
                   or "AssertionError" in s["evidence"] or "Traceback" in s["evidence"]
                   for s in st2["steps"])
        # 历史已沉淀
        assert len(get(lab, "/api/debug/history")["data"]["hits"]) >= 1
        # 播种"过去调试结果"(同类失败曾被修复过) → 同形命中 → 重试复用补丁
        lab["db"].add_debug(
            failure_txt, "demo_bug.py",
            "python -c \"import demo_bug; assert demo_bug.multiply(2,3)==6\"",
            "AssertionError", ["multiply"], [{"state": "FIX", "evidence": "历史补丁复用"}],
            json.dumps({"replacements": [{"line_start": 2, "line_end": 2,
                                          "new_lines": "    return a * b"}]}),
            True, "回归通过: rc=0", 1, "seed2")
        hits = dh.find_hits(lab["db"], failure_txt, "demo_bug.py", ["multiply"])
        assert len(hits) > 0 and bool(hits[0]["hit"].get("patch")), \
            f"hits={len(hits)}"
        r3 = post(lab, "/api/debug/retry",
                  {"id": lab["db"].list_debug("", 1)[0]["id"]})
        assert r3["ok"]
        st3 = wait_run(lab, "debug", r3["data"]["run_id"], limit=120)
        assert st3["status"] == "success", st3["status"]
        assert any("历史补丁" in s["evidence"] for s in st3["steps"])
        with open(demo, encoding="utf-8") as fh:
            fixed = fh.read()
        assert "return a * b" in fixed, "文件确实被自动修复"

    # ── 07 模型配置 ───────────────────────────────
    def test_07_models(self, lab):
        r = post(lab, "/api/models", {"providers": [{
            "id": "ollama", "name": "本地Ollama", "type": "local",
            "base_url": "http://127.0.0.1:11434",
            "api_key": "sk-1234567890abcdef", "active": True}],
            "chain": ["ollama"]})
        assert r["ok"], r.get("error")
        m = get(lab, "/api/models")["data"]
        assert m["providers"][0]["api_key_status"] == "sk-1***ef", \
            m["providers"][0]["api_key_status"]
        # 空串/含* 保留旧值
        r = post(lab, "/api/models", {"providers": [{
            "id": "ollama", "name": "本地Ollama", "type": "local",
            "base_url": "http://127.0.0.1:11434", "api_key": "***",
            "active": True}], "chain": ["ollama"]})
        assert r["ok"]
        m = get(lab, "/api/models")["data"]
        assert m["providers"][0]["has_key"] is True
        # 连通测试诚实性：用一个"未配置的 provider"探测 → 必须诚实返回失败，
        # 绝不编造绿。不依赖本机是否安装 Ollama（本机若装了 Ollama 真实可连，
        # 诚实结果本就是成功，故用未配置 provider 验证"不编造"这一性质）。
        mt = post(lab, "/api/models/test", {"provider_id": "ghost-provider-not-configured"})
        assert (mt.get("ok") is False) or (mt.get("data", {}).get("ok") is False)

    # ── 08 审查报告 ───────────────────────────────
    def test_08_report(self, lab):
        r = post(lab, "/api/report", {"path": "."})
        assert r["ok"], r.get("error")
        st = wait_run(lab, "report", r["data"]["report_id"], limit=300)
        assert st["status"] == "success", st["status"]
        assert len(st.get("sections", [])) >= 3, f"sections={len(st.get('sections',[]))}"
        for f_ in st.get("findings", []):
            assert f_["level"] in ("P0", "P1", "P2")
        assert st.get("structure"), "代码结构章节"
        html = get_text(lab, f"/api/report/{r['data']['report_id']}.html")
        assert isinstance(html, str) and "<html" in html
        md = get_text(lab, f"/api/report/{r['data']['report_id']}.md")
        assert isinstance(md, str) and ("报告" in md or "#" in md)

    # ── 09 保存/事件/审计 ─────────────────────────
    def test_09_edit_events_audit(self, lab):
        fpath = "hello_lab.py"
        with open(os.path.join(lab["tgt"], fpath), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("def hello():\n    return 'hi'\n")
        r = post(lab, "/api/file/save", {"path": fpath,
                                         "content": "def hello():\n    return 'hi lab'\n"})
        assert r["ok"] and r["data"]["compile_ok"], r.get("error")
        with open(os.path.join(lab["tgt"], fpath), encoding="utf-8") as fh:
            assert "hi lab" in fh.read()
        r = post(lab, "/api/file/save", {"path": fpath,
                                         "content": "def broken(:\n    pass\n"})
        assert not r["ok"], "语法错误必须拒绝保存"
        ev = get(lab, "/api/events?since=0")["data"]
        types = {e["type"] for e in ev["events"]}
        assert "pipeline.finished" in types and "atom.registered" in types
        assert "report.finished" in types
        assert any(e["action"] == "save" for e in
                   get(lab, "/api/editlog")["data"]["edits"])
        assert len(get(lab, "/api/pipelines")["data"]["runs"]) >= 1

    # ── 10 对话路由 ───────────────────────────────
    def test_10_chat(self, lab):
        r = post(lab, "/api/chat", {"message": "帮我总结这段代码", "history": []})
        d = r["data"]
        assert d["intent"] == "unknown" or "可用" in d["reply"], d["reply"][:80]
        r = post(lab, "/api/chat", {"message": "生成审查报告", "history": []})
        assert r["data"]["intent"] == "report"
        r = post(lab, "/api/chat", {"message": "调试 demo_bug.py", "history": []})
        assert r["data"]["intent"] == "debug"
        r = post(lab, "/api/chat", {"message": "部署到生产环境", "history": []})
        assert r["data"]["intent"] == "unsupported"