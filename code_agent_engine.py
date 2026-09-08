"""
CodeAgentEngine — 写码引擎库(自包含, 单引擎家), 唯一承载于本仓库。

架构位: 本模块是本仓库**唯一的写码引擎实现**，被 code-implement 原子
(code.implement/code.write)等 import，作为其真实自包含实现。

承载内容(逻辑原样, 只改路径/承载):
- class CodeAgent: implement / think / review(多模) / test / load_project /
  reuse_search / auto_reuse_search / analyze_project / scan_issues / batch_review /
  _refine_experience / _log_to_obsidian 落知识库
- PONYTAIL_SYSTEM 写码纪律 + auto_reuse 复用 + loop 迭代
- 静态分析(AST 语法/import/圈复杂度/命名/SQL注入/命令注入/密钥/反序列化/路径穿越)
- 模型调用: generate 读同目录 config/model_config.json(openai/deepseek)，审查走确定性路由

版本: v1.4。中文注释。
"""


import os, sys, json, ast, re, subprocess
import urllib.request
from pathlib import Path
_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)
# 本仓库根下的 obsidian_ops 为唯一实现源(未配置知识库时其函数静默降级返回空/[])
from obsidian_ops import inject_context, quality_report, daily_summary

# ── 共享记忆(可选外部)本地桥: 跨库硬引用整改 ─────────────────────
# 铁律: 禁止本地引用——引擎不再 sys.path 指向外部库路径去
# import optmem_mem(那会让引擎顶层 import 崩于外部目录缺失)。改为加载**本仓库内**
# code-memory 原子自带的零依赖本地桥 agents/memory/code-memory/optmem_mem.py:
# 纯 stdlib subprocess 包装 memo/memo_search, 不 import 任何外部 Python 模块;
# 外部共享记忆库仅作**数据目录**经 env OPTMEM_MEMO_DIR 注入(未指向则该桥 available()=False,
# save/recall 静默降级不崩)。此桥 .save/.recall 与引擎用法兼容(见 tests/conftest 同款载入)。
def _load_local_optmem_bridge():
    import importlib.util as _ilu
    _p = os.path.join(_ENGINE_DIR, "agents", "memory", "code-memory", "optmem_mem.py")
    if os.path.isfile(_p):
        try:
            _spec = _ilu.spec_from_file_location("optmem_mem", _p)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            return _mod
        except Exception:
            return None
    return None

optmem_mem = _load_local_optmem_bridge()   # 载入失败→None, 上层所有调用均在 try 内静默降级
from memory_gate import should_share as _gate_share   # 共享质量闸单源(与 code-memory 原子复用, 不复制两份)

# ── 确定性模型路由(unified_client) ──
# code_review 维度走确定性路由(本地next优先, 数据不出厂), 借用路由+format=json+降级链, 不做其schema校验(返回兼容 content)
# 确定性路由(unified_client)仅在 env UNIFIED_CLIENT_DIR 注入其独立部署目录时启用;
# 未设置/不可用→降级返回"路由不可用"(不改业务)。
_ROUTER_CLIENT = None
def _call_router(messages, dim="code_review"):
    global _ROUTER_CLIENT
    try:
        _rd = (os.environ.get("UNIFIED_CLIENT_DIR", "") or "").strip()
        if not _rd or not os.path.isdir(_rd):
            return {"error": "router不可用(未设 UNIFIED_CLIENT_DIR)", "content": ""}
        if _rd not in sys.path:
            sys.path.insert(0, _rd)
        import unified_client as _router_uc
        if _ROUTER_CLIENT is None:
            _ROUTER_CLIENT = _router_uc.UnifiedClient()
        obj, meta = _ROUTER_CLIENT.complete(messages, dim, validate=False)
        return {"content": json.dumps(obj, ensure_ascii=False), "meta": meta}
    except Exception as e:
        return {"error": str(e), "content": ""}

# ── 共享记忆(代码经验桥) ──────────────────────
def memory_save(text, confidence=0.5):
    """沉淀一条代码经验进共享代码经验记忆库(带域前缀 + 置信度标记), 供跨会话检索复用。失败静默降级。
    confidence: 0.0-1.0, 0.3试探/0.5普通/0.7较可信/0.9近乎确定。

    写前过与 code-memory 原子**同一道**质量闸 memory_gate.should_share
    (拦 conf<0.5 / 明显噪音标记; 本路径无 severity 概念, 传 None 交闸按 conf+噪音判)。
    拦下返回 (False, 'filtered:<reason>') 且**不调 optmem_mem.save**(不进共享记忆); 通过才写。
    """
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
        share, reason = _gate_share(text, severity=None, conf=confidence)
        if not share:
            return False, f"filtered:{reason}"   # 低质/试探/噪音 → 不进共享(本地/业务照常)
        tagged = f"[conf:{confidence:.1f}] {text}"
        return optmem_mem.save(tagged)
    except Exception:
        return False, "降级跳过"

def memory_recall(q, top_k=4):
    """检索共享代码经验记忆库历史代码经验(域优先 + 置信度从高到低), 失败返回空列表(不阻断)。"""
    try:
        ok, hits = optmem_mem.recall(q, top_k * 3)  # 多取一些再按置信度排序
        if not ok:
            return True, []
        def _conf(line):
            m = re.search(r"\[conf:([0-9.]+)\]", line)
            return float(m.group(1)) if m else 0.5  # 无标记按 0.5
        hits.sort(key=lambda l: (-_conf(l), l))  # 置信度高排前
        return True, hits[:top_k]
    except Exception:
        return True, []
# ── 自动工作日志 ──────────────────────────────────

def _log_to_obsidian(task_type: str, task: str, result: dict):
    """将工作日志写入知识库(daily/codeagent 目录)，供后续每日汇总查阅。

    仅当 env CODEAGENT_OBSIDIAN_VAULT 已配置且目录真实存在时落库；
    未配置/目录缺失 → 静默跳过（不建目录、不抛异常）。
    """
    vault = (os.environ.get("CODEAGENT_OBSIDIAN_VAULT", "") or "").strip()
    if not vault or not os.path.isdir(vault):
        return
    from datetime import datetime
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        slug = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff_-]', '_', task[:30])
        path = os.path.join(vault, "daily", "codeagent", f"{today}_{slug}.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        files = list(result.get("files", {}).keys()) if isinstance(result.get("files"), dict) else []
        issues = result.get("issues", [])
        summary = result.get("summary", result.get("plan", ""))

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"""---
created: {today}
type: work-log
task: {task_type}
---

# {task_type} — {task[:60]}

**时间**: {today}
**任务**: {task}
**结果**: {summary}

## 产出
""")
            if files:
                f.write("\n".join(f"- `{p}`" for p in files) + "\n")
            if issues:
                f.write("\n## 发现的问题\n")
                for i in issues[:5]:
                    f.write(f"- [{i.get('severity','?')}] {i.get('title','')}\n")
            f.write(f"\n[[daily/{today}|{today}的工作日志]]\n")
    except Exception:
        return


# ── 配置 ───────────────────────────────────────────
def _read_env(key):
    """仅从进程环境变量读取 key（不读任何个人 .env / 外部凭据文件）。"""
    return os.environ.get(key, "")

