#!/usr/bin/env python3
"""code-graph 探索原子（open_source: true，新原子）。

把 CodeGraph（colbymchenry/codegraph，MIT，本地代码知识图）接成 CodeAgent 的一个能力：
一次查询拿到「相关符号 + 调用关系 + 逐字原文」，等价于已经做过一轮 Read。

口径（照官方基准与本地实测，别当万能）：
- 只在**跨文件结构性问题**上划算（谁调我 / 改这个影响谁 / 这条链路怎么串）；
- 单点查证（某个常量在哪）用 grep 更省——实测反向 100 倍；
- 一次返回是**稠密 payload**，会长期占用上下文（官方七仓平均残留 +82%），
  所以默认回传裁剪摘要 + 全量落盘，不把整块原文直接灌进对话；
- 索引缺失不猜、不兜底成 grep：回 degraded 并提示先 `codegraph init`。

零依赖：纯标准库（subprocess/shutil/time/json）。
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

DEFAULT_TIMEOUT = 120
MAX_INLINE_CHARS = 6000          # 回传上限：超了就落盘 + 给摘要，避免一次灌爆上下文
CHARS_PER_TOKEN = 3.6            # 代码+CJK 混合的经验系数，仅用于估算


class CodeExploreAgent(AtomicAgent):
    name = "code-explore"
    version = "0.1.0"
    domain = "code"
    description = ("代码知识图探索原子: 调本地 codegraph explore，一次返回相关符号、调用关系与逐字原文；"
                   "只适合跨文件结构问题，单点查证请用 grep；索引缺失回 degraded，不静默兜底。")
    provides = ["code.explore", "code.graph_status", "code.graph_init"]
    depends_on = []
    inputs = ["query", "project", "timeout", "max_inline_chars", "force"]
    outputs = ["payload", "chars", "tokens_est", "elapsed_s", "truncated", "spill_path", "status"]

    def _bin(self):
        return os.environ.get("CODEGRAPH_BIN") or shutil.which("codegraph") or ""

    def _register_defaults(self):
        self.register("code.explore", self._explore)
        self.register("code.graph_status", self._graph_status)
        self.register("code.graph_init", self._graph_init)

    # ── 内部 ──────────────────────────────────────────────
    def _run_cli(self, args, cwd, timeout):
        exe = self._bin()
        if not exe:
            return None, "未找到 codegraph 可执行文件（装法：npm i -g @colbymchenry/codegraph；或设 CODEGRAPH_BIN）"
        try:
            p = subprocess.run([exe] + args, cwd=cwd, capture_output=True,
                               timeout=timeout, shell=False)
        except subprocess.TimeoutExpired:
            return None, "codegraph 超时（%ss）：跨仓大查询请缩小问题或调大 timeout" % timeout
        except OSError as exc:
            return None, "启动 codegraph 失败：%s" % exc
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        if p.returncode != 0:
            return None, "codegraph 退出码 %s：%s" % (p.returncode, (err or out)[:300])
        return out, None

    # 宪法例外: 参数表要吸收运行时透传的额外入参（**_）；收成 5 个就得给调用方改参数名
    def _explore(self, query=None, project=None, timeout=DEFAULT_TIMEOUT,
                 max_inline_chars=MAX_INLINE_CHARS, force=False, **_):
        query = (query or "").strip()
        if not query:
            return self._envelope(False, degraded=True, error="query 不能为空")
        if len(query) < 6 and not force:
            return self._envelope(False, degraded=True,
                                  error="问题过短（<6 字符）：单点查证用 grep 更省，真要跑请带 force=True")
        cwd = project or REPO_ROOT
        if not os.path.isdir(cwd):
            return self._envelope(False, degraded=True, error="project 目录不存在：%s" % cwd)
        if not os.path.isdir(os.path.join(cwd, ".codegraph")):
            return self._envelope(False, degraded=True,
                                  error="该项目未建索引：先在 %s 跑 codegraph init（本原子不静默兜底成 grep）" % cwd)

        t0 = time.time()
        out, err = self._run_cli(["explore", query], cwd, timeout)
        elapsed = round(time.time() - t0, 2)
        if err:
            return self._envelope(False, degraded=True, error=err, elapsed_s=elapsed)

        chars = len(out)
        truncated = chars > max_inline_chars
        spill_path = None
        payload = out
        if truncated:
            os.makedirs(os.path.join(REPO_ROOT, "artifacts"), exist_ok=True)
            spill_path = os.path.join(REPO_ROOT, "artifacts", "codegraph_%d.md" % int(t0))
            with open(spill_path, "w", encoding="utf-8") as fh:
                fh.write(out)
            # 头部保留（问题与命中列表），尾部给符号清单，正文靠 spill 文件按需取
            payload = out[:max_inline_chars] + ("\n\n…[截断] 全文 %d 字符已落盘：%s" % (chars, spill_path))

        return self._envelope(True, data={
            "query": query,
            "project": cwd,
            "payload": payload,
            "chars": chars,
            "tokens_est": int(round(chars / CHARS_PER_TOKEN)),
            "elapsed_s": elapsed,
            "truncated": truncated,
            "spill_path": spill_path,
        })

    def _graph_status(self, project=None, **_):
        cwd = project or REPO_ROOT
        out, err = self._run_cli(["status"], cwd, 60)
        if err:
            return self._envelope(False, degraded=True, error=err)
        return self._envelope(True, data={"project": cwd, "status_text": out[:2000]})

    def _graph_init(self, project=None, timeout=600, **_):
        cwd = project or REPO_ROOT
        t0 = time.time()
        out, err = self._run_cli(["init"], cwd, timeout)
        if err:
            return self._envelope(False, degraded=True, error=err)
        return self._envelope(True, data={"project": cwd, "elapsed_s": round(time.time() - t0, 2),
                                          "output": out[-1500:]})


agent = CodeExploreAgent()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="code-explore 原子自测入口")
    ap.add_argument("--capability", default="code.explore",
                    choices=["code.explore", "code.graph_status", "code.graph_init"])
    ap.add_argument("--query", default="how does the runloop dispatch work")
    ap.add_argument("--project", default=REPO_ROOT)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = ap.parse_args()
    agent.load()
    print("══ code-explore 原子自测 ══", agent.describe()["name"],
          "status=" + agent.describe()["status"])
    if args.capability == "code.explore":
        r = agent.run(_capability="code.explore", query=args.query,
                      project=args.project, timeout=args.timeout)
    elif args.capability == "code.graph_status":
        r = agent.run(_capability="code.graph_status", project=args.project)
    else:
        r = agent.run(_capability="code.graph_init", project=args.project)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:3000])
    if not r["ok"] and not r.get("degraded"):
        sys.exit(1)
