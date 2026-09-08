#!/usr/bin/env python3
"""code-runloop 原子（写码 · 真跑反馈闭环）。open_source:true，纯 stdlib 自包含。

与 code-implement 并列的新写码原子(设计 v2 §1-§10): 把"单发无真跑"升级为
agentic run_loop —— harness 一次 LLM generate 后用真工具执行+观测回喂, 循环到
无 tool_call 收尾 + verify_cmd DoD 硬校验绿。引擎本体(run_loop/tools/llm/prompts)
不 import 任何跨库活目录 / 闭源编排; 唯一 LLM 调用读 config/model_config.json。

能力: code.runloop — 真跑反馈闭环写码。
输入: task / path / output_dir / verify / language / max_iterations / fake_model
输出: files / verify_passed / turns_used / tool_log / trace / engine / ...
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from atomic_base import AtomicAgent            # noqa: E402  仓库内开源壳(纯 stdlib)
from run_loop import run_loop                  # noqa: E402  引擎核心(自包含)

DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, "_impl_output")
DEFAULT_MAX_ITER = 12
TRACE_KEEP = 60


class CodeRunLoopAgent(AtomicAgent):
    name = "code-runloop"
    version = "0.1.0"
    domain = "code"
    description = ("写码原子(真跑反馈闭环, agentic run_loop): 纯 stdlib 自包含, 不进 import 跨库活目录; "
                   "一次 LLM generate + 真工具执行回喂(设计v2), verify_cmd DoD 硬校验绿才算完成。")
    open_source = True
    provides = ["code.runloop"]
    depends_on = []
    inputs = ["task", "path", "output_dir", "verify", "language",
              "max_iterations", "fake_model"]
    outputs = ["files", "summary", "verify_passed", "verify_cmd", "turns_used",
               "tool_log", "trace", "engine", "last_model_text"]

    def _register_defaults(self):
        self.register("code.runloop", self._runloop)

    def _runloop(self, task=None, path=None, output_dir=None, verify=None,
                 language="python", max_iterations=DEFAULT_MAX_ITER,
                 fake_model=None, model_config=None, write_root=None):
        """真跑反馈闭环写码。

        输入: task 任务描述; path 目标文件(权威, 必给做 DoD); verify DoD 命令
              (缺省且 path 以 .py 结尾 → 自动 python -m py_compile path);
              output_dir 工作目录(缺省 path 所在目录); fake_model='smoke' 走
              确定性自纠冒烟(不连网), 否则读 config 真调模型。
        输出: {ok, data:{files, verify_passed, turns_used, tool_log, trace, ...}}
        """
        if not task:
            return {"ok": False, "data": {}, "error": "缺少必需参数 task",
                    "degraded": True}
        target_path = os.path.abspath(path) if path else None
        out_dir = output_dir or (os.path.dirname(target_path) if target_path
                                 else DEFAULT_OUTPUT_DIR)
        out_dir = os.path.normpath(str(out_dir))

        llm_generate = None
        if str(fake_model or "").lower() == "smoke":
            if not target_path:
                return {"ok": False, "data": {}, "degraded": True,
                        "error": "fake_model=smoke 需提供 --path 作为目标文件"}
            from smoke_model import SmokeFake
            llm_generate = SmokeFake(target_path)

        trace = []
        try:
            r = run_loop(task, target_path=target_path, output_dir=out_dir,
                         verify=verify, language=language,
                         llm_generate=llm_generate,
                         max_iterations=int(max_iterations or DEFAULT_MAX_ITER),
                         config_path=model_config, trace=trace,
                         write_root=write_root,
                         allow_skip_verify=False)
        except Exception as e:  # noqa 引擎异常 → 降级不隐藏
            return {"ok": False, "data": {}, "degraded": True,
                    "error": f"run_loop 异常: {type(e).__name__}: {e}"}
        data = {k: v for k, v in r.items() if k != "ok"}
        data["trace"] = trace[-TRACE_KEEP:]
        data["fake_model"] = fake_model or None
        if r["ok"]:
            return {"ok": True, "data": data}
        return {"ok": False, "data": data,
                "error": r.get("reason", "run_loop 未通过 DoD"), "degraded": True}


agent = CodeRunLoopAgent()


def _print_trace(trace):
    print("\n── 轨迹(回合) ──")
    for t in trace:
        if t.get("stage") == "generate_start":
            print(f"[turn {t['turn']}] generate (hist_msgs={t.get('hist_msgs')})")
        elif t.get("stage") == "tool_calls":
            print(f"  → tool_calls ×{t.get('n')}: {t.get('content')!r}")
            obs = (t.get("obs") or "").replace("\n", " ")
            print(f"      obs: {obs[:180]}")
        elif t.get("stage") == "verify_fail":
            print(f"  ✗ DoD 失败 #{t.get('n')}: {t.get('out','')[:180]!r}")
        elif t.get("stage") == "verify_ok":
            print("  ✓ DoD 绿: " + str(t.get("verify_cmd")))
        elif t.get("stage") == "finish_length":
            print(f"  ! FinishReasonLength(len={t.get('len')}) → 续写")
    print("── 轨迹结束 ──\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="code-runloop 原子 CLI(真跑反馈闭环写码自测入口)")
    ap.add_argument("--capability", default="code.runloop",
                    choices=["code.runloop"])
    ap.add_argument("--task", default="实现一个 greet(name) 返回问候语, 纯标准库, 并确保可 import")
    ap.add_argument("--path", default=None, help="目标文件绝对路径(权威, 做 DoD)")
    ap.add_argument("--output_dir", default=None, help="工作目录(缺省 path 所在目录)")
    ap.add_argument("--verify", default=None,
                    help="DoD 校验命令(缺省 .py → python -m py_compile <path>)")
    ap.add_argument("--language", default="python")
    ap.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITER)
    ap.add_argument("--fake-model", default=None, choices=[None, "smoke", "none"],
                    help="smoke=确定性自纠冒烟(不连网); none/省略=读 config 真调模型")
    ap.add_argument("--verbose", action="store_true", help="打印回合轨迹")
    args = ap.parse_args()

    agent.load()
    print(f"══ {agent.name} 原子自测 ══ v{agent.version} status={agent.describe()['status']}")
    r = agent.run(_capability=args.capability, task=args.task, path=args.path,
                  output_dir=args.output_dir, verify=args.verify,
                  language=args.language, max_iterations=args.max_iterations,
                  fake_model=args.fake_model)
    if r.get("ok") and args.verbose:
        _print_trace(r["data"].get("trace", []))
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if r.get("ok") else 1)
