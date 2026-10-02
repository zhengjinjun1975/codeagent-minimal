"""
知识库双向支撑库 — 知识库的自检与注入（通用工作日志/每日摘要）。
知识库路径由环境变量 CODEAGENT_OBSIDIAN_VAULT 指定，未配置则整体静默降级。
"""

import os, re, json, hashlib, subprocess, sys
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

_VAULT_STR = os.environ.get("CODEAGENT_OBSIDIAN_VAULT", "").strip()
VAULT = Path(_VAULT_STR) if _VAULT_STR else None
# 检索脚本默认在知识库根下 scripts/ 推测；也可用 env 单独覆盖。
KB_INDEX = os.environ.get("CODEAGENT_KB_INDEX", "") or (
    str(VAULT / "scripts" / "kb_index.py") if VAULT is not None else "")
KB_SEARCH = os.environ.get("CODEAGENT_KB_SEARCH", "") or (
    str(VAULT / "scripts" / "kb_search.py") if VAULT is not None else "")
KB_INDEX = KB_INDEX or None
KB_SEARCH = KB_SEARCH or None
# 知识库整体是否可用（路径已配置且目录真实存在）。
VAULT_READY = bool(VAULT) and VAULT.is_dir()


# ═══════════════════════════════════════════════════
# 1. 笔记质量检查 — 发现缺失元数据、孤立笔记、断链
# ═══════════════════════════════════════════════════

def check_note(path: Path) -> dict:
    """检查单篇笔记：frontmatter完整性、wikilink有效性（无知识库时返回安全空值）"""
    if not VAULT_READY:
        return {"path": "", "title": "", "issues": [], "links": 0}
    issues = []
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # frontmatter 检查
    if text.startswith("---"):
        end = text.find("---", 3)
        fm = text[3:end] if end > 0 else ""
        has_title = "title:" in fm
        has_tags = "tags:" in fm
        has_created = "created:" in fm
        if not has_title: issues.append("缺 title")
        if not has_tags: issues.append("缺 tags")
        if not has_created: issues.append("缺 created")
    else:
        issues.append("无 frontmatter")

    # 孤立笔记（0 个出链）
    wikilinks = re.findall(r'\[\[([^\]|]+)', text)
    if not wikilinks and "daily" not in str(path):
        issues.append("孤立笔记（无链接）")

    return {"path": str(path.relative_to(VAULT)), "title": path.stem,
            "issues": issues, "links": len(wikilinks)}


def scan_vault(folder="knowledge") -> list:
    """扫描整个知识库，返回所有问题笔记（无知识库时返回空列表）"""
    if not VAULT_READY:
        return []
    results = []
    for f in (VAULT / folder).rglob("*.md"):
        r = check_note(f)
        if r["issues"]:
            results.append(r)
    return sorted(results, key=lambda x: -len(x["issues"]))


def quality_report() -> str:
    """生成知识库质量报告（无知识库时返回空字符串）"""
    if not VAULT_READY:
        return ""
    issues = scan_vault()
    today = date.today().isoformat()
    lines = [f"# 知识库质量报告 — {today}", ""]
    if not issues:
        lines.append("✅ 无问题笔记")
    else:
        lines.append(f"共发现 {len(issues)} 篇有问题的笔记：\n")
        for r in issues:
            lines.append(f"- **{r['title']}** (`{r['path']}`)")
            for i in r["issues"]:
                lines.append(f"  - ⚠️ {i}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════
# 2. 重复检测 — 基于内容哈希 + 标题相似度
# ═══════════════════════════════════════════════════

def _content_hash(text: str) -> str:
    """去除空白后取 SHA1 前缀"""
    clean = re.sub(r'\s+', '', text[:5000])
    return hashlib.sha1(clean.encode()).hexdigest()[:12]


def find_duplicates(folder="knowledge") -> list:
    """查找内容高度相似的笔记对（无知识库时返回空）"""
    if not VAULT_READY:
        return
    notes = {}
    for f in (VAULT / folder).rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        h = _content_hash(text)
        if h in notes:
            yield (notes[h], str(f.relative_to(VAULT)))
        notes[h] = str(f.relative_to(VAULT))


# ═══════════════════════════════════════════════════
# 3. 链接建议 — 基于关键词配对的未链接笔记
# ═══════════════════════════════════════════════════

def suggest_links(folder="knowledge", min_common=3) -> list:
    """找出应该建立 [[link]] 但还没连的笔记对（无知识库时返回空列表）"""
    if not VAULT_READY:
        return []
    notes = {}
    for f in (VAULT / folder).rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        # 提取已有 wikilinks
        links = set(re.findall(r'\[\[([^\]|]+)', text))
        # 提取关键词（标题级词语 + 专有名词）
        words = set(re.findall(r'[A-Z][a-z]{2,}|[a-z]{4,}', text[:3000]))
        notes[str(f.relative_to(VAULT))] = {"links": links, "words": words}

    paths = list(notes.keys())
    suggestions = []
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            a, b = paths[i], paths[j]
            # 如果已经链接了就跳过
            if notes[a]["links"] & {Path(b).stem} or notes[b]["links"] & {Path(a).stem}:
                continue
            common = notes[a]["words"] & notes[b]["words"]
            if len(common) >= min_common:
                suggestions.append((a, b, common))
    return sorted(suggestions, key=lambda x: -len(x[2]))


# ═══════════════════════════════════════════════════
# 4. 每日汇总 — 汇总今天的工作日志
# ═══════════════════════════════════════════════════

def daily_summary(date_str=None) -> str:
    """汇总当天的工作日志（无知识库时返回空字符串）"""
    if not VAULT_READY:
        return ""
    today = date_str or date.today().isoformat()
    log_dir = VAULT / "daily" / "codeagent"
    lines = [f"# 每日工作日志 — {today}", ""]

    count = 0
    for f in sorted(log_dir.glob(f"{today}*.md")):
        text = f.read_text(encoding="utf-8")
        task_match = re.search(r"\*\*任务\*\*: (.+)", text)
        result_match = re.search(r"\*\*结果\*\*: (.+)", text)
        task = task_match.group(1) if task_match else f.stem
        result = result_match.group(1) if result_match else ""
        lines.append(f"- [{task}]({f.name}) {'✅' if '无' in result else '📋'}")
        count += 1

    lines.append("")
    if count == 0:
        lines.append("今日无工作日志记录")
    else:
        lines.append(f"共 {count} 项任务")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════
# 5. 知识注入触发器 — 生成前自动检索
# ═══════════════════════════════════════════════════

def inject_context(query: str, top_k=3) -> str:
    """搜索知识库，返回可注入 prompt 的上下文片段（无检索脚本时返回空字符串）"""
    if not KB_SEARCH or not os.path.exists(KB_SEARCH):
        return ""
    try:
        r = subprocess.run(
            [sys.executable, KB_SEARCH, query, "--json", "--top-k", str(top_k)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return ""

        results = json.loads(r.stdout)
        snippets = []
        for item in results[:top_k]:
            title = item.get("title", "")
            snippet = item.get("snippet", "")[:200]
            if title and snippet:
                snippets.append(f"- {title}: {snippet}")

        if snippets:
            return "\n来自知识库的参考：\n" + "\n".join(snippets)
        return ""
    except:
        return ""
