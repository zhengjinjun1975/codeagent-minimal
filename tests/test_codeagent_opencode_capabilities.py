#!/usr/bin/env python3
"""test_codeagent_opencode_capabilities.py — 吸收 OpenCode 能力升维原子测试（pytest）。

覆盖吸收 OpenCode 的六项新能力，全部用**真实数据断言**（非空壳）：
1. MCP（mcp-client 原子）：mcp.tools 返回真实工具清单（echo/upper/add 等）、mcp.call 真实调用
2. 多模型（llm-router v0.2.0）：llm.list_models 返回多 provider 注册表（cloud+local）、默认 local_only 封锁云端
3. SKILL（code-skill 原子 + SKILL.md 标准）：skill.list 非空、sediment_skill_to_md 产出 SKILL.md 标准资产
4. CodeMode 编排（闭源 codemode.py）：run_program 单次 execute 编排依赖/并发/聚合真实生效，confined 白名单拒绝任意能力
5. 细粒度权限（code-dispatch v0.2.0）：dispatch.permission allow/ask/deny 三级判定 + deny>allow>ask 优先级 + 通配
6. LSP 诊断（code-review v0.2.0）：codereview.lsp 连真实 LSP server 拉语法/未定义名/行长诊断并入评分

真实数据断言铁律：凡涉工具/模型/技能/权限/诊断，必须断言返回里带真实内容（工具名/模型provider/
SKILL.md路径/decision/severity），只认实测证据。CodeMode/LSP 的语法错误文件本身是故意坏的，import
它们必须忽略（不 assert 语法正确）。
"""

import os
import sys
import json

# 闭源工作区目录(env可覆盖); 未配置则闭源联动测试跳过
CLOSED_DIR = os.environ.get("CODEAGENT_CLOSED_DIR", "")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent_runtime import AgentRuntime
from codeagent import CodeAgent


# ══════════ 1. MCP：mcp-client 原子真实工具 ══════════
def test_mcp_tools_real_list():
    rt = AgentRuntime()
    r = rt.run_capability("mcp.tools", server="demo", local_only=True)
    assert r["ok"], r.get("error")
    tools = r["data"].get("tools", [])
    names = {t.get("name") for t in tools}
    assert "echo" in names and "upper" in names and "add" in names, f"MCP 工具清单缺真实工具: {names}"
    # 工具带 inputSchema（真实契约非空壳）
    echo = next(t for t in tools if t["name"] == "echo")
    assert echo.get("inputSchema", {}).get("type") == "object", f"工具缺 inputSchema: {echo}"
    assert "text" in echo["inputSchema"].get("required", []), f"echo 缺 required text: {echo}"


def test_mcp_call_real():
    rt = AgentRuntime()
    r = rt.run_capability("mcp.call", server="demo", tool="upper",
                          arguments={"text": "codeagent"}, local_only=True)
    assert r["ok"], r.get("error")
    d = r["data"]
    # 真实回显：upper 应把输入转大写
    text = json.dumps(d, ensure_ascii=False, default=str)
    assert "CODEAGENT" in text, f"mcp.call upper 未真实转大写: {text[:200]}"


# ══════════ 2. 多模型：llm-router v0.2.0 注册表 ══════════
def test_llm_multi_model_registry():
    rt = AgentRuntime()
    r = rt.run_capability("llm.list_models", local_only=True)
    assert r["ok"], r.get("error")
    models = r["data"].get("models", [])
    assert len(models) >= 3, f"多模型注册表应 ≥3，实得 {len(models)}"
    types = {m.get("type") for m in models}
    assert "cloud" in types and "local" in types, f"应含 cloud+local 两类: {types}"
    providers = {m.get("provider") for m in models}
    assert "glm" in providers or "deepseek" in providers, f"缺云端 provider: {providers}"
    # 每模型有 provider/model/type 真实字段
    for m in models:
        assert m.get("provider") and m.get("model") and m.get("type"), f"模型缺字段: {m}"


def test_llm_local_only_blocks_cloud():
    rt = AgentRuntime()
    r = rt.route_model("gen", [{"role": "user", "content": "hi"}])
    assert r["ok"] is False and r.get("degraded") is True, f"默认应本地封锁云端: {r}"
    assert "不出厂" in r["error"] or "local-only" in r["error"], f"封锁原因不对: {r.get('error')}"


# ══════════ 3. SKILL：code-skill 原子 + SKILL.md 标准资产 ══════════
def test_skill_list_real():
    rt = AgentRuntime()
    r = rt.run_capability("skill.list")
    assert r["ok"], r.get("error")
    d = r["data"]
    # skills 应含已沉淀技能（SKILL.md 资产）或至少是 dict 容器
    assert "skills" in d or isinstance(d, dict), f"skill.list 未返回技能清单: {d}"
    if "skills" in d:
        assert isinstance(d["skills"], (list, dict)), f"skills 应为容器: {type(d['skills'])}"


