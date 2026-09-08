#!/usr/bin/env python3
"""CodeAgent 原子壳（open_source:true）。

复用（零改动核心）：self_evolve.remember/_load/_save + self_prompt(召回)
只加壳：把既有函数 import 进 run() 包 {ok,data} 信封。

能力域：memory。数据不出厂，可独立运行。

记忆双通道：
  - 主写可选外部共享记忆库(经 env OPTMEM_MEMO_DIR 配置)：条目带 [code] 域前缀 + [conf:X.X] 置信度，
    供跨会话/跨进程语义检索，一边记另一边能搜到。
  - 本地 experience/*.json(self_evolve)保留为降级后备 + 私标副本；
    记忆库不可达(未配置/异常/超时/无 Ollama)时静默回落本地，绝不中断业务主流程。
  - 读取优先从共享记忆库召回(域隔离 [code])，失败降级本地 self_prompt。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if HERE not in sys.path:          # 本原子目录，用于 import 桥模块
    sys.path.insert(0, HERE)

from atomic_base import AtomicAgent

import self_evolve as se
import optmem_mem as om          # 可选外部共享记忆库零依赖桥
import memory_gate               # 共享质量闸单源(与 code_agent_engine 直写复用, 不复制两份)


class CodeMemoryAgent(AtomicAgent):
    name = "code-memory"
    version = "0.1.1"
    domain = "memory"
    description = "记忆/经验原子: 主写可选外部共享记忆库(env 配置) + 本地 experience 降级, 跨会话召回"
    provides = ["memory.save", "memory.recall", "memory.sediment"]
    depends_on = []
    inputs = ["findings", "task", "memdir", "top_k"]
    outputs = ["added", "lessons", "prompt", "skills"]

    def _register_defaults(self):
        self.register("memory.save", self._save)
        self.register("memory.recall", self._recall)
        self.register("memory.sediment", self._sediment)

    # ═══════════ 共享写路径质量闸(2026-09 审计后加) ═══════════
    # 规则**单源**在 memory_gate.py(本仓库根), 本原子与 code_agent_engine
    # 直写共享记忆库(memory_save)复用同一道闸, 不复制两份规则。类级常量/静态方法
    # 保留为对单源 gate 的转发, 语义/原因串与抽前逐字节一致, 兼容既有引用。
    # 依据(据实, 非凭空拍)见 memory_gate 模块头: severity 真实集合={critical,
    # major, minor, info}, info 仅表未证实/试探; 共享记忆实测污染样本
    # 是 probe-/验证共享OK/verify 类自测探针 等噪音; 生产调用方一律 conf=None。
    # 只拦"推共享记忆库"这一步: 本地 remember 先行、无 conf 来源标注、不动 refine/recall。
    NOISE_MARKERS = memory_gate.NOISE_MARKERS
    _LOW_SEVERITY = memory_gate.LOW_SEVERITY
    _CONF_FLOOR = memory_gate.CONF_FLOOR

    @staticmethod
    def _noise(text):
        """命中明显噪音标记(探测/自测/占位等)→ 不进共享。保留本地写不受影响。"""
        return memory_gate.is_noise(text)

    @staticmethod
    def _should_share(text, severity=None, conf=None):
        """推共享记忆库前的最小质量闸(转调单源 memory_gate.should_share)。"""
        return memory_gate.should_share(text, severity=severity, conf=conf)

    # ── 内部：把一条经验 push 到共享记忆库，返回 (ok, note_id_or_msg) ──
    def _opt_push(self, text, severity=None, conf=None):
        """主写共享库；失败静默返回 (False,msg)，由调用方保证本地降级已发生。"""
        if not om.available():
            return False, "记忆库未配置或不可达"
        return om.save(text, severity=severity, conf=conf)

    # ── 从一条 finding(或字符串)提炼经验文本 + 严重度 ──
    @staticmethod
    def _finding_text(f):
        if isinstance(f, dict):
            text = f.get("suggestion") or f.get("title") or ""
            sev = f.get("severity")
            return (text, sev)
        return (str(f) if f else "", None)

    def _save(self, findings, task="", memdir=None, conf=None):
        """经验沉淀：主写共享记忆库([code][conf]) + 本地 lessons.json 降级后备。"""
        memdir = memdir or se.DEFAULT_MEM
        # 1) 本地始终写入(降级后备/私标副本)，保证不因共享层失败丢记忆
        added = se.remember(findings, task=task, memdir=memdir)
        # 2) 主写共享记忆库：每条 finding 逐条 note，带 [code]+conf 前缀
        #    质量闸拦截低质/噪音/未证实项(本地写已在上步发生，仅拦共享推送)。
        shared_ids, shared_errs, blocked = [], [], []
        if isinstance(findings, dict):
            findings = [findings]
        if isinstance(findings, str):
            findings = [{"title": findings}]
        for f in (findings or []):
            text, sev = self._finding_text(f)
            if not text:
                continue
            share, why = self._should_share(text, severity=sev, conf=conf)
            if not share:
                blocked.append({"text": text[:60], "severity": sev, "conf": conf,
                                "reason": why})   # 仅拦共享，本地已写
                continue
            ok, info = self._opt_push(text, severity=sev, conf=conf)
            (shared_ids if ok else shared_errs).append(info)
        # 3) 判定主通道是否真走共享(供诚实汇报)
        if shared_ids:
            source = "optmem"
        elif blocked and om.available():
            source = "optmem_filtered"  # 共享可达但本轮条目均被质量闸拦下
        elif om.available():
            source = "optmem_empty"   # 共享可达但本轮无有效条目
        else:
            source = "local"          # 外部共享记忆库不可达 → 本地降级
        return {"added": added, "lessons": se._load(memdir, se._LESSONS),
                "memdir": memdir, "optmem": {
                    "source": source,
                    "shared_ids": shared_ids,
                    "shared_errors": shared_errs[:3],
                    "blocked": blocked,     # 被质量闸拦下(未进共享、仍在本地)
                }}

    def _recall(self, task, memdir=None, top_k=3):
        """跨会话召回：优先从共享记忆库(域隔离[code])取回，失败降级本地 self_prompt。"""
        memdir = memdir or se.DEFAULT_MEM
        shared_up = om.available()  # 共享库可达(目录存在且未 OPTMEM_OFF=1)
        ok, hits = (om.recall(task, top_k=top_k) if shared_up else (False, []))
        if ok and hits:
            prompt = om._fmt_prompt(hits)
            shared = True
        else:
            # 外部共享记忆库无命中或不可达(含显式关闭) → 本地 experience/*.json 召回(降级后备)
            prompt = se.self_prompt(task, memdir=memdir, top_k=top_k)
            hits = []
            shared = bool(shared_up and ok and not hits)  # 区分"共享空命中"与"共享不可达"
        return {"prompt": prompt,
                "lessons": se._load(memdir, se._LESSONS),
                "refinements": se._load(memdir, se._REFINES),
                "skills": se._load(memdir, se._SKILLS),
                "optmem": {"source": "optmem" if shared else "local",
                           "hits": hits, "recall_ok": ok, "shared_up": shared_up}}

    def _sediment(self, task, action, bucket, memdir=None):
        """技能沉淀：主写共享记忆库([code][conf:0.9]) + 本地 skills.json 双写。"""
        memdir = memdir or se.DEFAULT_MEM
        before = len(se._load(memdir, se._SKILLS))
        se._sediment_skill(task, action, bucket, memdir)     # 本地去重沉淀(后备)
        skill_line = f"沉淀技能[{bucket}]: {action}"
        # 质量闸：空/占位/未证实 action 不进共享(本地 _sediment_skill 已写)；技能默认 conf=0.9
        share, why = self._should_share(skill_line, severity=None, conf=0.9)
        if not share:
            return {"skills": se._load(memdir, se._SKILLS),
                    "added": len(se._load(memdir, se._SKILLS)) - before,
                    "optmem": {"source": "local_filtered", "note": why,
                               "blocked": {"text": skill_line, "reason": why}}}
        ok, info = self._opt_push(skill_line, severity=None, conf=0.9)
        return {"skills": se._load(memdir, se._SKILLS),
                "added": len(se._load(memdir, se._SKILLS)) - before,
                "optmem": {"source": "optmem" if ok else "local",
                           "note": info}}


agent = CodeMemoryAgent()

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="code-memory 原子自测入口")
    ap.add_argument("--capability", default="memory.save",
                    choices=["memory.save", "memory.recall", "memory.sediment"])
    ap.add_argument("--task", default="实现加法函数 add(a,b)")
    ap.add_argument("--memdir", default=None)
    args = ap.parse_args()
    agent.load()
    print("══ code-memory 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    if args.capability == "memory.save":
        r = agent.run(_capability="memory.save", findings=[{"severity": "major", "title": "缺边界值", "suggestion": "补 None/空串/0 处理"}], task=args.task, memdir=args.memdir)
    elif args.capability == "memory.recall":
        r = agent.run(_capability="memory.recall", task=args.task, memdir=args.memdir)
    else:
        r = agent.run(_capability="memory.sediment", task=args.task, action="先补参数校验", bucket="P", memdir=args.memdir)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
