#!/usr/bin/env python3
"""smoke_model.py — code-runloop 确定性自纠冒烟用的脚本化"模型"。

不连网。目的: 证明 harness 是"真跑反馈闭环"而非单发 —— 第一次真写入一个**语法错误**的
目标文件但不自查 → harness 跑 DoD(py_compile) 捕获真实 SyntaxError → 把真实失败回喂 →
本 fake 读到 DoD 失败消息后才真写入**修复版** → 收尾 → harness 再跑 DoD 转绿。

turn 序列(真实工具/真 py_compile, 非打地鼠):
  1. write BUGGY 目标          (故意带语法错)
  2. (无 tool_call → harness DoD) → 真 py_compile 失败, 回喂真实报错
  3. write FIXED 目标          (仅在读到 DoD 失败后才修复 = 反馈驱动)
  4. (无 tool_call → harness DoD) → 真 py_compile 绿 → ok
"""
import json

BUGGY = ('def greet(name):\n'
         '    return "Hello, " + name\n'
         'print(greet("world")')           # 少个右括号 → SyntaxError

FIXED = ('def greet(name):\n'
         '    """Return a greeting for name."""\n'
         '    return "Hello, " + name\n'
         '\n'
         'if __name__ == "__main__":\n'
         '    print(greet("world"))\n')


def _saw_dod_fail(history):
    return any("DoD 校验失败" in (m.get("content") or "") for m in history)


class SmokeFake:
    """(history)->resp dict 的可调用 fake, 遵循 run_loop 期待的统一 resp 形态。"""

    def __init__(self, target_path):
        self.target = str(target_path)
        self.wrote_bug = False
        self.fixed = False
        self.calls = 0

    def _tool_content(self, content_body):
        path_j = json.dumps(self.target)
        content_j = json.dumps(content_body)
        return ("<tool_call>\n"
                '{"name": "write", "args": {"path": ' + path_j +
                ', "content": ' + content_j + '}}\n'
                "</tool_call>")

    def __call__(self, history):
        self.calls += 1
        dod_fail = _saw_dod_fail(history)
        if not self.wrote_bug:
            self.wrote_bug = True
            return {"content": self._tool_content(BUGGY),
                    "finish_reason": None, "error": None}
        if dod_fail and not self.fixed:
            self.fixed = True
            return {"content": self._tool_content(FIXED),
                    "finish_reason": None, "error": None}
        # 无 DoD 失败(等 harness 首次抓 bug) 或 已修复 → 收尾让 harness 跑 DoD
        return {"content": "目标文件已写好, 请跑 DoD 校验确认绿。",
                "finish_reason": None, "error": None}