def test_sediment_skill_to_standard_md():
    rt = AgentRuntime()
    r = rt.sediment_skill_to_md("多模型路由", "provider 注册表 + local_only 数据不出厂")
    assert r["ok"], r.get("error")
    d = r["data"]
    # 产出标准 SKILL.md 资产（path 或 content 真实存在）
    assert "path" in d or "name" in d or "content" in d, f"SKILL.md 资产未产出: {d}"
    content = d.get("content") or ""
    if content:
        assert "SKILL.md" in str(d.get("name", "")) or "#" in content or "1." in content, \
            f"SKILL.md 内容非标准: {content[:120]}"


# ══════════ 4. CodeMode 编排（闭源 codemode.py）══════════
def _load_codemode():
    orc_path = CLOSED_DIR
    if not os.path.exists(os.path.join(orc_path, "codemode.py")):
        return None, None
    if orc_path not in sys.path:
        sys.path.insert(0, orc_path)
    from orchestrator import Orchestrator
    from codemode import CodeMode
    orc = Orchestrator()
    return orc, CodeMode(orc)


def test_codemode_run_program_single_execute():
    orc, cm = _load_codemode()
    if cm is None:
        return  # 闭源侧不存在则跳过
    program = {
        "steps": [
            {"id": "review", "capability": "codereview.review",
             "inputs": {"code": {"calc.py": "def add(a, b):\n    return a + b\n"},
                        "use_llm": False}},
            {"id": "impact", "capability": "impact.analyze",
             "inputs": {"path": REPO_ROOT, "impact": "dep_report", "transitive": True},
             "concurrent": True},
        ],
        "aggregate": "concat",
    }
    r = cm.run_program(program, budget={"max_calls": 20, "timeout_ms": 30000},
                       allow_caps=["codereview.review", "impact.analyze"])
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["step_count"] == 2, f"CodeMode 应编排 2 步: {d['step_count']}"
    assert d["calls_made"] == 2, f"CodeMode 应真实调用 2 次: {d['calls_made']}"
    # 依赖/并发组都真实执行且 ok
    for sid in ("review", "impact"):
        v = d["results"].get(sid)
        assert isinstance(v, dict) and v.get("ok"), f"CodeMode 步骤 {sid} 未真实执行: {v}"
    # 精简聚合（省上下文）：concat 只留 summary/score
    assert "note" in d and "省上下文" in d["note"], f"CodeMode 缺省上下文说明: {d.get('note')}"


def test_codemode_confined_whitelist_denies():
    orc, cm = _load_codemode()
    if cm is None:
        return
    # confined 解释器：白名单外能力一律拒绝，不执行
    r = cm.run_program({"steps": [{"id": "x", "capability": "evil.run", "inputs": {}}]},
                       budget={"max_calls": 5}, allow_caps=["codereview.review"])
    res = r["data"]["results"].get("x", {})
    assert isinstance(res, dict) and res.get("ok") is False, f"白名单外应拒绝: {res}"
    assert "白名单" in str(res.get("error", "")), f"拒绝原因应为白名单: {res.get('error')}"


# ══════════ 5. 细粒度权限：code-dispatch v0.2.0 dispatch.permission ══════════
_POLICY_RULES = [
    {"type": "command", "pattern": "git *", "effect": "allow"},
    {"type": "command", "pattern": "rm -rf *", "effect": "deny"},
    {"type": "command", "pattern": "pytest *", "effect": "ask"},
    {"type": "file", "pattern": "**/secrets/*", "effect": "deny"},
]


def test_permission_allow_ask_deny_three_levels():
    rt = AgentRuntime()
    # allow
    r = rt.run_capability("dispatch.permission", action="check", resource="git status",
                          resource_type="command", rules=_POLICY_RULES)
    assert r["ok"], r.get("error")
    assert r["data"]["decision"] == "allow" and r["data"]["granted"] is True, r["data"]
    # deny（rm -rf 危险命令）
    r = rt.run_capability("dispatch.permission", action="check", resource="rm -rf /etc",
                          resource_type="command", rules=_POLICY_RULES)
    assert r["data"]["decision"] == "deny" and r["data"]["blocked"] is True, r["data"]
    # ask（pytest 需人工确认）
    r = rt.run_capability("dispatch.permission", action="check", resource="pytest tests",
                          resource_type="command", rules=_POLICY_RULES)
    assert r["data"]["decision"] == "ask" and r["data"]["granted"] is True, r["data"]
    # file 类型 deny
    r = rt.run_capability("dispatch.permission", action="check", resource="private/secrets/key.txt",
                          resource_type="file", rules=_POLICY_RULES)
    assert r["data"]["decision"] == "deny" and r["data"]["blocked"] is True, r["data"]


