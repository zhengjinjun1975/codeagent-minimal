#!/usr/bin/env python3
"""ontology-review 原子壳（open_source:true）——P0 本体审查(factory-ontology链路) + P2 本体数据质量。

针对用户工厂本体项目（factory-ontology-web ↔ ontology-learning-kit）的审查原子：
  链路审查：Web → server/ontology.js → 桥接 Python 套件 → CSV → run.py setup → deep.nt → ask/aggregate
  数据质量：CSV(空值/重复主键/表头) / NT 三元组(悬空引用) / lexicon 词典(JSON 合法+条目)
纯 stdlib 数据不出厂。

能力：
  ontology.chain  — factory-ontology 链路断链审查（Web↔套件↔数据/产物）
  ontology.quality— 本体数据质量（CSV/NT/lexicon 结构问题）
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

# 默认目标目录：开源版不硬编码本地绝对路径，可经 web_dir/kit_dir/data_dir 入参覆盖。
_DEF_WEB = ""
_DEF_KIT = ""


class OntologyReviewAgent(AtomicAgent):
    name = "ontology-review"
    version = "0.1.0"
    domain = "ontology"
    description = ("本体审查原子（P0+P2）: factory-ontology 链路断链审查(Web↔套件↔CSV↔NT) + "
                   "本体数据质量(CSV/NT/lexicon)。纯 stdlib 数据不出厂。")
    provides = ["ontology.chain", "ontology.quality"]
    depends_on = []
    inputs = ["web_dir", "kit_dir", "data_dir", "target"]
    outputs = ["ok", "links", "issues", "quality", "verdict"]

    def _register_defaults(self):
        self.register("ontology.chain", self._chain)
        self.register("ontology.quality", self._quality)

    # ── 链路审查 ─────────────────────────────
    def _chain(self, web_dir=None, kit_dir=None):
        web = os.path.abspath(web_dir or _DEF_WEB)
        kit = os.path.abspath(kit_dir or _DEF_KIT)
        links = []
        issues = []
        checks = [
            ("factory-ontology-web 目录", web, os.path.isdir(web)),
            ("Web 桥接 server/ontology.js", os.path.join(web, "server", "ontology.js"),
             os.path.isfile(os.path.join(web, "server", "ontology.js"))),
            ("ontology-learning-kit codes 目录", kit, os.path.isdir(kit)),
        ]
        for label, path, ok in checks:
            links.append({"label": label, "path": path, "ok": ok})
            if not ok:
                issues.append(f"{label} 缺失: {path}")
        # 套件核心脚本
        for script in ("run.py", "aggregate.py", "csv_to_owl.py"):
            p = os.path.join(kit, script)
            ok = os.path.isfile(p)
            links.append({"label": f"套件脚本 {script}", "path": p, "ok": ok})
            if not ok:
                issues.append(f"套件脚本缺失 {script}")
        # 数据与产物
        for sub, label in (("data", "数据 CSV 目录"), ("output", "产物 NT 目录"),
                           ("config", "词典 config 目录")):
            p = os.path.join(kit, sub)
            ok = os.path.isdir(p)
            links.append({"label": label, "path": p, "ok": ok})
            if not ok:
                issues.append(f"{label} 缺失: {p}")
        # 产物文件
        for pat in ("*.nt", "*.json"):
            found = False
            for r, _d, files in os.walk(kit):
                if any(f.endswith(pat[1:]) for f in files):
                    found = True
                    break
            label = f"套件产物 {pat}"
            links.append({"label": label, "path": kit, "ok": found})
            if not found:
                issues.append(f"套件无 {pat} 产物（未建模过）")
        return {"ok": not issues, "links": links, "issues": issues,
                "web_dir": web, "kit_dir": kit,
                "verdict": "factory-ontology 链路完整" if not issues
                else f"{len(issues)} 处链路断点"}

    # ── 数据质量 ─────────────────────────────
    def _quality(self, kit_dir=None, data_dir=None, target=None):
        kit = os.path.abspath(kit_dir or _DEF_KIT)
        base = data_dir or kit
        issues = []
        # 1) CSV
        csvs = []
        for r, _d, files in os.walk(base):
            for f in files:
                if f.endswith(".csv"):
                    csvs.append(os.path.join(r, f))
        for csv in csvs:
            try:
                rows = self._read_csv(csv)
            except Exception as e:
                issues.append({"file": os.path.basename(csv), "kind": "csv", "issue": f"读取失败: {e}"})
                continue
            if not rows:
                issues.append({"file": os.path.basename(csv), "kind": "csv", "issue": "空 CSV（无数据行）"})
                continue
            header = rows[0]
            if any(not str(c).strip() for c in header):
                issues.append({"file": os.path.basename(csv), "kind": "csv", "issue": "表头存在空列名"})
            # 空单元格
            empties = 0
            for row in rows[1:]:
                empties += sum(1 for c in row if c == "" or str(c).strip() == "")
            if empties:
                issues.append({"file": os.path.basename(csv), "kind": "csv",
                               "issue": f"{empties} 个空单元格"})
            # 重复主键(首列)
            keys = [str(row[0]).strip() for row in rows[1:] if row and str(row[0]).strip()]
            dup = {k for k in keys if keys.count(k) > 1}
            if dup:
                issues.append({"file": os.path.basename(csv), "kind": "csv",
                               "issue": f"重复主键: {sorted(dup)[:5]}"})
        # 2) NT 三元组悬空引用
        nts = []
        for r, _d, files in os.walk(kit):
            for f in files:
                if f.endswith(".nt"):
                    nts.append(os.path.join(r, f))
        for nt in nts:
            subjects, objects = set(), set()
            total = 0
            with open(nt, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    total += 1
                    parts = line.split()
                    if len(parts) >= 2:
                        subjects.add(parts[0])
                        if len(parts) >= 3:
                            objects.add(parts[-2] if len(parts) > 3 else parts[2])
            dangling = sorted(o for o in objects if o not in subjects and o not in ("Literal",))
            if dangling:
                issues.append({"file": os.path.basename(nt), "kind": "nt",
                               "issue": f"{total} 三元组, {len(dangling)} 个悬空对象引用未定义"})
        # 3) lexicon JSON
        lexicons = []
        for r, _d, files in os.walk(kit):
            for f in files:
                if f.startswith("lexicon") and f.endswith(".json"):
                    lexicons.append(os.path.join(r, f))
        for lx in lexicons:
            try:
                with open(lx, encoding="utf-8") as fh:
                    data = json.load(fh)
                n = len(data) if isinstance(data, (list, dict)) else 0
                if n == 0:
                    issues.append({"file": os.path.basename(lx), "kind": "lexicon", "issue": "空词典"})
            except (json.JSONDecodeError, OSError) as e:
                issues.append({"file": os.path.basename(lx), "kind": "lexicon",
                               "issue": f"JSON 非法: {e}"})
        return {"ok": not issues, "issues": issues,
                "csv_files": len(csvs), "nt_files": len(nts), "lexicon_files": len(lexicons),
                "verdict": "本体数据质量合规" if not issues else f"{len(issues)} 处数据质量问题"}

    @staticmethod
    def _read_csv(path):
        """最小 CSV 解析（处理引号包裹的逗号）。"""
        rows = []
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row, buf, inq = [], [], False
                for ch in line:
                    if ch == '"':
                        inq = not inq
                    elif ch == "," and not inq:
                        row.append("".join(buf).strip())
                        buf = []
                    else:
                        buf.append(ch)
                row.append("".join(buf).strip())
                rows.append(row)
        return rows


agent = OntologyReviewAgent

if __name__ == "__main__":
    from atomic_base import run_cli
    sys.exit(run_cli(OntologyReviewAgent(), run_args={
        "capability": {"default": "ontology.chain", "choices": list(OntologyReviewAgent.provides)},
        "web_dir": {}, "kit_dir": {}, "data_dir": {},
    }))
