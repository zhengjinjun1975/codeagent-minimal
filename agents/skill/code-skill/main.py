#!/usr/bin/env python3
"""code-skill 原子壳（open_source:true, 新原子）——SKILL.md 技能标准对齐。

对齐 OpenCode P0-3「SKILL.md 技能标准 + 跨工具互通」：
技能 = 一个目录放 `SKILL.md`（YAML frontmatter: name/description + 正文步骤）。
搜索路径同时兼容 `.opencode/skills` / `.claude/skills` / `.agents/skills`
（项目级 + 全局 ~/.config）。`skill` 按需加载：先看清单，需要时再取全文 → 省上下文。

与自进化衔接：`code-evolve`/`code-memory` 沉淀的 `experience/skills.json` 技能，
可经 `skill.export` 转成 SKILL.md 标准，被 Claude/其他 Agent 直接复用，也能从生态
吸收技能——把「自进化沉淀的技能」变成「标准可交换资产」。

零依赖：纯标准库，YAML frontmatter 用极简解析（只取 name/description 关键字段），
不引入 PyYAML。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

# 兼容搜索路径（OpenCode/Claude/.agents 通用技能目录）
SKILL_DIRS = [".opencode/skills", ".claude/skills", ".agents/skills", ".codeagent/skills"]
GLOBAL_CONF_DIRS = ["~/.config/opencode/skills", "~/.config/claude/skills",
                    "~/.config/.agents/skills"]


def _parse_frontmatter(content):
    """极简 YAML frontmatter 解析（SKILL.md 头两行 `---` 之间）。
    只提取 name/description/version（SKILL.md 标准必需字段），返回 (meta, body)。"""
    if not content.startswith("---"):
        return {}, content
    lines = content.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, content
    meta = {}
    for line in lines[1:end]:
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip("\"'")
    body = "\n".join(lines[end + 1:]).strip()
    return meta, body


class CodeSkillAgent(AtomicAgent):
    name = "code-skill"
    version = "0.1.0"
    domain = "skill"
    description = ("SKILL.md 技能标准原子(新原子): 兼容 .opencode/.claude/.agents/skills "
                   "搜索路径, skill.load/list/export 跨工具互通 + 自进化资产标准化")
    provides = ["skill.list", "skill.load", "skill.export", "skill.sediment"]
    depends_on = ["memory.recall"]
    inputs = ["paths", "name", "content", "task", "action", "bucket", "memdir", "frontmatter"]
    outputs = ["skills", "skill", "exported", "added", "path"]

    def _register_defaults(self):
        self.register("skill.list", self._list)
        self.register("skill.load", self._load)
        self.register("skill.export", self._export)
        self.register("skill.sediment", self._sediment)

    def _search_paths(self):
        """项目级 SKILL_DIRS + 全局 ~/.config 目录。返回绝对路径列表。"""
        paths = [os.path.join(REPO_ROOT, d) for d in SKILL_DIRS]
        for d in GLOBAL_CONF_DIRS:
            paths.append(os.path.expanduser(d))
        return [p for p in paths if os.path.isdir(p)]

    def _list(self, paths=None):
        """列出所有可发现的 SKILL.md（跨 .opencode/.claude/.agents 目录）。"""
        found = []
        for base in (paths or self._search_paths()):
            if not os.path.isdir(base):
                continue
            for root, dirs, files in os.walk(base):
                if "SKILL.md" in files:
                    p = os.path.join(root, "SKILL.md")
                    try:
                        content = open(p, encoding="utf-8", errors="ignore").read()
                    except Exception:
                        continue
                    meta, _ = _parse_frontmatter(content)
                    found.append({"name": meta.get("name", os.path.basename(root)),
                                  "path": p, "description": meta.get("description", ""),
                                  "frontmatter": meta,
                                  "dir": os.path.basename(os.path.dirname(p))})
        return {"skills": found, "count": len(found),
                "search_paths": [p for p in (paths or self._search_paths())]}

    def _load(self, name=None, path=None, paths=None):
        """按需加载单个 SKILL.md 全文（省上下文：只取命中的那一个）。"""
        if path:
            if not os.path.isfile(path):
                return self._envelope(False, degraded=True, error=f"SKILL.md 不存在: {path}")
            p = path
        else:
            lst = self._list(paths)["skills"]
            hit = next((s for s in lst if s["name"] == name), None)
            if not hit:
                return self._envelope(False, degraded=True,
                                      error=f"未找到技能 '{name}'（可选: {[s['name'] for s in lst]}）")
            p = hit["path"]
        try:
            content = open(p, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            return self._envelope(False, degraded=True, error=f"读取失败: {e}")
        meta, body = _parse_frontmatter(content)
        return {"skill": {"name": meta.get("name", os.path.basename(os.path.dirname(p))),
                          "path": p, "description": meta.get("description", ""),
                          "frontmatter": meta, "body": body,
                          "body_len": len(body)}}

    def _export(self, content, name=None, description=None, frontmatter=None,
                dest_dir=None):
        """把一个技能写成本地 SKILL.md 标准文件（YAML frontmatter + 正文）。
        供 code-evolve 沉淀的技能导出为可被 Claude/其他 Agent 复用的标准资产。"""
        if isinstance(content, dict):
            content = content.get("body", content.get("action", json_dumps(content)))
        name = name or "generated-skill"
        fm = frontmatter or {}
        fm.setdefault("name", name)
        fm.setdefault("description", description or fm.get("description", ""))
        fm.setdefault("version", "0.1.0")
        head = "---\n"
        for k, v in fm.items():
            head += f"{k}: {v}\n"
        head += "---\n\n"
        out = head + str(content)
        dest = dest_dir or os.path.join(REPO_ROOT, ".codeagent", "skills", name)
        os.makedirs(dest, exist_ok=True)
        p = os.path.join(dest, "SKILL.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(out)
        return {"exported": True, "path": p, "name": name, "content": out}

    def _sediment(self, task, action, bucket="P", memdir=None):
        """把经验技能沉淀进 experience/skills.json（复用 self_evolve._sediment_skill）。
        与 code-memory.sediment 同源，供 self_prompt 召回。"""
        import self_evolve as se
        memdir = memdir or se.DEFAULT_MEM
        before = len(se._load(memdir, se._SKILLS))
        se._sediment_skill(task, action, bucket, memdir)
        after = len(se._load(memdir, se._SKILLS))
        return {"skills": se._load(memdir, se._SKILLS), "added": after - before,
                "note": "已沉淀进 experience/skills.json（可经 skill.export 转 SKILL.md）"}


def json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


agent = CodeSkillAgent()

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="code-skill 原子自测入口")
    ap.add_argument("--capability", default="skill.list",
                    choices=["skill.list", "skill.load", "skill.export", "skill.sediment"])
    ap.add_argument("--name", default=None)
    ap.add_argument("--path", default=None)
    ap.add_argument("--task", default="实现加法函数 add(a,b)")
    args = ap.parse_args()
    agent.load()
    print("══ code-skill 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    if args.capability == "skill.list":
        r = agent.run(_capability="skill.list")
    elif args.capability == "skill.load":
        r = agent.run(_capability="skill.load", name=args.name, path=args.path)
    elif args.capability == "skill.export":
        r = agent.run(_capability="skill.export", name="demo-skill",
                      description="演示技能", content="1. 先查参数\n2. 再算结果")
    else:
        r = agent.run(_capability="skill.sediment", task=args.task, action="先补参数校验", bucket="P")
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
