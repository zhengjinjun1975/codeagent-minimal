#!/usr/bin/env python3
"""run_loop.py — code-runloop 真跑反馈闭环 harness 核心(纯 stdlib, 自包含)。

设计 §2/§3: 模型一次 generate 后用真工具执行+观测回喂, 循环到:
  1) 该 turn 无 tool_call(仅收尾文本) 且 verify_cmd DoD 硬校验绿 → 完成;
  2) 或 max_iterations 硬停。
失败把真实输出当 user 消息回喂重来; 工具输出截断保尾防爆 context;
捕获 FinishReasonLength → prefill 续写提示。
"""
import json
import os
import re

from prompts import (build_system_prompt, build_task_message,
                     FINISH_REASON_LENGTH_HINT)
import tools

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
MAX_USER_OBS_CHARS = 30000   # 单条回喂消息字节帽, 防 context 爆


def extract_tool_calls(text):
    """从模型文本提取 <tool_call> 块并 json 解析。返回 (calls, bad_chunks)。
    calls: list[{"name","args","raw"}]。bad_chunks: list[str] 无法解析的原文。"""
    calls, bad = [], []
    for m in TOOL_CALL_RE.finditer(text or ""):
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError("tool_call 须为 JSON 对象")
            calls.append({"name": str(obj.get("name", "")), "args": obj.get("args", {}),
                          "raw": raw})
        except Exception as e:  # noqa
            bad.append(f"<tool_call> 无法解析({type(e).__name__}: {e}): {raw[:160]}")
    return calls, bad


def _trunc_obs(text, cap=MAX_USER_OBS_CHARS):
    text = text or ""
    return text if len(text) <= cap else "[obs 超长截断 ...] " + text[-cap:]


def _fmt_result(name, res):
    """把单个工具返回 dict 格式化为 <tool_result> 文本(前缀状态/exit code, 后缀 exit_code)。"""
    st = res.get("status", "ok")
    if st != "ok":
        reason = res.get("reason") or res.get("output") or str(res)
        return (f"<tool_result name={name} status=error>\n{_trunc_obs(str(reason))}\n"
                "</tool_result>")
    code = res.get("exit_code")
    if "exit_code" in res and code is not None:
        body = (res.get("output") or
                f"[{name} 完成 exit_code={code}]")
        suffix = f"\n[exit code {code}]"
    else:
        body = (res.get("content") or res.get("msg") or
                f"[{name} 完成: " + json.dumps({k: v for k, v in res.items()
                                                if k not in ("status",)}, ensure_ascii=False) + "]")
        suffix = ""
    if res.get("timeout"):
        body = "(超时, 哨兵 124)\n" + body
    return f"<tool_result name={name} status=ok>\n{_trunc_obs(body)}\n{suffix}\n</tool_result>"


def execute_tool(name, args, roots, cwd):
    """按名分发到 tools.* 真执行。返回工具 dict。未知工具/越界 → status error。"""
    args = dict(args or {})
    if "path" in args and roots:
        # path 越界由各工具内 ensure_inside 拒绝, 这里只需透传 roots
        args["roots"] = roots
    try:
        if name == "run":
            return tools.run(args.get("cmd", ""), cwd=args.get("cwd") or cwd,
                             timeout=args.get("timeout", tools.DEFAULT_TIMEOUT))
        if name == "read_file":
            return tools.read_file(args.get("path"), args.get("a"), args.get("b"), roots)
        if name == "grep":
            return tools.grep(args.get("pattern"), args.get("path"), roots)
        if name == "write":
            return tools.write(args.get("path"), args.get("content", ""), roots)
        if name == "edit":
            return tools.edit(args.get("path"), args.get("old", ""),
                              args.get("new", ""), roots,
                              dry_run=args.get("dry_run", True))
        if name == "verify_cmd":
            return tools.verify_cmd(args.get("cmd", ""), cwd=args.get("cwd") or cwd)
        return {"status": "error", "reason": f"未知工具: {name}"}
    except Exception as e:  # noqa
        return {"status": "error", "reason": f"工具执行异常: {type(e).__name__}: {e}"}