def _load_model_config():
    """从 config/model_config.json 读取模型配置（复用 ontology 已验证模式）。"""
    import json
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "model_config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_MCFG = _load_model_config()
_gen = _MCFG.get("generate", {})
_rev = _MCFG.get("review", {})
ZHIPU_KEY = os.environ.get("ZHIPU_API_KEY", "") or _read_env("ZHIPU_API_KEY")
ZHIPU_URL = _gen.get("base_url", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
ZHIPU_MODEL = _gen.get("model", "glm-4-flash")
# 注: review 段已从 model_config.json 移除(死配置, 审查不读本文件)。
# 代码审查/测试/安全 审查通道 = _call_router → 确定性路由(_call_router 按 env UNIFIED_CLIENT_DIR 启用)。
# 下面 OLLAMA_* 仅服务 generate.type=ollama 时的本地生成降级(_call_ornith), 与审查无关。
OLLAMA_URL = _rev.get("base_url", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = _rev.get("model", "ornith:latest")


def _resolve_key(cfg):
    """从后端配置解析 api_key。值若指向环境变量名(如 DEEPSEEK_API_KEY)则取该环境变量。"""
    k = cfg.get("api_key", "")
    if not k:
        return ""
    if k in os.environ:
        return os.environ[k]
    return _read_env(k)


def _call_zhipu(messages, temp=0.3, max_tokens=8192):
    # 从 generate 配置取(兼容智谱/DeepSeek/OpenAI): base_url/model/api_key
    url = _gen.get("base_url", ZHIPU_URL)
    model = _gen.get("model", ZHIPU_MODEL)
    key = (_resolve_key(_gen) or os.environ.get("DEEPSEEK_API_KEY", "")
           or _read_env("DEEPSEEK_API_KEY") or ZHIPU_KEY)
    if not key:
        return {"error": "generate API_KEY 未配置"}
    try:
        # 绕开系统代理(Clash 33210 常未连节点): proxies=None 直连云端
        r = _http_post_json(url,
            {"model": model, "messages": messages, "temperature": temp,
             "max_tokens": max_tokens, "thinking": {"type": "disabled"}},
            {"Authorization": f"Bearer {key}"})
        return r["choices"][0]["message"]
    except Exception as e:
        return {"error": str(e)}


def _call_ornith(messages, temp=0.4):
    # 审查后端: review.type==ollama→本地Ollama; 否则→远端OpenAI兼容(DeepSeek)
    if _rev.get("type", "ollama") != "ollama":
        url = _rev.get("base_url", "https://api.deepseek.com/v1/chat/completions")
        model = _rev.get("model", "deepseek-v4-flash")
        key = (_resolve_key(_rev) or os.environ.get("DEEPSEEK_API_KEY", "")
               or _read_env("DEEPSEEK_API_KEY"))
        if not key:
            return {"error": "review API_KEY 未配置", "content": ""}
        try:
            r = _http_post_json(url,
                {"model": model, "messages": messages, "temperature": temp, "max_tokens": 4096},
                {"Authorization": f"Bearer {key}"})
            return r["choices"][0]["message"]
        except Exception as e:
            return {"error": str(e), "content": ""}
    try:
        r = _http_post_json(OLLAMA_URL,
            {"model": OLLAMA_MODEL, "messages": messages, "stream": False,
             "options": {"temperature": temp}})
        return r["message"]
    except Exception as e:
        return {"error": str(e), "content": ""}


def _http_post_json(url, payload, headers=None, timeout=120):
    """纯标准库 POST JSON 并解析返回 dict(无代理直连, 等价原 requests.post)。失败抛异常由调用方 try 捕获。"""
    _h = {"Content-Type": "application/json"}
    if headers:
        _h.update(headers)
    _req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                  headers=_h, method="POST")
    _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 无代理直连
    with _opener.open(_req, timeout=timeout) as _resp:
        return json.loads(_resp.read().decode("utf-8"))


def _call_generate(messages, temp=0.3, max_tokens=32000):
    """按 model_config.generate.type 选择生成模型。
    - openai: 远端 OpenAI 兼容端点（智谱等）
    - ollama: 本地 Ollama（或任意 OpenAI 兼容）
    修复: implement/think 原来硬编码 _call_zhipu, 忽略 config 的 generate 配置,
    导致本地模型可用时仍走远端、远端代理不通则必失败。
    """
    gtype = _gen.get("type", "openai")
    if gtype == "ollama":
        return _call_ornith(messages, temp=temp)
    return _call_zhipu(messages, temp=temp, max_tokens=max_tokens)

def _ensure_key():
    global ZHIPU_KEY
    if not ZHIPU_KEY: ZHIPU_KEY = _read_env("ZHIPU_API_KEY")
    return ZHIPU_KEY


# ── Karpathy 原则注入 ──────────────────────────────

PONYTAIL_SYSTEM = """你是懒人高级开发者。写代码前爬这个阶梯：
1. YAGNI → 不建
2. 代码库已有 → 复用
3. 标准库有 → 用
4. 平台原生有 → 用
5. 已装依赖有 → 用
6. 一行能搞定 → 一行
7. 不行才写最少代码

硬规则：不加未要求的抽象/依赖/样板。删除>添加。最短diff胜出。
不可偷懒：验证/安全/数据保护。非平凡代码留assert或测试。

禁止去绿捷径（自写码自验收最容易骗绿）：
- "测试以后加"→以后不会加，自测是任务一部分
- "测试过了就够了"→绿测试不覆盖架构/安全/可读性/兄弟调用方
- "先这样下次清理"→延迟清理不发生，一次做干净
- "复现不了但我确信"→复现不了就别宣称确信修复
- "能编译就对"→验证不可协商，给可运行证据
- "文档说API这么用"→版本漂移，读真实依赖文件，找不到标UNVERIFIED别编
- "我跑通了你验收吧"→自写码自验收=循环性，产出让外部独立体复核

对抗式复核：背靠背复核只递被测物+验收契约，剥离自己的推理与结论；复核prompt明确"找问题、不验证、不总结"。收到结果按 契约误读/有效可行动/有效取舍/噪音 四类归类，连续多轮实质动作却0可行动=走形式点赞。"""


# ═══════════════════════════════════════════════════
# 静态分析工具函数（零模型调用，纯 AST）
# ═══════════════════════════════════════════════════

def _static_check_syntax(content: str) -> list:
    """语法检查：返回语法错误列表"""
    try:
        ast.parse(content)
        return []
    except SyntaxError as e:
        return [{"severity":"critical","title":f"语法错误: {e.msg}","line":e.lineno,"suggestion":str(e)}]


def _static_check_imports(tree, content: str) -> list:
    """检查 import 可达性和未使用"""
    issues = []
    imports = {}
    used_names = set()

    for node in ast.walk(tree):
        # 收集所有 import
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                imports[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                imports[name] = node.lineno
        # 收集所有名称引用
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.add(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used_names.add(node.value.id)

    for name, lineno in imports.items():
        base = name.split(".")[0]
        if base not in used_names:
            issues.append({"severity":"minor","title":f"未使用的 import: {name}","line":lineno,"suggestion":"删除未使用的 import"})

    return issues


def _static_check_complexity(tree) -> list:
    """圈复杂度检查：超过阈值则标记"""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            complexity = 1
            for n in ast.walk(node):
                if isinstance(n, (ast.If, ast.While, ast.For, ast.ExceptHandler,
                                  ast.And, ast.Or, ast.Assert)):
                    complexity += 1
                elif isinstance(n, (ast.Try,)):
                    complexity += 1
            if complexity > 10:
                issues.append({"severity":"major","title":f"圈复杂度 {complexity} > 10: {node.name}",
                              "line":node.lineno,"suggestion":"考虑拆分为多个小函数"})
    return issues


def _static_check_naming(tree) -> list:
    """命名规范检查：函数/类命名"""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            if not re.match(r'^[a-z_]\w*$', node.name):
                issues.append({"severity":"minor","title":f"函数名建议小写: {node.name}",
                              "line":node.lineno,"suggestion":f"改名 {node.name.lower()}"})
        elif isinstance(node, ast.ClassDef):
            if not re.match(r'^[A-Z]\w*$', node.name):
                issues.append({"severity":"minor","title":f"类名建议大写开头: {node.name}",
                              "line":node.lineno,"suggestion":f"改名 {node.name.capitalize()}"})
    return issues


def _static_check_security(content: str) -> list:
    """静态安全检查：SQL注入/命令注入/eval-exec/硬编码密钥/危险反序列化"""
    issues = []
    # SQL 注入（字符串拼接进 SQL）
    # 防自匹配误报：先剔除"检测规则/正则定义行"，避免正则匹配到规则自身文本；
    # 负向后行断言排除属性调用(如 path.insert)里的增删改词；负向断言排除 re.search/re.compile 语境
    _sql_scan = "\n".join(
        ln for ln in content.splitlines()
        if not re.search(r're\.(search|compile)|_static_check_security', ln)
    )
    if re.search(r'(?<![\w.])\b(SELECT|INSERT|UPDATE|DELETE)\b(?![\\w\\s]*re\.(search|compile)).*?(f["\']|\+\s*["\'a-zA-Z_]|%["\']|\.format\(|\{[^}]*\})', _sql_scan, re.I | re.S):
        issues.append({"severity": "critical", "title": "SQL 注入风险",
                       "suggestion": "用参数化查询/占位符，避免将变量直接拼进 SQL"})
    # 命令注入
    if re.search(r'subprocess\.[a-z]+\([^)]*shell\s*=\s*True', content, re.I):
        issues.append({"severity": "critical", "title": "命令注入风险(shell=True)",
                       "suggestion": "避免 shell=True；用参数列表传命令，勿拼接用户输入"})
    if re.search(r'os\.system\s*\([^)]*[\+\{]', content, re.I):
        issues.append({"severity": "major", "title": "命令拼接风险",
                       "suggestion": "os.system 传动态字符串易注入，改用 subprocess 参数列表"})
    # 危险 eval/exec
    if re.search(r'\beval\s*\([^)]*\)|\bexec\s*\([^)]*\)', content):
        issues.append({"severity": "major", "title": "不安全的 eval/exec",
                       "suggestion": "避免对不可信输入执行 eval/exec；用 ast.literal_eval 等安全替代"})
    # 硬编码密钥
    if re.search(r'\b(password|passwd|secret|api_key|apikey|token|client_secret)\s*=\s*["\'][^"\']{6,}', content, re.I):
        issues.append({"severity": "major", "title": "硬编码密钥/密码",
                       "suggestion": "密钥不要写死在代码，改用环境变量/配置文件"})
    # 危险反序列化
    if re.search(r'pickle\.loads|yaml\.load\s*\([^)]*\)(?!\s*,\s*Loader)', content):
        issues.append({"severity": "major", "title": "不安全的反序列化",
                       "suggestion": "pickle/yaml.load 可执行任意代码，改用安全 Loader 或 JSON"})
    return issues


def _check_filename_traversal(filename: str) -> list:
    """检查文件名是否含路径穿越/绝对路径逃逸。返回 issue 列表(空=安全)。"""
    if not filename:
        return []
    issues = []
    norm = filename.replace("\\", "/")
    # 1) .. 穿越
    if re.search(r"(^|/)\.\.(/|$)|\\.\\.\\\\", norm):
        issues.append({"severity": "critical", "title": f"路径穿越: {filename}",
                       "suggestion": "文件名不得含 '..'，防止写入审查目录外"})
    # 2) 绝对路径逃逸(盘符或根)
    elif re.match(r"^[A-Za-z]:[/\\]|^/", norm):
        issues.append({"severity": "major", "title": f"绝对路径文件名: {filename}",
                       "suggestion": "文件名应相对当前审查目录，不得用绝对路径"})
    return issues


def _static_analyze(content: str) -> dict:
    """对单文件执行全量静态分析，返回结构化的检查结果"""
    result = {"syntax":[],"imports":[],"complexity":[],"naming":[],"security":[],"summary":"","score":100}
    all_issues = []

    # 语法
    result["syntax"] = _static_check_syntax(content)
    all_issues.extend(result["syntax"])

    # 安全检查（SQL注入/命令注入/eval/密钥等）
    result["security"] = _static_check_security(content)
    all_issues.extend(result["security"])

    if not result["syntax"]:
        try:
            tree = ast.parse(content)
            result["imports"] = _static_check_imports(tree, content)
            result["complexity"] = _static_check_complexity(tree)
            result["naming"] = _static_check_naming(tree)
            all_issues.extend(result["imports"])
            all_issues.extend(result["complexity"])
            all_issues.extend(result["naming"])
        except SyntaxError:
            pass

    # 综合评分
    severity_weights = {"critical":20,"major":10,"minor":3}
    penalty = sum(severity_weights.get(i["severity"],5) for i in all_issues)
    result["score"] = max(0, 100 - penalty)
    result["summary"] = f"静态分析: {len(all_issues)} 个问题" if all_issues else "静态分析通过"
    result["all_issues"] = all_issues
    return result


# ── 代码解析 ───────────────────────────────────────

def _infer_schema_contract(task: str, extra_hint: str = "") -> str:
    """从 task 写入路径推断项目根，向上找 db.py/schema/models 并解析 CREATE TABLE，
    返回【真实表:列契约】文本供 implement 注入（杜绝 LLM 臆造列名——compliance_matrix 曾
    臆造 certificate.id / db.get_connection 而根因是 implement 从不真读项目 schema）。
    找不到真实 schema 时返回 ''（安全降级，不影响非 db 项目）。extra_hint 可再给显式契约。"""
    import re as _re
    target = None
    for pat in (r"写入文件\s+([A-Za-z]:[^\s，。;]+?\.py)", r"([A-Za-z]:/[^\s，。;]+?\.py)"):
        m = _re.search(pat, task)
        if m:
            target = m.group(1); break
    schema_files = []
    if target:
        d = Path(target).parent
        for _ in range(6):
            if d.exists():
                for f in d.iterdir():
                    if f.suffix == ".py" and f.stem in ("db", "schema", "models", "tables") and f not in schema_files:
                        schema_files.append(f)
            parent = d.parent
            if parent == d: break
            d = parent
    out, seen = [], set()
    for sf in schema_files[:3]:
        try:
            txt = sf.read_text(encoding="utf-8")
        except Exception:
            continue
        for m2 in _re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\)\s*;", txt, _re.S):
            tbl, body = m2.group(1), m2.group(2)
            if tbl in seen: continue
            seen.add(tbl)
            cols, in_pk = [], False
            for line in body.splitlines():
                s = line.strip()
                if s.startswith("PRIMARY KEY"):
                    cols.append("[PK]"); continue
                cm = _re.match(r"(\w+)\s", s)
                if cm and cm.group(1) not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT", "REFERENCES"):
                    cols.append(cm.group(1))
            out.append(f"- {tbl}({', '.join(cols)})")
    # 迁移加列(ALTER TABLE ... ADD COLUMN, 如 _X_COLS)补进对应表——CREATE TABLE 不含它们, 漏了会致 LLM 不知真实列
    alt = {}
    for sf in schema_files[:3]:
        try:
            txt = sf.read_text(encoding="utf-8")
        except Exception:
            continue
        for m3 in _re.finditer(r'ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)', txt):
            alt.setdefault(m3.group(1), []).append(m3.group(2))
    if alt:
        out.append("# 迁移加列(ALTER ADD, 真实存在):")
        for t, cs in alt.items():
            out.append(f"- {t} + {', '.join(sorted(set(cs)))}")
    if extra_hint.strip():
        out.append(extra_hint.strip())
    if not out:
        return ""
    return "\n".join(out)


def _parse_code_blocks(text):
    files = {}
    # 1) 标准完整闭合代码块 ```lang:path ... ```
    blocks = re.findall(r'```(\w+)?:?([^\n]+?)\n(.*?)```', text, re.DOTALL)
    for lang, path, code in blocks:
        path = path.strip()
        if path:
            if "." not in path: path = f"{path}.py"
            files[path] = code.strip()
    # 2) 未闭合代码块容错(长代码常被截断,最后一个 ``` 没闭合 → findall 匹配不到)
    #    按 ```lang:path 开头提取到文本结尾, 只收路径明确且代码够长的块
    if not files:
        for fm in re.finditer(r'```(\w+)?:?([^\n]+?)\n(.*?)(?:```|$)', text, re.DOTALL):
            path = fm.group(2).strip()
            code = fm.group(3).strip()
            if path and code and len(code) > 20 and "." in path:
                if "." not in path: path = f"{path}.py"
                files.setdefault(path, code)
    # 3) 兜底: 无代码块但含 def/class 且非空 → 整体当 main.py
    if not files and ("def " in text or "class " in text) and text.strip():
        files["main.py"] = text.strip()
    return files

def _parse_json(text):
    if "```" in text:
        for p in text.split("```"):
            c = p.strip()
            if c.startswith("json"): c = c[4:].strip()
            if c.startswith("{"): return json.loads(c)
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e+1]) if s >= 0 and e > s else {}


