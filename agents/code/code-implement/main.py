#!/usr/bin/env python3
"""code-implement 原子（写码引擎，自包含真实实现）。open_source:true，真写生产代码到磁盘。

真合并(2026-09)：本原子**就是**写码引擎的唯一承载，import codeagent-minimal 根下的
`code_agent_engine`(CodeAgentEngine, 旧引擎的 code_agent.py 整体迁入本仓, 含
PONYTAIL_SYSTEM 写码纪律 + auto_reuse 复用 + loop 迭代 + 落 Obsidian + config)。
**不再 import 旧引擎地址**——那个地址已降为 re-export 转发壳，本原子只
import 本家 code_agent_engine 模块，二者同源(引擎逻辑只住 codeagent-minimal 一处)。

能力域：code。数据经 LLM 生成后落本地磁盘，不额外上传。
能力：
  - code.implement  — 真写生产代码（调本地 code_agent_engine.CodeAgent().implement）
  - code.write      — code.implement 别名
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent

# 本家自包含写码引擎（本仓根, 引擎唯一实现；不再指向外部引擎目录）
from code_agent_engine import CodeAgent as LocalCodeAgent

# 默认落盘目录（引擎 implement 缺省 output_dir 时用 codeagent-minimal/_impl_output）
DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, "_impl_output")


def _load_local_engine():
    """加载本家自包含引擎 CodeAgent 类。失败 → 抛异常由信封兜底降级。"""
    return LocalCodeAgent()


class CodeImplementAgent(AtomicAgent):
    name = "code-implement"
    version = "0.3.0"
    domain = "code"
    description = "写码原子: 底层用本家自包含 code_agent_engine(CodeAgentEngine, 含PONYTAIL+reuse+loop+落Obsidian), 真写生产代码到磁盘; 不再依赖外部旧引擎"
    open_source = True
    provides = ["code.implement", "code.write"]
    depends_on = []  # 直接调本家自包含写码引擎库，非其它原子
    inputs = ["task", "language", "path", "output_dir", "domain", "loop", "max_iter",
               "write_root", "skill_path", "verify"]
    outputs = ["files", "score", "summary", "issues", "versions", "written",
               "write_error", "engine"]

    def _register_defaults(self):
        self.register("code.implement", self._implement)
        self.register("code.write", self._implement)  # 别名

    # ── code.implement / code.write：真写生产代码 ──
    def _implement(self, task=None, language="python", path=None, output_dir=None,
                   domain="general", loop=False, max_iter=5, write_root=None,
                   verify=None, skill_path=None):
        """调本家引擎 CodeAgent().implement() 真写码到磁盘。

        输入：
          task      需求描述（若含"写入文件 <绝对路径>"，引擎会据此落盘）
          language  语言（python/javascript/go/frontend...）
          path      （可选）目标文件绝对路径；给出时并入 output_dir 语义
          output_dir（可选）落盘目录；缺省 → codeagent-minimal/_impl_output
          domain    领域 general/backend/frontend/cli
          loop      是否迭代优化
          max_iter  loop 时最大迭代数
          write_root（可选）项目根：显式给出时才用，缺省不猜（不用 cwd/引擎目录兜底）
        输出：{files, score, summary, issues, versions, written, write_error, engine}
        """
        if not task:
            return self._envelope(False, error="缺少必需参数 task", degraded=True)

        # 落盘目录：output_dir > (path 所在目录) > codeagent-minimal/_impl_output
        tgt_dir = output_dir or (os.path.dirname(os.path.abspath(path)) if path else None) \
            or write_root or DEFAULT_OUTPUT_DIR
        tgt_dir = os.path.normpath(str(tgt_dir))

        try:
            engine = _load_local_engine()  # 返回本家 CodeAgent 实例
        except Exception as e:  # 引擎导入失败 → 降级不隐藏
            return self._envelope(
                False, degraded=True,
                error=f"本家写码引擎 code_agent_engine 导入失败: {type(e).__name__}: {e}")

        try:
            result = engine.implement(
                task=task, language=language, loop=bool(loop),
                max_iter=int(max_iter or 5), domain=domain,
                output_dir=tgt_dir,
                auto_reuse=True,
                skill_path=skill_path,
                write_root=write_root,
                verify=verify,          # 给了 DoD 就在落盘后真跑，分数按执行口径报
                path=path,              # 文件名也要透传：只传目录会让产物一律落 main.py
            )
        except Exception as e:  # 引擎运行异常 → 降级
            return self._envelope(False, degraded=True,
                                  error=f"implement 运行异常: {type(e).__name__}: {e}")

        # 引擎返回 {files,score,...,written} 或 {error:...}
        if isinstance(result, dict) and result.get("error"):
            return self._envelope(False, degraded=True,
                                  error=f"写码引擎返回错误: {result['error']}",
                                  data={"engine": "code_agent_engine.CodeAgent.implement"})
        data = dict(result or {})
        data.setdefault("engine", "code_agent_engine.CodeAgent.implement")
        data.setdefault("output_dir", tgt_dir)

        # 🔴 单发假绿守卫（2026-09-21 实测）：引擎可能返回 ok 形状但**一个文件都没落盘**
        #    （实测回执 files={} / score=0 / 却还有 archive.files 列着目标文件），调用方完全看不出失败。
        #    真落盘只认**磁盘**：written/files 里的路径真存在，或目标 path 真被写过。
        landed = []
        for item in (data.get("written") or []):
            if isinstance(item, str) and os.path.exists(item):
                landed.append(item)
        files = data.get("files")
        for nm in (files.keys() if isinstance(files, dict) else (files or [])):
            cand = str(nm)
            cand = cand if os.path.isabs(cand) else os.path.join(tgt_dir, os.path.basename(cand))
            if os.path.exists(cand):
                landed.append(cand)
        if not landed:
            target_note = ""
            if path:
                ap = os.path.normpath(str(path))
                target_note = ("目标文件 %s %s。" % (ap, "存在但本次没被改动" if os.path.exists(ap) else "不存在"))
            return self._envelope(
                False, degraded=True,
                error=("单发写码没往盘上落任何文件（回执 files=%r、written=%r）—— 未完成，别当成功。%s"
                       % (data.get("files"), data.get("written"), target_note)),
                data=data)
        data["landed"] = landed
        return self._envelope(True, data=data)


agent = CodeImplementAgent()

if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="code-implement 原子 CLI 自测入口")
    ap.add_argument("--capability", default="code.implement",
                    choices=["code.implement", "code.write"])
    ap.add_argument("--task", default="实现一个函数 add(a,b) 返回两数之和，纯标准库")
    ap.add_argument("--language", default="python")
    ap.add_argument("--output_dir", default=None)
    ap.add_argument("--domain", default="general")
    ap.add_argument("--path", default=None)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--max_iter", type=int, default=5)
    ap.add_argument("--write_root", default=None)
    ap.add_argument("--verify", default=None)
    ap.add_argument("--skill_path", default=None,
                    help="规格文件路径：长规格走这里，不要塞进 task")
    args = ap.parse_args()
    agent.load()
    print("══ code-implement 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, task=args.task, language=args.language,
                  output_dir=args.output_dir, domain=args.domain, path=args.path,
                  loop=args.loop, max_iter=args.max_iter, write_root=args.write_root,
                  verify=args.verify, skill_path=args.skill_path)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
