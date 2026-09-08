#!/usr/bin/env python3
"""code-implement 原子（写码引擎，自包含真实实现）。open_source:true，真写生产代码到磁盘。

本原子就是写码引擎的唯一承载：import 本仓库根下的 `code_agent_engine`
(自包含写码引擎(含工程纪律 + 自动复用 + 迭代循环)
+ 可选工作日志落库 + config)。引擎逻辑只住 code_agent_engine 一处，本原子只做加壳转发。

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

# 本仓库自包含写码引擎(唯一实现)
from code_agent_engine import CodeAgent as LocalCodeAgent

# 默认落盘目录（引擎 implement 缺省 output_dir 时用 仓库根/_impl_output）
DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, "_impl_output")


def _load_local_engine():
    """加载本仓库自包含写码引擎。失败 → 抛异常由信封兜底降级。"""
    return LocalCodeAgent()


class CodeImplementAgent(AtomicAgent):
    name = "code-implement"
    version = "0.2.0"
    domain = "code"
    description = "写码原子: 底层用本仓库自包含写码引擎(含工程纪律+复用+循环+可选工作日志), 真写生产代码到磁盘"
    open_source = True
    provides = ["code.implement", "code.write"]
    depends_on = []  # 直接调本仓库自包含写码引擎库
    inputs = ["task", "language", "path", "output_dir", "domain", "loop", "max_iter"]
    outputs = ["files", "score", "summary", "issues", "versions", "written",
               "write_error", "engine"]

    def _register_defaults(self):
        self.register("code.implement", self._implement)
        self.register("code.write", self._implement)  # 别名

    # ── code.implement / code.write：真写生产代码 ──
    def _implement(self, task=None, language="python", path=None, output_dir=None,
                   domain="general", loop=False, max_iter=5):
        """调本家引擎 CodeAgent().implement() 真写码到磁盘。

        输入：
          task      需求描述（若含"写入文件 <绝对路径>"，引擎会据此落盘）
          language  语言（python/javascript/go/frontend...）
          path      （可选）目标文件绝对路径；给出时并入 output_dir 语义
          output_dir（可选）落盘目录；缺省 → 仓库根/_impl_output
          domain    领域 general/backend/frontend/cli
          loop      是否迭代优化
          max_iter  loop 时最大迭代数
        输出：{files, score, summary, issues, versions, written, write_error, engine}
        """
        if not task:
            return self._envelope(False, error="缺少必需参数 task", degraded=True)

        # 落盘目录：output_dir > (path 所在目录) > 仓库根/_impl_output
        tgt_dir = output_dir or (os.path.dirname(os.path.abspath(path)) if path else None) \
            or DEFAULT_OUTPUT_DIR
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
    args = ap.parse_args()
    agent.load()
    print("══ code-implement 原子自测 ══", agent.describe()["name"], "status=" + agent.describe()["status"])
    r = agent.run(_capability=args.capability, task=args.task, language=args.language,
                  output_dir=args.output_dir, domain=args.domain, path=args.path,
                  loop=args.loop)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if not r["ok"]:
        sys.exit(1)