def _type_ok(v, t):
    """校验值 v 是否符合期望类型名 t（'str'/'int'/'list'/'dict'/'bool'/'any'）"""
    if t == "str": return isinstance(v, str)
    if t == "int": return isinstance(v, int) and not isinstance(v, bool)
    if t == "list": return isinstance(v, list)
    if t == "dict": return isinstance(v, dict)
    if t == "bool": return isinstance(v, bool)
    if t == "any": return True
    return False


def _parse_json_schema(text, expected):
    """带期望 schema 的 JSON 解析 + 三分类诊断（借鉴 PenguinHarness strictParse）。

    expected: {字段名: 类型名}, 类型名如 'str'/'int'/'list'/'dict'/'bool'/'any'
    返回 {"ok": bool, "value": dict, "missing": [缺的字段], "dropped": [未知的键], "invalid": [类型错的键]}
    复用 _parse_json 的提取/解析逻辑；解析失败时 missing=list(expected) 并带 error="parse_failed"。
    """
    try:
        value = _parse_json(text)
    except Exception:
        return {"ok": False, "value": {}, "missing": list(expected), "dropped": [], "invalid": [], "error": "parse_failed"}
    if not isinstance(value, dict):
        return {"ok": False, "value": {}, "missing": list(expected), "dropped": [], "invalid": [], "error": "not_object"}
    missing = [k for k in expected if k not in value]
    dropped = [k for k in value if k not in expected]
    invalid = [k for k, t in expected.items() if k in value and not _type_ok(value[k], t)]
    return {"ok": not missing and not invalid, "value": value, "missing": missing, "dropped": dropped, "invalid": invalid}


# ── 增强测试生成（规则参数化：数字/字符串/列表/bool 边界值）──────

def _extract_type_hint(annotation) -> str:
    """从 AST 注解节点提取类型名（支持 Name / 字符串注解 / Subscript 泛型）"""
    if annotation is None:
        return ""
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        return annotation.value.id  # list[str] → "list"
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    return ""


def _boundary_values(hints: dict) -> list:
    """按参数类型生成边界值规则（零模型调用）。

    数字 → 0 / 负数 / 大数
    字符串 → 空串 / None / 超长串
    列表 → 空列表 / None
    bool → True / False 两分支

    返回 [(参数名, 值表达式, 说明), ...]
    """
    rules = []
    for arg, t in hints.items():
        t = t.lower().strip()
        if t in ("int", "float", "number", "complex", "integer"):
            rules += [(arg, "0", "零"), (arg, "-1", "负数"), (arg, "10**6", "大数")]
        elif t in ("str", "string", "text", "bytes"):
            rules += [(arg, '""', "空串"), (arg, "None", "None"), (arg, '"x"*1000', "超长串")]
        elif t in ("list", "array", "sequence", "dict", "set", "tuple", "list[str]", "list[int]"):
            rules += [(arg, "[]", "空列表"), (arg, "None", "None")]
        elif t in ("bool", "boolean"):
            rules += [(arg, "True", "True 分支"), (arg, "False", "False 分支")]
    return rules


def _is_bool_function(name: str, node) -> bool:
    """推断函数是否返回 bool：命名前缀（is/has/can/should）或 return 全为布尔表达式"""
    if re.match(r"^(is|has|can|should)_", name):
        return True
    returns = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is not None]
    if returns:
        return all(isinstance(r.value, (ast.Compare, ast.BoolOp)) for r in returns)
    return False


def _append_boundary_tests(lines: list, mod: str, cls, fn: str, args: list,
                           hints: dict, node) -> None:
    """追加参数化边界值测试（pytest.mark.parametrize，函数/方法通用）。"""
    rules = _boundary_values(hints)
    if not rules or not args:
        return
    is_bool = _is_bool_function(fn, node)
    assert_line = "    assert result in (True, False)" if is_bool else "    assert result is not None"
    params = ", ".join(args)
    lines.append(f"@pytest.mark.parametrize(\"{params}\", [")
    for arg, val, desc in rules:
        call = ", ".join(val if a == arg else "1" for a in args)
        lines.append(f"    ({call},),  # {arg}={desc}")
    lines.append("])")
    if cls:
        lines.append(f"def test_{cls}_{fn}_boundary({params}):")
        lines.append(f"    obj = {mod}.{cls}()")
        lines.append(f"    result = obj.{fn}({call})")
    else:
        lines.append(f"def test_{fn}_boundary({params}):")
        lines.append(f"    result = {mod}.{fn}({call})")
    lines.append(assert_line)
    lines.append("")


def _gen_python_tests(path, content):
    """根据 AST 生成参数化测试，包含数字/字符串/列表/bool 边界值"""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    mod = path.replace("/", ".").replace(".py", "")
    funcs, methods = [], []

    for n in ast.iter_child_nodes(tree):
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
            params = [a.arg for a in n.args.args]
            has_return = any(isinstance(x, ast.Return) for x in ast.walk(n))
            param_hints = {a.arg: _extract_type_hint(a.annotation) for a in n.args.args if a.annotation}
            funcs.append((n.name, params, has_return, param_hints, n))

    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and not m.name.startswith("_"):
                    params = [x.arg for x in m.args.args if x.arg not in ("self", "cls")]
                    has_return = any(isinstance(x, ast.Return) for x in ast.walk(m))
                    param_hints = {a.arg: _extract_type_hint(a.annotation) for a in m.args.args if a.annotation}
                    methods.append((n.name, m.name, params, has_return, param_hints, m))

    lines = [f'"""tests for {path}"""',
             "import sys; sys.path.insert(0, '.')",
             f"import {mod}",
             "import pytest",
             ""]

    for fn, args, hr, hints, node in funcs:
        # 基础测试
        args_list = ", ".join(["1"] * max(len([x for x in args if x not in ("self", "cls")]), 1))
        lines += [f"def test_{fn}_basic():",
                  f"    result = {mod}.{fn}({args_list})"]
        if hr:
            if _is_bool_function(fn, node):
                lines.append("    assert result in (True, False)  # bool 两分支")
            else:
                lines.append("    assert result is not None")
        lines.append("")

        # 边界值测试（参数化）
        _append_boundary_tests(lines, mod, None, fn, args, hints, node)

    for cls, fn, args, hr, hints, node in methods:
        # 基础测试
        args_list = ", ".join(["1"] * max(len(args), 1))
        lines += [f"def test_{cls}_{fn}_basic():",
                  f"    obj = {mod}.{cls}()",
                  f"    result = obj.{fn}({args_list})"]
        if hr:
            if _is_bool_function(fn, node):
                lines.append("    assert result in (True, False)  # bool 两分支")
            else:
                lines.append("    assert result is not None")
        lines.append("")

        # 边界值测试（参数化）
        _append_boundary_tests(lines, mod, cls, fn, args, hints, node)

    return "\n".join(lines)


def _gen_js_tests(path, content):
    funcs = set(re.findall(r'(?:function|const|exports\.)\s*(\w+)\s*(?:[=(:]|;)', content))
    if not funcs: return None
    return (f'// tests for {path}\nconst mod = require("./{path.replace(".js","")}");\n' +
            "\n".join(f'describe("{fn}",()=>{{it("works",()=>{{expect(mod.{fn}()).toBeDefined()}})}});' for fn in sorted(funcs)))

