#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atom_runner.py — 原子执行器（NFR6：原子调用进子进程，防挂死 HTTP 线程 + 信封隔离）。

混合策略（真实可用为本）：
  1) 重型能力（测试/审查/链/护栏/模糊/依赖扫描…）→ subprocess 调 codeagent.py 统一入口
  2) 扩展原子（lab/extensions，模板统一 run_cli）→ subprocess 调 <ext>/main.py --capability
  3) 其余轻量原子 → 进程内信封调用（agent_loader.load_agent 只读加载 + AtomicAgent.call）
每次调用返回 {ok, data?, error?, degraded?, elapsed, source}。失败绝不虚构、绝不抛给上层。
"""
import json
import os
import subprocess
import sys
import time

from lab_config import get_config

# 重型能力 → codeagent.py 子命令（子进程执行）
_HEAVY_SUBCMD = {
    "codereview.review": ("review", ["path"]),
    "test.run": ("test", ["path"]),
    "test.gen": ("test", ["path"]),
    "test.tdd": ("test", ["path"]),
    "impact.analyze": ("impact", ["path", "symbol"]),
    "plan.think": ("plan", ["task"]),
    "plan.gen": ("plan", ["task"]),
    "dispatch.template": ("dispatch", []),
    "dispatch.budget": ("dispatch", ["task"]),
    "project.load": ("project", ["path"]),
    "reuse.local": ("reuse", ["path", "content"]),
    "memory.save": ("memory", ["findings", "task"]),
    "skill.list": ("skill", []),
    "skill.load": ("skill", ["name"]),
    "skill.export": ("skill", ["name"]),
    "mcp.tools": ("mcp", []),
    "mcp.list": ("mcp", []),
    "mcp.call": ("mcp", ["tool"]),
    "evolve.refine": ("evolve", ["task", "outcome"]),
    "depscan.scan": ("dep-scan", ["path"]),
    "depscan.sca": ("dep-scan", ["path"]),
    "depscan.taint": ("dep-scan", ["path"]),
    "depscan.chainbreak": ("dep-scan", ["path"]),
    "llm.list_models": ("models", []),
    "deliver.report": ("deliver", ["chain", "outputs"]),
    "taskstate.list": ("taskstate", []),
}


def run_capability(atom_name, capability, params=None, timeout=180,
                   registry=None, codeagent_root=None):
    """执行单个原子能力，返回信封。params: dict（值须可 JSON 序列化）。"""
    from atom_loader_ext import find_atom, atom_abs_dir
    from lab_config import get_config as _gc
    cfg = _gc()
    root = codeagent_root or cfg.codeagent_root()
    reg = registry
    if reg is None:
        from atom_loader_ext import merged_registry
        reg = merged_registry(root)
    atom = find_atom(atom_name, reg)
    if atom is None:
        return {"ok": False, "error": f"原子不存在: {atom_name}", "degraded": True,
                "elapsed": 0, "source": "runner"}
    if capability not in atom["provides"]:
        return {"ok": False, "error": f"能力 {capability} 不在 {atom_name} 的 provides 中",
                "degraded": True, "elapsed": 0, "source": "runner"}
    params = dict(params or {})
    t0 = time.time()

    # 1) 扩展原子 → subprocess main.py --capability（模板统一 run_cli，args 平铺 --k v）
    if atom.get("origin") == "ext":
        a_dir = atom_abs_dir(atom, cfg)
        entry = os.path.join(a_dir, atom.get("entry") or "main.py")
        if os.path.isfile(entry):
            args = [sys.executable, entry, "--capability", capability]
            declared = set(atom.get("inputs") or [])
            for k, v in params.items():
                if v is None:
                    continue
                if declared and k not in declared:
                    continue  # 未声明的入参不转发（避免 argparse 拒绝）
                if isinstance(v, (dict, list)):
                    args += [f"--{k}", json.dumps(v, ensure_ascii=False)]
                else:
                    args += [f"--{k}", str(v)]
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                      encoding="utf-8", errors="replace", env=env)
                elapsed = round(time.time() - t0, 2)
                out = (proc.stdout or "").strip()
                # run_cli 首行是横幅，找最后一个 JSON 块
                env_out = _last_json(out) or _last_json(proc.stderr or "")
                if env_out is not None:
                    env_out.setdefault("elapsed", elapsed)
                    env_out.setdefault("source", "subprocess:ext")
                    return env_out
                return {"ok": False, "error": "扩展原子无 JSON 输出（退出码 "
                        f"{proc.returncode}）", "raw": out[-1000:], "degraded": True,
                        "elapsed": elapsed, "source": "subprocess:ext"}
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": f"原子超时(>{timeout}s)", "degraded": True,
                        "elapsed": round(time.time() - t0, 2), "source": "subprocess:ext"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}: {e}", "degraded": True,
                        "elapsed": round(time.time() - t0, 2), "source": "subprocess:ext"}

    # 2) 重型能力 → codeagent.py 子命令（子进程）
    if capability in _HEAVY_SUBCMD:
        subcmd, arg_names = _HEAVY_SUBCMD[capability]
        args = [sys.executable, os.path.join(root, "codeagent.py"), subcmd, "--json"]
        if subcmd == "deliver":
            # deliver 的 chain/outputs 是 --chain/--outputs 选项(非位置参数), 需特殊传参
            for opt, an in (("--chain", "chain"), ("--outputs", "outputs")):
                v = params.get(an)
                if v is None:
                    # 缺必参 → 诚实失败（不猜测拼装）
                    return {"ok": False, "error": f"能力 {capability} 缺参数 {an}",
                            "degraded": True, "elapsed": round(time.time() - t0, 2),
                            "source": "runner"}
                args += [opt, json.dumps(v, ensure_ascii=False)]
        else:
            for an in arg_names:
                v = params.get(an) or params.get(_alias(an))
                if v is None:
                    # 缺必参 → 诚实失败（不猜测拼装）
                    return {"ok": False, "error": f"能力 {capability} 缺参数 {an}",
                            "degraded": True, "elapsed": round(time.time() - t0, 2),
                            "source": "runner"}
                if isinstance(v, (dict, list)):
                    args.append(json.dumps(v, ensure_ascii=False))
                else:
                    args.append(str(v))
        if subcmd == "impact" and params.get("symbol"):
            args += ["--symbol", str(params["symbol"])]
        if subcmd == "dispatch" and arg_names and params.get("task"):
            args += ["--task", str(params["task"])]
        if subcmd == "test":
            args += ["--no-mutation"]  # 流水线回归默认不跑变异（耗时）
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace",
                                  cwd=root)
            elapsed = round(time.time() - t0, 2)
            out = (proc.stdout or "").strip()
            try:
                data = json.loads(out)
                env = data.get(subcmd) if isinstance(data, dict) and subcmd in data else data
            except json.JSONDecodeError:
                return {"ok": False, "error": "codeagent 输出非 JSON", "raw": out[-1000:],
                        "stderr": proc.stderr[-1000:], "degraded": True,
                        "elapsed": elapsed, "source": f"subprocess:{subcmd}"}
            if isinstance(env, dict) and env.get("ok") is not None:
                env.setdefault("elapsed", elapsed)
                env.setdefault("source", f"subprocess:{subcmd}")
                return env
            return {"ok": True, "data": env, "elapsed": elapsed,
                    "source": f"subprocess:{subcmd}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"原子超时(>{timeout}s)", "degraded": True,
                    "elapsed": round(time.time() - t0, 2), "source": f"subprocess:{subcmd}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "degraded": True,
                    "elapsed": round(time.time() - t0, 2), "source": f"subprocess:{subcmd}"}

    # 3) 轻量原子 → 进程内（agent_loader 只读加载 + call，信封隔离）
    try:
        a_dir = atom_abs_dir(atom, cfg)
        _insert_root(root)
        import agent_loader as al
        res = al.load_agent(a_dir)
        if not res["ok"]:
            return {"ok": False, "error": res["error"], "degraded": True,
                    "elapsed": round(time.time() - t0, 2), "source": "inproc"}
        agent = res["data"]["agent"]
        # P0-2 边注入适配：仅把原子声明的入参传给能力函数，避免编排边注入的
        # 未声明参数（如 input/evidence）被通用原子静默丢弃或抛 TypeError。
        declared = set(atom.get("inputs") or [])
        call_params = dict(params)
        if declared:
            call_params = {k: v for k, v in params.items() if k in declared}
        env = agent.call(capability, **call_params)
        # 参数提示：若原子真实失败，返回「声明入参 vs 实际传入」差异供前端定位
        if not env.get("ok") and declared:
            missed = sorted(declared - set(params))
            extra = sorted(set(params) - declared)
            if missed or extra:
                env.setdefault("data", {})
                env["param_diff"] = {"missing": missed, "ignored": extra}
        env.setdefault("elapsed", round(time.time() - t0, 2))
        env.setdefault("source", "inproc")
        return env
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "degraded": True,
                "elapsed": round(time.time() - t0, 2), "source": "inproc"}


def _alias(an):
    return {"path": "target", "task": "task", "findings": "findings"}.get(an, an)


def _insert_root(root):
    if root and root not in sys.path:
        sys.path.insert(0, root)


def _last_json(text):
    """从文本里提取最后一个 JSON 对象/数组（容忍横幅前缀）。"""
    if not text:
        return None
    idx = text.rfind("{")
    while idx >= 0:
        try:
            obj = json.loads(text[idx:])
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
        idx = text.rfind("{", 0, idx)
    idx = text.rfind("[")
    while idx >= 0:
        try:
            obj = json.loads(text[idx:])
            if isinstance(obj, list):
                return {"data": obj}
        except (json.JSONDecodeError, ValueError):
            pass
        idx = text.rfind("[", 0, idx)
    return None