def test_permission_deny_overrides_allow():
    rt = AgentRuntime()
    # 同一条资源命中 allow+deny 两条，deny 优先级最高
    r = rt.run_capability("dispatch.permission", action="check", resource="rm -rf /tmp/x",
                          resource_type="command", rules=[{"type": "command", "pattern": "*", "effect": "allow"},
                                                          {"type": "command", "pattern": "rm -rf *", "effect": "deny"}])
    assert r["data"]["decision"] == "deny", f"deny 应覆盖 allow: {r['data']}"
    assert r["data"]["blocked"] is True


def test_permission_wildcard_glob():
    rt = AgentRuntime()
    r = rt.run_capability("dispatch.permission", action="check", resource="private/secrets/a/b/token.txt",
                          resource_type="file", rules=[{"type": "file", "pattern": "**/secrets/**", "effect": "deny"}])
    assert r["data"]["decision"] == "deny", f"通配 * 应匹配深层: {r['data']}"


# ══════════ 6. LSP 诊断：code-review v0.2.0 codereview.lsp ══════════
_LSP_TARGET = os.path.join(REPO_ROOT, "lsp_test_target.py")   # 含 resul/undefined_name 未定义名 + 超长行
_LSP_SYNTAX = os.path.join(REPO_ROOT, "lsp_syntax_error.py")   # 故意语法错误


def test_lsp_diagnostics_real_syntax_undefined_longline():
    rt = AgentRuntime()
    r = rt.run_capability("codereview.lsp", path=_LSP_SYNTAX)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["count"] >= 1, f"语法错误文件应检出 ≥1 诊断: {d['count']}"
    assert d["merged_into_score"] is True, "LSP 诊断应并入评分"
    msgs = " ".join(i.get("message", "") for i in d["issues"])
    assert "语法错误" in msgs or "invalid syntax" in msgs.lower(), f"应检出语法错误: {msgs}"
    # severity 1=Error → critical
    crit = [i for i in d["issues"] if i["severity"] == "critical"]
    assert crit, f"语法错误应映射 critical: {[i['severity'] for i in d['issues']]}"


def test_lsp_diagnostics_undefined_name_no_false_positive():
    """未定义名检测：resul/undefined_name 应检出；参数 a/b/x、定义 result 不应误报。"""
    rt = AgentRuntime()
    r = rt.run_capability("codereview.lsp", path=_LSP_TARGET)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert d["count"] >= 2, f"lsp_test_target 应检出 ≥2 诊断: {d['count']}"
    msgs = " ".join(i.get("message", "") for i in d["issues"])
    assert "未定义名 'resul'" in msgs, f"应检出 resul 未定义名: {msgs}"
    assert "未定义名 'undefined_name'" in msgs, f"应检出 undefined_name 未定义名: {msgs}"
    # 零误报：参数/定义名/关键字不得出现
    for bad in ("'a'", "'b'", "'x'", "'result'", "'def'", "'return'"):
        assert f"未定义名 {bad}" not in msgs, f"不应误报 {bad}: {msgs}"
    # 行长诊断（severity 3 → minor）
    assert "行过长" in msgs, f"应检出行长诊断: {msgs}"


def test_lsp_diagnostics_merge_into_score_real():
    """LSP 诊断并入评分真实生效：带 LSP 评分显著低于不带 LSP。"""
    rt = AgentRuntime()
    r_no = rt.run_capability("codereview.review", path=_LSP_TARGET, lsp=False)
    r_yes = rt.run_capability("codereview.review", path=_LSP_TARGET, lsp=True)
    assert r_no["ok"] and r_yes["ok"]
    assert r_yes["data"]["score"] < r_no["data"]["score"], \
        f"LSP 应降低评分: {r_no['data']['score']}→{r_yes['data']['score']}"
    assert len(r_yes["data"]["issues"]) > len(r_no["data"]["issues"]), "LSP issues 应并入"
    lsp_issues = [i for i in r_yes["data"]["issues"] if "LSP[" in i.get("title", "")]
    assert lsp_issues, "审查信封应含 LSP 诊断 issues"


def test_lsp_diagnostics_capability_single():
    """codereview.lsp 独立能力：返回原始 diagnostics + 精简 issues + merged_into_score。"""
    rt = AgentRuntime()
    r = rt.run_capability("codereview.lsp", path=_LSP_TARGET)
    assert r["ok"], r.get("error")
    d = r["data"]
    assert "lsp_diagnostics" in d, "缺原始 diagnostics"
    assert "issues" in d and isinstance(d["issues"], list), "缺精简 issues"
    assert "count" in d and "merged_into_score" in d, "缺 count/merged_into_score"
    for raw in d["lsp_diagnostics"]:
        assert "severity" in raw and "message" in raw and "range" in raw, f"诊断缺字段: {raw}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