def _gen_go_tests(path, content):
    funcs = [f for f in re.findall(r'func\s+(\w+)\s*\(', content) if not f.startswith("Test")]
    if not funcs: return None
    return "package tests\n\nimport \"testing\"\n\n" + "\n".join(f"func Test_{fn}(t *testing.T) {{}}" for fn in funcs)


# ── CodeAgent ──────────────────────────────────────

class CodeAgent:
    """代码辅助智能体。

    职责范围：
    - 方案设计、代码生成（智谱 GLM）
    - 代码审查（静态分析 + ornith 模型），支持多模审查
    - 测试生成（AST 规则 + 边界值）
    - 项目加载与分析

    多模审查（v1.2）：
    - mode='code': 代码质量（Ponytail + 静态分析，默认）
    - mode='design': 前端审美（反AI味规则集）
    - mode='layout': 布局稳定性（响应式 + 约束链）
    - mode='content': 内容真实性（不编造/不泄露）

    边界（我不做的事）：
    - 不改生产环境配置
    - 不安装系统级依赖
    - 不执行危险命令
    """

    # ═══════════════════════════════════════════════
    # 项目加载与分析（v1.1 新增）
    # ═══════════════════════════════════════════════

    def load_project(self, path: str, file_pattern="*.py") -> dict:
        """加载项目结构，返回可分析的项目快照。

        参数：
            path: 项目根目录
            file_pattern: 文件匹配模式，默认 *.py

        返回：
            {
                "root": 根目录,
                "files": 文件列表,
                "dirs": 目录结构,
                "file_count": 文件数,
                "imports": {文件: [依赖列表]},
                "funcs": {文件: [函数名列表]},
                "classes": {文件: [类名列表]},
                "total_lines": 总行数
            }
        """
        root = Path(path)
        if not root.exists():
            return {"error": f"路径不存在: {path}"}

        all_files = sorted(root.rglob(file_pattern))
        result = {
            "root": str(root),
            "files": [str(f.relative_to(root)) for f in all_files],
            "dirs": sorted(set(str(f.parent.relative_to(root)) for f in all_files if str(f.parent) != str(root))),
            "file_count": len(all_files),
            "imports": {},
            "funcs": {},
            "classes": {},
            "total_lines": 0,
        }

        for f in all_files:
            try:
                content = f.read_text(encoding="utf-8")
                result["total_lines"] += content.count("\n") + 1
                tree = ast.parse(content)
                # imports
                imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        for alias in node.names:
                            imports.append(f"{mod}.{alias.name}" if mod else alias.name)
                rel = str(f.relative_to(root))
                if imports:
                    result["imports"][rel] = imports
                # funcs + classes
                funcs = [n.name for n in ast.iter_child_nodes(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                classes = [n.name for n in ast.iter_child_nodes(tree) if isinstance(n, ast.ClassDef)]
                if funcs:
                    result["funcs"][rel] = funcs
                if classes:
                    result["classes"][rel] = classes
            except (SyntaxError, UnicodeDecodeError):
                continue

        return result

    def reuse_search(self, path: str, task: str, top_k: int = 3, file_pattern: str = "*.py", path_contains=None) -> list:
        """检索代码库中可复用的函数/类定义（L0 代码复用）。

        用 AST 提取每个函数/类的定义片段 + docstring，按任务关键词做简单
        相关性打分（docstring/名称命中权重高），返回最可能可复用的代码片段。

        参数：
            path: 代码库根目录
            task: 当前任务描述（用于提取检索关键词）
            top_k: 返回前几个片段
            file_pattern: 文件匹配模式
            path_contains: 文件名粗筛列表（可空），仅扫描文件名包含任一词的 .py，
                           用于超大库（如 sktime 上千文件）避免全量 AST 扫描。

        返回：
            [{"file", "name", "kind", "signature", "docstring", "code", "score"}]
        """
        root = Path(path)
        if not root.exists():
            return []
        # 从任务提取检索词（英文/数字 token + 中文短语）
        import re as _re
        query_tokens = _re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}", task.lower())
        query_tokens = [t for t in query_tokens if len(t) >= 2]
        # 中文 token 切 2-字 bigram(近似分词): 让"计算平均值"能匹配 docstring 里的"均值"
        # 中文连串被当单个 token, 不用 bigram 则任务词与 docstring 用词略异就 0 命中
        bigrams = []
        for t in query_tokens:
            if _re.search(r"[\u4e00-\u9fff]", t) and len(t) > 2:
                bigrams += [t[i:i+2] for i in range(len(t) - 1)]
        if bigrams:
            query_tokens += bigrams
        query_tokens = list(dict.fromkeys(query_tokens))  # 去重保序
        if not query_tokens:
            return []

        candidates = []
        for f in sorted(root.rglob(file_pattern)):
            # 跳过测试/示例/文档目录，聚焦核心源码
            rel_parts = f.parts
            if any(p in ("tests", "test", "examples", "docs", "doc", "benchmark", "benchmarks", "__pycache__", "extension_templates") for p in rel_parts):
                continue
            # 文件名粗筛：path_contains 非空时仅扫描文件名含任一词的 .py（提速超大库）
            if path_contains and not any(seg in f.name.lower() for seg in path_contains):
                continue
            try:
                content = f.read_text(encoding="utf-8")
                tree = ast.parse(content)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            rel = str(f.relative_to(root))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = node.name
                    # 提取 docstring
                    doc = ast.get_docstring(node) or ""
                    # 提取签名
                    if isinstance(node, ast.ClassDef):
                        sig = f"class {name}:"
                    else:
                        args = [a.arg for a in node.args.args]
                        sig = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else 'def '}{name}({', '.join(args)}):"
                    # 取定义代码（用 ast.get_source_segment 需源码，这里用 unparse 近似）
                    try:
                        code = ast.unparse(node)[:600]
                    except Exception:
                        code = sig
                    # 打分：docstring + 名称 命中查询词
                    hay = f"{name} {doc}".lower()
                    score = sum(2 if q in name.lower() else (1 if q in doc.lower() else 0) for q in query_tokens)
                    if score > 0:
                        candidates.append({
                            "file": rel, "name": name,
                            "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                            "signature": sig, "docstring": doc[:200],
                            "code": code, "score": score,
                        })
        candidates.sort(key=lambda c: -c["score"])
        return candidates[:top_k]

    # ═══════════════════════════════════════════════
    # 自动领域路由（v1.3，直接复用）
    # ═══════════════════════════════════════════════
    # 领域库检索: DOMAIN_LIBS 不硬编码任何外部绝对路径。
    #  - solo-atoms(自产最常用原子)已镜像进本仓库 vendor/solo-atoms → 恒可用, 无外部依赖。
    #  - 其余为超大第三方源码库(welly/segyio/obspy/pylops/networkx/rdflib/sktime), 本就
    #    不随本仓库分发、也不整库硬拷进 vendor; 仅当 env CODEAGENT_DOMAIN_LIBS_DIR 指向
    #    其父目录时才作为可配置检索源启用, 未设置则整组跳过。
    _EXT_DOMAIN_ROOT = (os.environ.get("CODEAGENT_DOMAIN_LIBS_DIR", "") or "").strip().rstrip("/\\")
    DOMAIN_LIBS = [
        # 自产原子库(已镜像 vendor, 优先): 通用数据分析/清洗/存储/本体/地球物理极简原子
        (os.path.join(_ENGINE_DIR, "vendor", "solo-atoms"), [
            "数据分析", "数据清洗", "清洗", "统计", "描述统计", "异常检测", "异常值",
            "控制图", "spc", "趋势", "平均值", "均值", "去重", "缺失",
            "持久化", "存储", "原子写", "json", "错误契约", "错误处理",
            "本体", "owl", "ntriples", "csv转本体", "csv转owl", "词典", "ontology",
            "测井", "las", "曲线", "深度", "地震", "地球物理", "geophys", "归一化", "平滑",
        ], None),
    ]
    if _EXT_DOMAIN_ROOT and os.path.isdir(_EXT_DOMAIN_ROOT):
        # (子目录名, 触发关键词列表, 文件名粗筛[超大库用]) —— 仅第三方已vendor源码, env根可选启用
        _EXT_LIBS = [
            ("welly", ["las", "测井", "well log", "well"], ["las", "well"]),
            ("segyio", ["segy", "seismic", "地震", "地震数据"], None),
            ("obspy", ["波形", "waveform", "seismogram", "地震台", "trace", "spectrum"], None),
            ("pylops", ["反演", "inversion", "pylops", "线性算子", "regularization"], None),
            ("networkx", ["最短路径", "shortest path", "graph", "图论", "网络", "节点", "networkx", "拓扑"], None),
            ("rdflib", ["本体", "ontology", "rdf", "owl", "图谱", "语义网", "triple"], None),
            ("sktime", ["时序", "时间序列", "time series", "预测", "forecast", "sktime", "分类", "classification", "智能制造"],
             ["forecast", "prediction", "classif", "regress", "transform", "scalar"]),
        ]
        for _sub, _kw, _pf in _EXT_LIBS:
            _p = os.path.join(_EXT_DOMAIN_ROOT, _sub)
            if os.path.isdir(_p):
                DOMAIN_LIBS.append((_p, _kw, _pf))

    def auto_reuse_search(self, task: str, top_k: int = 3) -> list:
        """按任务关键词自动路由到最相关的领域库并检索可复用片段。

        遍历领域库映射，找到首个任务关键词命中的库；超大库（sktime）
        用 path_contains 文件名粗筛避免全量 AST 扫描（sktime 全扫约 44s，
        粗筛后降至秒级）。命中后把该库的英文关键词拼进查询，以桥接
        "中文任务 ↔ 英文代码库"（如"最短路径"→ networkx 函数名）。
        无命中返回空列表。

        返回：
            [{"file", "name", "kind", "signature", "docstring", "code", "score"}]
        """
        t = task.lower()
        for path, keywords, path_filter in self.DOMAIN_LIBS:
            if not any(k in t for k in keywords):
                continue
            # 中英桥接：把该库英文关键词(ASCII)并入查询，提升对英文代码库的匹配
            ascii_kw = [k for k in keywords if k.isascii()]
            query = task + " " + " ".join(ascii_kw)
            hits = self.reuse_search(path, query, top_k=top_k, path_contains=path_filter)
            if hits:
                return hits
        return []

    def analyze_project(self, path: str) -> str:
        """分析项目，返回人类可读的概览报告。

        适合快速了解一个项目结构。
        """
        proj = self.load_project(path)
        if "error" in proj:
            return f"❌ {proj['error']}"

        lines = [f"# 项目分析: {proj['root']}",
                 f"",
                 f"**文件**: {proj['file_count']} 个 | **总行数**: {proj['total_lines']}",
                 f"",
                 f"## 目录结构",
                 f"```"]
        for d in proj["dirs"][:20]:
            lines.append(f"  {d}/")
        if len(proj["dirs"]) > 20:
            lines.append(f"  ... 共 {len(proj['dirs'])} 个目录")
        lines.append("```\n")

        # 模块依赖概览
        if proj["imports"]:
            lines.append("## 模块依赖")
            for f, imps in list(proj["imports"].items())[:15]:
                lines.append(f"- {f}: {', '.join(imps[:5])}")
            if len(proj["imports"]) > 15:
                lines.append(f"- ... 共 {len(proj['imports'])} 个文件有依赖")

        # API 接口概览
        if proj["classes"]:
            lines.append("\n## 类定义")
            for f, cls in list(proj["classes"].items())[:10]:
                lines.append(f"- {f}: {', '.join(cls)}")
        if proj["funcs"]:
            lines.append("\n## 函数定义")
            for f, funcs in list(proj["funcs"].items())[:10]:
                lines.append(f"- {f}: {', '.join(funcs[:8])}")

        return "\n".join(lines)

    def scan_issues(self, path: str) -> list:
        """扫描项目中的潜在问题。

        对每个 Python 文件执行静态分析，返回问题清单。
        """
        proj = self.load_project(path)
        if "error" in proj:
            return [proj]

        all_issues = []
        root = Path(path)
        for rel in proj["files"]:
            try:
                content = (root / rel).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            sa = _static_analyze(content)
            for issue in sa.get("all_issues", []):
                issue["file"] = rel
                all_issues.append(issue)
        return sorted(all_issues, key=lambda x: {"critical":0,"major":1,"minor":2}.get(x.get("severity",""),3))

    # ═══════════════════════════════════════════════
    # Think（方案设计）
    # ═══════════════════════════════════════════════

    def think(self, task: str, language="python", domain="general") -> dict:
        """分析需求，输出方案设计，含约束链。

        参数：
            task: 需求描述
            language: 编程语言
            domain: 领域（general / frontend / backend / cli）

        返回：
            {"plan","assumptions","files_needed","simplest_approach",
             "constraint_chain","questions"}
        """
        domain_hints = {
            "frontend": "先考虑：响应式布局、组件拆分、设计约束、状态覆盖",
            "backend": "先考虑：API 路由、数据流、错误处理、权限",
            "cli": "先考虑：参数解析、输出格式化、退出码、错误提示",
        }
        hint = domain_hints.get(domain, "")
        prompt = f"""分析需求，输出 JSON 方案：
{{"plan":"一句话","assumptions":[],"files_needed":[],"simplest_approach":"",
"constraint_chain":["依赖A→依赖B","约束链：先满足A再满足B"],"questions":[]}}
需求：{task}  语言：{language}  领域：{domain}  {hint}  极简优先。"""
        resp = _call_generate([
            {"role": "system", "content": "你是架构师。输出纯JSON。"},
            {"role": "user", "content": prompt},
        ], temp=0.2)
        try:
            d = _parse_json(resp.get("content", "{}"))
            # 兜底: 解析到空 dict / 缺 plan 字段(DeepSeek 返回非纯 JSON 时) → 降级默认方案
            if not d or "plan" not in d:
                return {"plan": "直接实现", "files_needed": ["main.py"], "simplest_approach": task}
            return d
        except:
            return {"plan": "直接实现", "files_needed": ["main.py"], "simplest_approach": task}

    # ═══════════════════════════════════════════════
    # Implement（代码生成 + Loop 版本追踪）
    # ═══════════════════════════════════════════════

    def implement(self, task: str, language="python", loop=False, max_iter=5, skill_path=None, domain="general", reuse_path=None, output_dir=None, auto_reuse=True, wallclock_sec=None, token_budget=None) -> dict:
        """生成代码，支持迭代优化、领域约束、Skill 加载和代码库复用。

        参数：
            task: 需求描述（若含"写入文件 <路径>"，会据此推断落盘目录）
            language: 编程语言
            loop: 是否启用迭代优化
            max_iter: 最大迭代次数（默认 5）
            skill_path: Skill 文件路径，加载额外领域约束
            domain: 领域（general / frontend / backend / cli）
            reuse_path: 代码库根目录，非空时检索可复用代码并注入 prompt
            auto_reuse: 为 True 且 reuse_path 为空时，自动按任务关键词路由到领域库并复用
            output_dir: 生成的代码写入该目录（默认仓库 generated/，可被 task 中的"写入文件 X"覆盖）
            wallclock_sec: ⑤ 诚实预算门—墙钟秒上限(可选, None=不设), 到点即停不假装
            token_budget:  ⑤ 诚实预算门—累计token上限(可选, None=不设), 由生成轮上报

        返回：
            {"files": {文件名: 代码}, "score": 评分,
             "summary": "总结", "issues": 问题列表,
             "versions": [{迭代快照}], "best_version": 最佳版本号,
             "written": [已写入的绝对路径],
             # ⑤ 诚实回报：loop 且因预算耗尽(轮数/token/墙钟到点)未达收敛而停时, 额外带:
             #   budget_exhausted=True, budget_reason="...", iter_cap_reached=True
             # (不再静默返回 best 当"成功"; 喂给 self_evolve._observe 的 iter_cap_reached)}
        """
        kb_text = inject_context(f"{task} {language} 代码 极简 原则")

        # 代码库复用检索：reuse_path 非空时检索该库；为空且 auto_reuse 开启时自动路由到领域库
        reuse_text = ""
        reused = []
        try:
            if reuse_path:
                reused = self.reuse_search(reuse_path, task)
            elif auto_reuse:
                reused = self.auto_reuse_search(task)
        except Exception as e:
            print(f"[复用] 检索失败: {e}")
            reused = []

        if reused:
            try:
                parts = []
                for r in reused:
                    parts.append(f"# [{r['kind']}] {r['file']} :: {r['signature']}\n# doc: {r['docstring']}\n{r['code']}")
                reuse_text = "\n\n【可复用代码（优先复用，别重写）】\n" + "\n\n".join(parts)
                print(f"[复用] 命中 {len(reused)} 个代码库片段")
            except Exception as e:
                print(f"[复用] 注入失败: {e}")

        # 加载 Skill（如果指定）
        skill_text = ""
        if skill_path and os.path.exists(skill_path):
            try:
                with open(skill_path, "r", encoding="utf-8") as f:
                    skill_text = f.read()
                # 限制长度，避免撑爆上下文
                if len(skill_text) > 4000:
                    skill_text = skill_text[:4000] + "\n...(截断)"
                print(f"[Skill] 已加载: {skill_path}")
            except Exception as e:
                print(f"[Skill] 加载失败: {e}")

        design = self.think(task, language, domain=domain)
        print(f"[设计] {design.get('plan','')[:80]}")
        print(f"[文件] {design.get('files_needed',[])}")
        if design.get("constraint_chain"):
            print(f"[约束链] {design['constraint_chain']}")

        # 真实 schema 契约注入：implement 从不执行工具 read，LLM 只凭 task 文字描述易臆造
        # 列/表名(certificate.id/db.get_connection 教训)。这里主动解析项目 db.py 的真实表列，
        # 作为唯一可信契约喂给 LLM，并禁臆造。找不到(非 db 项目)返回空串不注入。
        schema_contract = _infer_schema_contract(task)
        schema_block = ""
        if schema_contract:
            schema_block = (
                "\n\n【项目真实表契约 · 唯一可信来源】下方是本模块所在项目的真实 SQLite 表与列"
                "(已从 db.py 自动解析)。编写 SQL 只能使用这些真实表/列名, 严禁臆造不存在的列或表"
                "(如本表无 id 就用 rowid, 无某列就不可引用)。连接必须用 from . import db 的 db.get_db() 事务块。\n"
                + schema_contract
            )

        # Loop 追踪
        versions = []
        best = {"files": {}, "score": 0, "summary": "", "issues": []}

        prompt_base = f"""需求：{task}
语言：{language}
方案：{design.get('plan','')}
文件：{design.get('files_needed',[])}
约束链：{design.get('constraint_chain',[])}
{skill_text[:2000] if skill_text else ''}
{reuse_text}
{schema_block}

遵循极简原则：不加抽象、不加未要求的功能、不加冗余注释。
标准库能搞定就不用第三方依赖。{kb_text}"""

        # ⑤ 诚实预算门：显式有限预算(轮数/续跑次数/累计token/墙钟)，默认 finite。
        # 本架构本就"迭代有限"，这里把隐式 max_iter 收进显式有限预算对象，并如实暴露
        # 耗尽信号(budget_exhausted/budget_reason/iter_cap_reached)而非静默返回 best。
        # 注意: 生成空响应需重试的空轮同样消耗预算(与原 for-range 语义一致)。
        from budget_gate import IterationBudget
        _budget = IterationBudget(
            max_iterations=max_iter if loop else 1,
            max_wallclock_sec=wallclock_sec,
            max_tokens=token_budget,
            reason_label=f"implement(loop={loop}, task={str(task)[:24]})",
        )
        _budget_stop = None            # 命中预算维度(loop 语义下若 score<80 → 报告 budget_exhausted)

        round_no = 0
        while True:
            if _budget.exhausted():    # 到点即停：上一轮真实工作照常返回, 不再假装完成
                _budget_stop = _budget.status()
                break
            _budget.step_iteration()
            round_no += 1
            prompt = prompt_base
            if round_no > 1 and best.get("issues"):
                prompt += f"\n\n审查反馈：{best['summary']}\n修复问题，保持接口不变。不要引入新抽象。"

            resp = _call_generate([
                {"role": "system", "content": PONYTAIL_SYSTEM},
                {"role": "user", "content": prompt},
            ])
            if resp.get("error"): return {"error": resp["error"]}

            files = _parse_code_blocks(resp.get("content", ""))
            if not files:
                content = resp.get("content", "")
                # 兜底: 有代码特征(代码块/def/class) → 整段当 main.py 落盘, 避免静默空转
                if content and ("def " in content or "class " in content or "```" in content) and content.strip():
                    files = {"main.py": content.strip()}
                else:
                    # 截断/空响应检测: 内容以代码块开头但未闭合(反引号奇数), 或过短 → 下轮带纠错提示重试
                    trunc = content.strip().startswith("```") and content.count("```") % 2 == 1
                    short = len(content.strip()) > 0 and len(content.strip()) < 400
                    prompt_base += ("\n注意: 上一次输出被截断或为空, 未返回完整闭合的代码块。"
                                    "请这次输出完整的代码, 确保代码块以 ``` 正确结束, 不要省略任何函数或代码。")
                    print("[生成] 截断/空响应, 下轮带纠错提示重试" if (trunc or short) else "[生成] 未解析到代码内容, 下轮重试")
                    continue

            # 双层审查
            review = self.review(files, language)
            score = review.get("score", 0)

            # 保存版本
            versions.append({
                "iter": round_no,
                "files": dict(files),  # copy
                "score": score,
                "issues": review.get("issues", []),
                "summary": review.get("summary", ""),
            })

            if score > best["score"]:
                best = {
                    "files": files, "score": score,
                    "summary": review.get("summary",""),
                    "issues": review.get("issues",[]),
                    "best_version": round_no,
                }

            # 智能终止：连续两轮不提升
            if len(versions) >= 3:
                recent = versions[-3:]
                if recent[-1]["score"] <= recent[-2]["score"] and recent[-2]["score"] <= recent[-3]["score"]:
                    print(f"[停止] 连续 {len(versions)} 轮未提升，终止")
                    break

            if score >= 80:
                break

        # diff 摘要
        if len(versions) > 1:
            v_changes = []
            for v in versions:
                v_changes.append(f"  迭代{v['iter']}: 评分 {v['score']}{' ← 最佳' if v['iter'] == best.get('best_version') else ''}")
            best["diff_summary"] = "版本历史:\n" + "\n".join(v_changes)
        else:
            best["diff_summary"] = "单次生成"

        best["versions"] = versions

        # ⑤ 诚实回报：loop 且因预算耗尽(轮数/token/墙钟到点, 未收敛于 score>=80 或智能早停)
        # 而停 → 如实标记 budget_exhausted, 不再把"用光预算的中间结果"当作已完成返回。
        # 同时 set iter_cap_reached（此前 self_evolve._observe 一直读它却从未被赋值——悬空契约）。
        if loop and _budget_stop is not None and best.get("score", 0) < 80:
            best["budget_exhausted"] = True
            best["budget_reason"] = _budget_stop.get("reason") or "预算耗尽"
            best["budget_dimension"] = _budget_stop.get("dimension")
            best["iter_cap_reached"] = True

        # ── 落盘：把最佳版本的代码写入磁盘 ──
        # 解析落盘目录：task 里的 "写入文件 <路径>" > output_dir 参数 > 默认
        target_dir = output_dir or os.path.join(_ENGINE_DIR, "_impl_output")
        m_dir = re.search(r"(?:写入文件|输出到|保存到)\s*[:：]?\s*([^\s\"']+\.\w+)", task)
        if m_dir:
            target_dir = os.path.dirname(os.path.abspath(m_dir.group(1)))
        written = []
        if best.get("files"):
            try:
                os.makedirs(target_dir, exist_ok=True)
                # 若 task 明确"写入文件 X"，用那个绝对路径作为首选落盘点
                if m_dir:
                    full = os.path.abspath(m_dir.group(1))
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    # 取最佳版本里任意一份代码（多个文件时取第一个）
                    first_code = next(iter(best["files"].values()))
                    with open(full, "w", encoding="utf-8") as f:
                        f.write(first_code)
                    written.append(full)
                    print(f"[落盘] {full}")
                else:
                    for fname, code in best["files"].items():
                        full = os.path.join(target_dir, fname)
                        with open(full, "w", encoding="utf-8") as f:
                            f.write(code)
                        written.append(full)
                        print(f"[落盘] {full}")
                best["written"] = written
            except Exception as e:
                print(f"[落盘失败] {e}")
                best["write_error"] = str(e)

        _log_to_obsidian("implement", task, best)
        return best

    def quality_check(self) -> str:
        """知识库质量检查：frontmatter、孤立笔记"""
        return quality_report()

    def today_summary(self) -> str:
        """今日工作汇总"""
        return daily_summary()

    # ═══════════════════════════════════════════════
    # Review（双层审查：静态分析 + 模型检查）
    # ═══════════════════════════════════════════════

    def review(self, code: dict, language="python", mode='code', rules=None, focus=None) -> dict:
        """审查代码：支持多模审查。

        参数：
            code: {文件名: 代码内容}
            language: 编程语言
            mode: 'code' | 'design' | 'layout' | 'content'
            rules: 自定义审查规则文件路径（可选）
            focus: 审查重点（可选）

        返回：
            {"passed": bool, "score": 0-100,
             "summary": "总结",
             "issues": [{file, severity, title, suggestion}],
             "static_issues": [静态问题]}

        mode='code': 双栈审查（静态分析 + 模型检查）
        mode='design': 前端设计审查（反AI味 + 审美约束）
        mode='layout': 布局稳定性审查（响应式 + 约束链）
        mode='content': 内容真实性审查（不编造/不泄露）
        """
        files_text = "\n---\n".join(f"### {p}\n```{language}\n{c}\n```" for p,c in code.items())

        # 路径穿越安全加固：检查每个文件名的 ../ 穿越与绝对路径逃逸（并入 code 模式结果）
        traversal_issues = []
        for fname in code:
            for issue in _check_filename_traversal(fname):
                issue["file"] = fname
                traversal_issues.append(issue)

        # 模式路由
        if mode == 'design':
            return self._review_design(code, files_text, rules)
        elif mode == 'layout':
            return self._review_layout(code, files_text)
        elif mode == 'content':
            return self._review_content(code, files_text)
        elif mode == 'agent':
            return self._review_agent(code, files_text)
        # 默认 code 模式（原有逻辑）
        return self._review_code(code, files_text, language, traversal_issues)

    def _review_code(self, code: dict, files_text: str, language: str, traversal_issues=None) -> dict:
        """code 模式：双栈审查（静态分析 + 模型检查）"""
        all_static = []
        if traversal_issues:
            all_static.extend(traversal_issues)
        for path, content in code.items():
            if path.endswith(".py"):
                sa = _static_analyze(content)
                for issue in sa.get("all_issues", []):
                    issue["file"] = path
                    all_static.append(issue)

        overengineering = []
        model_issues = []
        model_score = 0
        if files_text.strip():
            # 极简性捷径对照项：(编号, 落入判据, 扣分)
            _SHORTCUTS = [
                ("1", "出现未要求的抽象层/接口/工厂", 20),
                ("2", "为小功能引入第三方依赖或自造 util", 15),
                ("3", "冗余注释、死代码、只为结构完整拆多文件", 15),
                ("4", "配置系统/管理器/注册表用于 <5 个硬编码值", 15),
                ("5", "Repository/Service/Controller 三层链", 10),
                ("6", "隐藏 catch 吞异常、fail-open、静默降级", 20),
            ]
            _sc_prompt = "\n".join(
                f'- 项{i}: 落入判据「{c}」→ 扣{d}分' for i, c, d in _SHORTCUTS
            )
            msg = f"""审查代码，输出JSON：
{{"passed":bool,"score":0-100,"summary":"总结",
"issues":[{{"file":"","severity":"critical|major|minor","title":"","suggestion":""}}],
"overengineering":["发现过度工程问题"],
"shortcut_checks":[{{"item":"1","hit":false,"reason":"..."}}]}}

捷径对照逐项判分：对下面每一项判断代码是否落入过度工程捷径。hit=true 表示落入，其 reason 会进 issues。
{_sc_prompt}

特别检查：
- 有没有未要求的功能/抽象/配置系统？
- 有没有只用一次的抽象层？
- 有没有能用标准库替代的第三方依赖？
- 有没有冗余注释？

{files_text}"""
            resp = _call_router([
                {"role":"system","content":"你是代码审查专家。关注极简性和过度工程。输出纯JSON。"},
                {"role":"user","content":msg},
            ], "code_review")
            _diag_note = ""
            try:
                model_result = _parse_json(resp.get("content","{}"))
                _diag = _parse_json_schema(resp.get("content","{}"), {"score":"int","summary":"str","issues":"list","overengineering":"list","shortcut_checks":"list"})
                if _diag["missing"]:
                    _diag_note = f"  [诊断] 模型未返回期望字段: {','.join(_diag['missing'])}（可能走回退）"
                elif _diag["invalid"]:
                    _diag_note = f"  [诊断] 模型字段类型不符: {','.join(_diag['invalid'])}"
                overengineering = model_result.get("overengineering", [])
                model_issues = model_result.get("issues", [])
                sc = model_result.get("shortcut_checks")
                if isinstance(sc, list) and sc:
                    # 捷径对照逐项判分：model_score = 100 - Σ(hit项扣分)
                    model_score = 100
                    _sc_ded = {i: d for i, c, d in _SHORTCUTS}
                    for c in sc:
                        if not isinstance(c, dict) or not c.get("hit"):
                            continue
                        ded = _sc_ded.get(str(c.get("item")), 0)
                        model_score -= ded
                        model_issues.append({
                            "file": "",
                            "severity": "critical" if ded >= 20 else ("major" if ded == 15 else "minor"),
                            "title": f"落入捷径项{c.get('item')}",
                            "suggestion": str(c.get("reason", "")),
                        })
                else:
                    # 向后兼容：模型不返回 shortcut_checks 时回退原单值 score 逻辑
                    model_score = model_result.get("score", 50)
            except:
                model_score = 0

        static_score = min(100, 100 - sum({"critical":20,"major":10,"minor":3}.get(i["severity"],5) for i in all_static))
        merged_score = round(static_score * 0.4 + model_score * 0.6)

        return {
            "passed": merged_score >= 60,
            "score": merged_score,
            "summary": f"静态({static_score}分) + 模型({round(model_score)}分) = {merged_score}分" + _diag_note,
            "issues": all_static + model_issues,
            "static_issues": all_static,
            "overengineering": overengineering,
        }

    def _review_agent(self, code: dict, files_text: str) -> dict:
        """agent 模式：审查 Agent 技能/行为/状态（规则捷径对照 + 模型二次审查 + 防漂移快照）"""
        import hashlib

        def _material_hash(material: dict) -> str:
            # 对每个文件的 文件名+内容 做稳定序列化后取 sha256，排序保证顺序无关
            items = sorted(f"{k}\x00{v}" for k, v in material.items() if isinstance(v, str))
            return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()

        # D. 防漂移快照：审查前对 Agent 材料取 hash，返回前复核是否被改动
        snapshot_before = _material_hash(code)
        issues = []
        static_score = 100
        skill = code.get("skill.md", "") or ""
        trace = code.get("trace.txt", "") or ""
        config = code.get("config.yaml", "") or ""
        all_text = f"{skill}\n{trace}\n{config}"

        # A. Agent 能力捷径对照表：(编号, 落入判据, 扣分)  —— 命中扣分进 issues
        _AGENT_SHORTCUTS = [
            ("1", "技能含绝对化规则/一次性结论(永远不要X/无条件禁令)", 10),
            ("2", "配置 catch/except 吞异常或 fail-open(pass或空except)", 20),
            ("3", "技能文件>2000字(过度堆砌,非可执行)", 10),
            ("4", "行为轨迹同一错误重复出现(未自愈)", 15),
            ("5", "轨迹走捷径(跳过验证/直接给结果无证据)", 15),
            ("6", "配置含硬编码密钥/路径(无抽象,不可迁移)", 20),
        ]

        def _sev(ded):
            return "critical" if ded >= 20 else ("major" if ded == 15 else "minor")

        def _scan(item):
            if item == "1":
                if re.search(r'(永远不要|永不|一律不|无条件(禁止|不能)|绝对(不能|不要)|never\s+(use|do|touch)|禁止一切)', skill, re.I):
                    return "技能含绝对化规则/一次性结论"
            elif item == "2":
                if re.search(r'except[^:]*:\s*(?:\n\s*(?:pass|continue)|pass)|except\s*:\s*pass|except[^:]*:\s*\n\s*except', all_text, re.I):
                    return "catch/except 吞异常或 fail-open"
            elif item == "3":
                if len(skill) > 2000:
                    return f"技能文件过长({len(skill)}字>2000, 过度堆砌)"
            elif item == "4":
                errs = re.findall(r'(?:Error|错误|失败|exception)[^\n]{0,60}', trace, re.I)
                if len(errs) >= 3 and len(set(errs)) <= len(errs) // 2:
                    return "轨迹中同一错误重复出现(未自愈)"
            elif item == "5":
                if re.search(r'(跳过(验证|测试|检查)|直接(返回|给结果)|未验证|没测试|没有验证|绕过(验证|测试)|省略(验证|测试))', trace):
                    return "轨迹显示走捷径(跳过验证/无证据给结果)"
            elif item == "6":
                if re.search(r'(?:api[_-]?key|token|password|secret)\s*[:=]\s*["\']?[^"\'\s,]{6,}|[CcDd]:\\\\|/Users/|/home/|/root/|/etc/|/var/', config):
                    return "配置含硬编码密钥/路径(无抽象,不可迁移)"
            return None

        for num, criteria, ded in _AGENT_SHORTCUTS:
            reason = _scan(num)
            if reason:
                static_score -= ded
                issues.append({"file": "", "severity": _sev(ded),
                               "title": f"落入捷径项{num}", "suggestion": reason})

        # B. 模型二次审查（shortcut_checks 逐项判分，复用 _review_code 的捷径判分结构）
        overengineering = []
        model_issues = []
        model_score = 0
        if files_text.strip():
            _sc_prompt = "\n".join(
                f'- 项{i}: 落入判据「{c}」→ 扣{d}分' for i, c, d in _AGENT_SHORTCUTS
            )
            msg = f"""审查这个 Agent 的技能/行为/状态，输出JSON：
{{"passed":bool,"score":0-100,"summary":"总结",
"issues":[{{"file":"","severity":"critical|major|minor","title":"","suggestion":""}}],
"overengineering":["发现过度工程问题"],
"shortcut_checks":[{{"item":"1","hit":false,"reason":"..."}}]}}

捷径对照逐项判分：对下面每一项判断 Agent 材料是否落入捷径。hit=true 表示落入，其 reason 会进 issues。
{_sc_prompt}

特别检查：
- 技能是否绝对化规则/一次性能结论？
- 配置是否吞异常/fail-open/硬编码密钥或路径？
- 轨迹是否同一错误重复(未自愈)或走捷径(跳过验证/无证据给结果)?

{files_text}"""
            resp = _call_router([
                {"role": "system", "content": "你是Agent能力审查专家。关注技能可执行性、自愈能力、是否走捷径。输出纯JSON。"},
                {"role": "user", "content": msg},
            ], "code_review")
            _diag_note = ""
            try:
                model_result = _parse_json(resp.get("content", "{}"))
                _diag = _parse_json_schema(resp.get("content", "{}"), {"score":"int","summary":"str","issues":"list","overengineering":"list","shortcut_checks":"list"})
                if _diag["missing"]:
                    _diag_note = f"  [诊断] 模型未返回期望字段: {','.join(_diag['missing'])}（可能走回退）"
                elif _diag["invalid"]:
                    _diag_note = f"  [诊断] 模型字段类型不符: {','.join(_diag['invalid'])}"
                overengineering = model_result.get("overengineering", [])
                model_issues = model_result.get("issues", [])
                sc = model_result.get("shortcut_checks")
                if isinstance(sc, list) and sc:
                    model_score = 100
                    _ded_map = {i: d for i, c, d in _AGENT_SHORTCUTS}
                    for c in sc:
                        if not isinstance(c, dict) or not c.get("hit"):
                            continue
                        ded = _ded_map.get(str(c.get("item")), 0)
                        model_score -= ded
                        model_issues.append({
                            "file": "",
                            "severity": _sev(ded),
                            "title": f"落入捷径项{c.get('item')}",
                            "suggestion": str(c.get("reason", "")),
                        })
                else:
                    model_score = model_result.get("score", 50)
            except Exception:
                model_score = 0

        # C. 合并评分
        static_score = max(0, static_score)
        merged_score = round(static_score * 0.4 + model_score * 0.6)

        # D. 防漂移快照：审查后复核材料是否被改动
        snapshot_after = _material_hash(code)
        drift_detected = snapshot_before != snapshot_after
        all_issues = issues + model_issues
        if drift_detected:
            all_issues = all_issues + [{
                "file": "",
                "severity": "critical",
                "title": "审查期间 Agent 材料被修改（防漂移快照不匹配）",
                "suggestion": "材料在审查过程中被改动，本评分可能不反映审查时快照，请重新审查",
            }]
        return {
            "passed": merged_score >= 60 and not drift_detected,
            "score": merged_score,
            "summary": f"Agent审查: 静态({static_score}分) + 模型({round(model_score)}分) = {merged_score}分" + _diag_note,
            "issues": all_issues,
            "static_issues": issues,
            "overengineering": overengineering,
            "drift_detected": drift_detected,
        }

    def _review_design(self, code: dict, files_text: str, rules_path=None) -> dict:
        """design 模式：前端审美审查（反AI味 + 设计约束）"""
        issues = []
        score = 100

        # 加载规则文件
        rules = []
        if rules_path and os.path.exists(rules_path):
            try:
                with open(rules_path, "r", encoding="utf-8") as f:
                    rules_text = f.read()
                for line in rules_text.split("\n"):
                    if line.strip().startswith("|") and "`" in line:
                        rules.append(line.strip())
            except: pass

        # 检查 A：紫蓝渐变
        for path, content in code.items():
            if not path.endswith((".css", ".jsx", ".tsx", ".svelte", ".html", ".vue")):
                continue
            if re.search(r'(purple|violet|indigo|#8b5cf6)', content, re.I):
                issues.append({"file":path,"severity":"major","title":"紫蓝渐变配色",
                              "suggestion":"替换为品牌色或纸面/编辑系统颜色"})
                score -= 10
            if re.search(r'box-shadow.*\d{2,}px', content) and re.search(r'rgba?.*0\.\d', content):
                issues.append({"file":path,"severity":"minor","title":"阴影可能过强",
                              "suggestion":"检查 box-shadow blur 和透明度"})
                score -= 5
            if re.search(r'grid-cols-3', content):
                issues.append({"file":path,"severity":"minor","title":"使用三列网格",
                              "suggestion":"考虑改用 2 列、列表或 case study 布局"})
                score -= 5
            if re.search(r'font-family:\s*(system-ui|Inter|Arial|sans-serif)', content):
                issues.append({"file":path,"severity":"minor","title":"使用默认字体栈",
                              "suggestion":"指定一个气质字体 + fallback，不止 system-ui"})
                score -= 5
            if re.search(r'rounded-(2xl|3xl|\[)', content):
                issues.append({"file":path,"severity":"minor","title":"使用超大圆角",
                              "suggestion":"card 用 rounded-lg，正文区用小圆角或不设"})
                score -= 3

        # 用模型做二次审查（AI味发现）
        model_issues = []
        if files_text.strip():
            msg = f"""审查这些前端文件的设计质量，输出JSON：
{{"issues":[{{"file":"","severity":"critical|major|minor","title":"","suggestion":""}}]}}
关注：
- 页面是否像通用 AI 模板（紫蓝渐变、三列卡片、默认字体）？
- 是否有真实的设计立场？
- 移动端是否只缩小没重排？
- 是否只做了成功态，缺 loading/empty/error？
- 文案是否空泛（"提升效率""打造体验"）？

{files_text}"""
            resp = _call_router([
                {"role":"system","content":"你是前端设计审查专家。关注审美真实性和反AI味。"},
                {"role":"user","content":msg},
            ], "code_review")
            try:
                model_issues = _parse_json(resp.get("content","{}")).get("issues", [])
                if model_issues:
                    score -= len(model_issues) * 3
            except: pass

        score = max(0, score)
        return {
            "passed": score >= 60,
            "score": score,
            "summary": f"设计审查: {len(issues)} 条规则命中 + {len(model_issues)} 条模型发现",
            "issues": issues + model_issues,
            "static_issues": issues,
        }

    def _review_layout(self, code: dict, files_text: str) -> dict:
        """layout 模式：布局稳定性审查（约束链检查）"""
        issues = []
        score = 100

        for path, content in code.items():
            if not path.endswith((".css", ".jsx", ".tsx", ".svelte", ".html", ".vue")):
                continue

            # 约束链检查
            if re.search(r'h-full', content) and not re.search(r'(min-h-screen|h-screen)', content):
                issues.append({"file":path,"severity":"major","title":"h-full 缺少父级高度容器",
                              "suggestion":"确保 h-full 的父级有确定高度"})
                score -= 10
            if re.search(r'flex.*overflow', content) and not re.search(r'min-h-0', content):
                issues.append({"file":path,"severity":"minor","title":"flex 滚动子元素缺少 min-h-0",
                              "suggestion":"flex 可滚动子项加 min-h-0"})
                score -= 5
            if re.search(r'overflow-x:\s*(auto|scroll)', content):
                issues.append({"file":path,"severity":"minor","title":"水平滚动可能溢出",
                              "suggestion":"检查是否有固定宽溢出，改用 max-w-full"})
                score -= 5
            if re.search(r'ScrollArea', content) and not re.search(r'(h-|max-h-)', content):
                issues.append({"file":path,"severity":"major","title":"ScrollArea 缺少明确高度",
                              "suggestion":"给 ScrollArea 设定固定或最大高度"})
                score -= 10

            # 响应式检查
            if re.search(r'grid-cols-\d', content) and not re.search(r'grid-cols-1\s', content):
                issues.append({"file":path,"severity":"major","title":"缺少移动端单列响应式",
                              "suggestion":"加 grid-cols-1 md:grid-cols-N"})
                score -= 8
            if not re.search(r'@media|md:|lg:|sm:', content):
                issues.append({"file":path,"severity":"major","title":"缺少响应式断点",
                              "suggestion":"至少有移动端和桌面端两个断点"})
                score -= 8

        score = max(0, score)
        return {
            "passed": score >= 60,
            "score": score,
            "summary": f"布局审查: {len(issues)} 个问题",
            "issues": issues,
            "static_issues": issues,
        }

    def _review_content(self, code: dict, files_text: str) -> dict:
        """content 模式：内容真实性审查"""
        issues = []
        score = 100
        fake_patterns = [
            (r'客户[们]?[的同意]*[说表示].{0,20}非常满意', "疑似假客户推荐语"),
            (r'[据统计研究显示].{0,30}\d+%', "疑似编造统计数据"),
            (r'(提升|提高|增长).{0,10}\d+%', "指标需有真实来源"),
            (r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', "可能泄露邮箱"),
            (r'1[3-9]\d{9}', "可能泄露手机号"),
            (r'\d{17}[\dXx]', "可能泄露身份证号"),
            (r'赋能|闭环|抓手|底层逻辑|颗粒度|组合拳', "黑话空话"),
        ]

        for path, content in code.items():
            for pattern, desc in fake_patterns:
                if re.search(pattern, content):
                    issues.append({"file":path,"severity":"major","title":desc,
                                  "suggestion":"替换为真实数据或留空占位"})
                    score -= 10

        # 隐私检查
        emails = []
        for path, content in code.items():
            emails.extend(re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', content))
        if emails:
            score -= 15
            issues.append({"file":"*","severity":"critical","title":f"发现 {len(emails)} 个邮箱地址",
                          "suggestion":"脱敏或使用 placeholders"})

        score = max(0, score)
        return {
            "passed": score >= 60,
            "score": score,
            "summary": f"内容审查: {len(issues)} 个问题",
            "issues": issues,
            "static_issues": issues,
        }

    # ═══════════════════════════════════════════════
    # Test（规则生成 + 边界值增强）
    # ═══════════════════════════════════════════════

    def test(self, code: dict) -> dict:
        """从代码生成测试文件，包含边界值测试。

        参数：
            code: {文件名: 代码内容}

        返回：
            {"test_files": {测试文件名: 测试代码},
             "summary": "生成摘要"}
        """
        test_files = {}
        for path, content in code.items():
            ext = path.split(".")[-1].lower()
            tc = {"py": _gen_python_tests, "js":_gen_js_tests, "jsx":_gen_js_tests,
                  "go":_gen_go_tests}.get(ext, lambda *a: None)(path, content)
            if tc: test_files[f"tests/test_{path.replace('/','_')}"] = tc
        return {"test_files": test_files, "summary": f"生成 {len(test_files)} 测试文件"}

    def run_tests(self, test_files: dict, workdir="."):
        """运行测试文件。

        参数：
            test_files: {文件名: 代码}（来自 test() 方法）
            workdir: 测试运行目录
        """
        if not test_files: return {"passed":False,"summary":"无测试"}
        for p, c in test_files.items():
            Path(workdir, p).parent.mkdir(parents=True, exist_ok=True)
            Path(workdir, p).write_text(c)
        try:
            r = subprocess.run([sys.executable,"-m","pytest","tests/","-v","--tb=short"],
                cwd=workdir, capture_output=True, text=True, timeout=60)
            return {"passed": r.returncode==0, "output": r.stdout[-1000:], "errors": r.stderr[-500:]}
        except Exception as e:
            return {"passed": False, "error": str(e)}

    # ═══════════════════════════════════════════════
    # 批量操作（v1.1 新增）
    # ═══════════════════════════════════════════════

    def batch_review(self, files: dict) -> dict:
        """批量审查多个文件，返回汇总报告。

        参数：
            files: {文件名: 代码内容}（支持任意数量）

        返回：
            {"total": 总文件数, "passed": 通过数,
             "summary": "汇总报告", "details": {文件名: 审查结果}}
        """
        details = {}
        passed = 0
        for path, content in files.items():
            r = self.review({path: content})
            details[path] = r
            if r.get("passed"):
                passed += 1

        return {
            "total": len(files),
            "passed": passed,
            "summary": f"{passed}/{len(files)} 通过",
            "details": details,
        }

    # ═══════════════════════════════════════════════
    # _refine_experience（小步证据-backed 经验沉淀，借鉴 Prime Agent /refine）
    # ═══════════════════════════════════════════════

    def _refine_experience(self, trajectory: dict, enable=True) -> dict:
        """小步经验沉淀(可选, 借鉴 Prime Agent /refine)。

        trajectory: 一次 review/implement 的结果 {task, result, outcome, issues}
        enable=False 时静默跳过(返回 {"refined": False, "reason": "disabled"})

        逻辑:
        1. 若 enable=False 或轨迹无可复用信号 -> 跳过
        2. 可复用信号判定(最小启发式):
           - result.get("passed") 且 score>=80 且 summary 非空 -> 成功经验
           - 或 result 有明确"教训"(如修复了某个重复问题, 由 issues 关键字命中)
        3. 沉淀内容: 小步单条经验(含证据), 调 memory_save(text, confidence=0.3)
           (低置信度起步, 控噪音; 只沉淀明确成功/教训, 绝不重写)
        4. 快照回滚: 沉淀前 hash 当前共享记忆经验集存入 before_hash 返回,
           若沉淀后检测异常可据此回滚(此处最小实现: 仅记录, 不自动回滚)
        返回 {"refined": bool, "confidence": float, "evidence": str, "before_hash": str}
        """
        if not enable:
            return {"refined": False, "reason": "disabled"}
        try:
            result = trajectory.get("result", {})
            if not isinstance(result, dict):
                return {"refined": False, "reason": "no_signal"}

            # ── 可复用信号判定(最小启发式) ──
            passed = bool(result.get("passed"))
            try:
                score = float(result.get("score", 0) or 0)
            except (TypeError, ValueError):
                score = 0.0
            summary = str(result.get("summary", "") or "").strip()
            success_signal = passed and score >= 80 and bool(summary)
            # 教训信号: issues/outcome 命中"修复/教训/重复"等关键字
            lesson_src = trajectory.get("issues", []) or trajectory.get("outcome") or []
            lesson_signal = any(
                any(k in str(s) for k in ("修复", "教训", "重复"))
                for s in (lesson_src if isinstance(lesson_src, list) else [lesson_src])
            )
            if not (success_signal or lesson_signal):
                return {"refined": False, "reason": "no_signal"}

            # ── 快照: 沉淀前 hash 当前共享记忆经验集 ──
            import hashlib
            before_hash = ""
            try:
                ok, hits = optmem_mem.recall("代码 经验 复用", 50)
                if ok and hits:
                    before_hash = hashlib.md5(
                        "||".join(sorted(hits)).encode("utf-8", "ignore")
                    ).hexdigest()
            except Exception:
                before_hash = ""

            # ── 沉淀内容: 小步单条经验(含证据), 低置信度 0.3 ──
            task = str(trajectory.get("task", "")).replace("\n", " ")[:30]
            evidence = (summary if success_signal else str(trajectory.get("outcome", "")))[:50]
            text = f"[成功经验] 任务:{task} 结果:{'passed' if passed else '教训'} 证据:{evidence}"
            memory_save(text, confidence=0.3)

            return {
                "refined": True,
                "confidence": 0.3,
                "evidence": evidence,
                "before_hash": before_hash,
            }
        except Exception as e:
            return {"refined": False, "reason": "error", "error": str(e)}



# ── 可选采样回传(默认关闭, env 启用)(fail-open, 不破坏 CodeAgent 主流程) ──────────
# 默认关闭的可选遥测回传; 无本地采样目录时静默跳过, 不影响主流程:
# 不硬编码外部采样器路径 —— 统一走 env FLYWHEEL_SAMPLER_DIR(同 agent_runtime.py
# 示范)。未设置/目录不存在/开关 CODEAGENT_FLYWHEEL_SAMPLE=0 → 不采样, 绝不影响主流程。
def _sampler_load():
    try:
        if os.environ.get("CODEAGENT_FLYWHEEL_SAMPLE", "1") == "0":
            return None
        _d = (os.environ.get("FLYWHEEL_SAMPLER_DIR", "") or "").strip()
        if not _d or not os.path.isdir(_d):
            return None
        if _d not in sys.path:
            sys.path.insert(0, _d)
        from codeagent_sampler import sample
        return sample
    except Exception:
        return None

_ca_sample = _sampler_load()   # None→不采样(下方每次调用已 try 保护)

def _flywheel_sample(dim, backend, input_data, output_data, polarity="pos", error=None):
    """写一条 CodeAgent 真实执行样本(fail-open, 任何异常静默)。"""
    try:
        if _ca_sample is None:
            return
        _ca_sample(dim=dim, backend=backend, input=input_data,
                   output=output_data, polarity=polarity, error=error)
    except Exception:
        pass

def _flywheel_wrap(method, dim, backend):
    """装饰器: 调原方法 → 采样本 → 原样返回结果, 绝不改变行为。"""
    from functools import wraps
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            result = method(self, *args, **kwargs)
        except Exception as e:
            _flywheel_sample(dim, backend, (args, kwargs), None, "neg", e)
            raise
        is_err = isinstance(result, dict) and result.get("error")
        _flywheel_sample(dim, backend, (args, kwargs), result,
                         "neg" if is_err else "pos")
        return result
    return wrapper

for _m, _d, _b in [
    ("review", "code_review", "codeagent"),
    ("think", "decision", "codeagent"),
    ("test", "test", "codeagent"),
    ("implement", "format", "codeagent"),
    ("scan_issues", "security_review", "codeagent"),
]:
    if hasattr(CodeAgent, _m):
        setattr(CodeAgent, _m, _flywheel_wrap(getattr(CodeAgent, _m), _d, _b))
# ── 采样注入结束 ──────────────────────────────────────