def execute_calls(calls, roots, cwd, files_written, tool_log, turn):
    """真执行一轮里所有 tool_call(顺序), 收集观测文本; 记录已写文件与轨迹。"""
    parts = []
    for i, call in enumerate(calls, 1):
        name, args = call["name"], call["args"]
        # 解析失败的块: 转成错误观测回喂, 不让模型静默失败
        if call.get("parse_error"):
            parts.append(_fmt_result(name or "?",
                                     {"status": "error",
                                      "reason": call["parse_error"]}))
            continue
        res = execute_tool(name, args, roots, cwd)
        # 记录成功写盘文件
        if res.get("status") == "ok" and res.get("path"):
            files_written.add(os.path.normpath(res["path"]))
        # 记录 edit dry-run 通过但未落盘 → 提示模型落盘
        if name == "edit" and res.get("status") == "ok" and res.get("dry_run"):
            res["msg"] = (res.get("msg", "") + " | dry-run 通过: 如需落盘请把 dry_run 设为 false 重发")
        tool_log.append({"turn": turn, "tool": name, "status": res.get("status"),
                         "arg": str(args)[:120]})
        parts.append(_fmt_result(name, res))
    return "\n".join(parts)


# run_loop 主函数体在下方由分片续写填入
def run_loop(task, *, target_path=None, output_dir=None, verify=None,
             language="python", llm_generate=None, max_iterations=10,
             temp=0.3, max_tokens=32000, config_path=None,
             system_prompt=None, trace=None, write_root=None,
             allow_skip_verify=False, max_verify_failures=4):
    """设计 §2/§3 主循环。llm_generate 若省略 → 读 config 的纯 stdlib generate。
    trace(可选 list) 每轮记录 {turn, content[:120], n_calls, obs[:200]} 供回读/冒烟。"""
    from llm import generate as _std_generate  # lazy: fake 注入时零网络

    gen_fn = llm_generate or (lambda msgs: _std_generate(
        msgs, temp=temp, max_tokens=max_tokens, config_path=config_path))

    cwd = output_dir or (os.path.dirname(os.path.abspath(target_path))
                         if target_path else os.getcwd())
    os.makedirs(cwd, exist_ok=True)
    roots = tools.resolve_roots(write_root or output_dir or cwd)

    verify_cmd = (verify or "").strip()
    verify_defaulted = False
    if not verify_cmd and target_path and str(target_path).endswith(".py"):
        verify_cmd = f"python -m py_compile {target_path}"
        verify_defaulted = True

    sys_p = system_prompt or build_system_prompt()
    task_msg = build_task_message(task, target_path=target_path,
                                  white_root=roots[0] if roots else cwd,
                                  verify_cmd=verify_cmd, language=language)
    history = [{"role": "system", "content": sys_p},
               {"role": "user", "content": task_msg}]
    if trace is not None:
        trace.append({"turn": 0, "stage": "task",
                      "target_path": target_path, "verify_cmd": verify_cmd or None,
                      "roots": roots, "output_dir": cwd})

    files_written = set()
    tool_log = []
    verify_failures = 0
    last_model_text = ""
    result = {"ok": False, "reason": "未进入循环"}

    for turn in range(1, int(max_iterations) + 1):
        if trace is not None:
            trace.append({"turn": turn, "stage": "generate_start",
                          "hist_msgs": len(history)})
        resp = gen_fn(history)
        if resp.get("error"):
            history.append({"role": "user",
                            "content": f"[harness] 模型调用失败: {resp['error']} —— 若为 API/网络问题请换 llm 通道后重试"})
            if trace is not None:
                trace.append({"turn": turn, "stage": "llm_error", "error": resp["error"]})
            result = {"ok": False, "reason": f"模型调用失败: {resp['error']}",
                      "llm_error": resp["error"]}
            break

        content = resp.get("content") or ""
        last_model_text = content
        calls, bad = extract_tool_calls(content)

        # 有 tool_call 块 → 真执行真回喂
        if calls or bad:
            for b in bad:
                history.append({"role": "user", "content": b})
            obs = execute_calls(calls, roots, cwd, files_written, tool_log, turn)
            history.append({"role": "assistant", "content": content})
            if obs.strip():
                history.append({"role": "user", "content": obs})
            else:
                history.append({"role": "user",
                                "content": "[harness] 本轮无有效工具观测, 请继续/收尾"})
            if trace is not None:
                trace.append({"turn": turn, "stage": "tool_calls", "n": len(calls),
                              "content": content[:120], "obs": obs[:300]})
            continue

        # 无 tool_call → 收尾候选; FinishReasonLength → prefill 续写
        if resp.get("finish_reason") == "length":
            history.append({"role": "assistant", "content": content})
            history.append({"role": "user", "content": FINISH_REASON_LENGTH_HINT})
            if trace is not None:
                trace.append({"turn": turn, "stage": "finish_length", "len": len(content)})
            continue
        if not content.strip():
            history.append({"role": "user", "content": "[harness] 模型返回空文本, 请重试(输出 tool_call 或收尾总结)"})
            continue

        # 完成判据: DoD 硬校验
        history.append({"role": "assistant", "content": content})
        if not verify_cmd:
            if allow_skip_verify:
                result = {"ok": True, "summary": content,
                          "verify_passed": None, "verify_cmd": None,
                          "verify_output": "", "verify_defaulted": verify_defaulted}
                break
            result = {"ok": False, "reason": "模型已收尾但无 DoD verify_cmd, 且 allow_skip_verify=False",
                      "summary": content, "verify_passed": None,
                      "verify_cmd": None, "verify_defaulted": verify_defaulted}
            break
        v = tools.verify_cmd(verify_cmd, cwd=cwd)
        # P1-2 谎报守卫: 若 verify 是自动 py_compile(defaulted) 且整轮没真写过任何文件
        # (纯 read/dry-run edit 后收尾), 判定为"未真改动"的疑似谎报 → 回喂强制真写, 不直接 ok。
        if v["ok"] and verify_defaulted and not files_written:
            if trace is not None:
                trace.append({"turn": turn, "stage": "verify_no_write",
                              "files_written": sorted(files_written)})
            history.append({"role": "user",
                            "content": "[harness] 校验通过但本轮未真写任何文件(仅 read/dry-run)。"
                                       "本任务要求改动文件, 请用 edit(write/dry_run=false) 真正落盘改动后再收尾; "
                                       "若确无文件需改动请说明, 否则视为未完成。"})
            continue
        if v["ok"]:
            result = {"ok": True, "summary": content, "verify_passed": True,
                      "verify_cmd": verify_cmd, "verify_defaulted": verify_defaulted,
                      "verify_output": v.get("output", "")}
            if trace is not None:
                trace.append({"turn": turn, "stage": "verify_ok",
                              "verify_cmd": verify_cmd})
            break
        # DoD 失败 → 真实输出当新消息回喂重来
        verify_failures += 1
        fail_out = (v.get("output") or f"exit_code={v.get('exit_code')} "
                    f"timeout={v.get('timeout')}").strip()
        history.append({"role": "user",
                        "content": f"[DoD 校验失败 #{verify_failures}] 命令: {verify_cmd}\n"
                                   f"真实输出:\n{fail_out}\n\n请依据以上真实失败修改到绿(别假装通过)。"})
        if trace is not None:
            trace.append({"turn": turn, "stage": "verify_fail", "n": verify_failures,
                          "out": fail_out[:200]})
        if verify_failures >= int(max_verify_failures):
            result = {"ok": False,
                      "reason": f"DoD 校验连续失败 {verify_failures} 次达到上限",
                      "verify_cmd": verify_cmd, "verify_output": fail_out,
                      "verify_passed": False}
            break
        continue

    if not result.get("ok"):
        result.setdefault("reason", "达到 max_iterations 仍未通过 DoD")
    result["ok"] = bool(result.get("ok"))
    result["turns_used"] = min(turn, int(max_iterations)) if locals().get("turn") else 0
    result["max_iterations"] = int(max_iterations)
    result["verify_failures"] = verify_failures
    result["verify_passed"] = result.get("verify_passed", False)
    result["files"] = sorted(files_written)
    result["tool_log"] = tool_log
    result["last_model_text"] = last_model_text
    result["output_dir"] = os.path.normpath(cwd)
    result["engine"] = "code-runloop.run_loop (agentic, 真跑反馈闭环)"
    return result
