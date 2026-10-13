# -*- coding: utf-8 -*-
"""cli_providers.py — 外部 CLI 子代理 provider 链（纯 stdlib，数据不出厂）。

用途：把本机装着的代码 CLI（codex / claude / qodercli 之类）包装成统一 provider，
让上层按「先探测可用性、再选第一个能用的、拿统一信封回来」的方式派活。

红线：
  · 纯 stdlib（shutil/subprocess/json/os/time），不引第三方。
  · 不硬编码绝对路径、不硬编码密钥；命令名靠 which 解析，路径可用配置覆盖。
  · 默认不出网：provider 表只描述本机命令；要不要跑由调用方显式发起。
  · 失败绝不假装成功：探测不到 / 超时 / 退出码非 0 → 如实返回 ok=False + degraded。

可配置（env）：
  SUBAGENT_CLI_PROVIDERS   JSON 数组，覆盖默认 provider 表（字段：name/bin/probe_args/exec_args）
  SUBAGENT_CLI_TIMEOUT     单次派活超时秒，默认 600
  SUBAGENT_CLI_PROBE_TTL   探测结果缓存秒，默认 300
"""
import json
import os
import shutil
import subprocess
import time

DEFAULT_PROVIDERS = [
    {"name": "codex", "bin": "codex", "probe_args": ["--version"],
     "exec_args": ["exec", "--json"], "kind": "code-cli"},
    {"name": "claude", "bin": "claude", "probe_args": ["--version"],
     "exec_args": ["-p"], "kind": "code-cli"},
    {"name": "qodercli", "bin": "qodercli", "probe_args": ["--version"],
     "exec_args": ["-p"], "kind": "code-cli"},
]

_CACHE = {}
TEXT_LIMIT = 4000


def _providers():
    """provider 表：env 覆盖优先，否则用内置默认表。坏 JSON 一律退回默认表。"""
    raw = os.environ.get("SUBAGENT_CLI_PROVIDERS", "").strip()
    if raw:
        try:
            got = json.loads(raw)
            if isinstance(got, list) and got:
                return [p for p in got if isinstance(p, dict) and p.get("bin")]
        except Exception:
            pass
    return list(DEFAULT_PROVIDERS)


def _ttl():
    try:
        return float(os.environ.get("SUBAGENT_CLI_PROBE_TTL", "300"))
    except Exception:
        return 300.0


def _timeout():
    try:
        return float(os.environ.get("SUBAGENT_CLI_TIMEOUT", "600"))
    except Exception:
        return 600.0


def probe(name=None):
    """探测一个（或全部）provider 是否可用：先 which，再真跑一次 --version。

    返回 {name, bin, found, path, version, ok, reason, cached}
    ok 只由「找到命令且 --version 退出码 0」决定；探测失败不抛异常。
    """
    now = time.time()
    out = []
    for p in _providers():
        if name and p.get("name") != name:
            continue
        key = "probe:" + str(p.get("bin"))
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _ttl():
            out.append(dict(hit[1], cached=True))
            continue
        binname = p.get("bin")
        rec = {"name": p.get("name") or binname, "bin": binname, "kind": p.get("kind", ""),
               "found": False, "path": "", "version": "", "ok": False, "reason": "", "cached": False}
        path = shutil.which(binname)
        if not path:
            rec["reason"] = "命令不在 PATH"
        else:
            rec["found"] = True
            rec["path"] = path
            try:
                r = subprocess.run([path] + list(p.get("probe_args") or ["--version"]),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=20)
                rec["version"] = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[0][:120] \
                    if ((r.stdout or "") + (r.stderr or "")).strip() else ""
                rec["ok"] = (r.returncode == 0)
                rec["reason"] = "可用" if rec["ok"] else ("探测退出码 %s" % r.returncode)
            except Exception as exc:
                rec["reason"] = "探测异常: %r" % exc
        _CACHE[key] = (now, dict(rec))
        out.append(rec)
    return out


def list_providers():
    """列出 provider 表与各自可用性（探测结果带缓存）。"""
    probes = {r["name"]: r for r in probe()}
    items = []
    for p in _providers():
        r = probes.get(p.get("name") or p.get("bin")) or {}
        items.append({"name": p.get("name") or p.get("bin"), "bin": p.get("bin"),
                      "kind": p.get("kind", ""), "available": bool(r.get("ok")),
                      "path": r.get("path", ""), "version": r.get("version", ""),
                      "reason": r.get("reason", "")})
    usable = [i["name"] for i in items if i["available"]]
    return {"providers": items, "count": len(items), "usable": usable,
            "usable_count": len(usable),
            "chain": usable}


def _clip(text):
    t = text or ""
    return t if len(t) <= TEXT_LIMIT else t[:TEXT_LIMIT] + "\n…[已截断，共 %d 字符]" % len(t)


def dispatch(task, provider=None, cwd=None, timeout=None):
    """派活给外部 CLI 子代理。

    provider=None → 按降级链自动取第一个可用的；都不行 → ok=False + degraded（如实说无人可派）。
    返回 {provider, available, command, exit_code, stdout_tail, parsed, degraded, reason}
    """
    tasks = probe()
    usable = [r for r in tasks if r.get("ok")]
    if provider:
        chosen = next((r for r in tasks if r.get("name") == provider), None)
        if chosen is None:
            return {"ok": False, "degraded": True, "reason": "provider 不在表内: %s" % provider,
                    "provider": provider, "chain": [r.get("name") for r in usable]}
        if not chosen.get("ok"):
            return {"ok": False, "degraded": True, "reason": "provider 不可用: %s" % chosen.get("reason"),
                    "provider": provider, "chain": [r.get("name") for r in usable]}
    else:
        if not usable:
            return {"ok": False, "degraded": True, "reason": "没有任何可用的外部 CLI 子代理",
                    "provider": "", "chain": []}
        chosen = usable[0]
    p = next((x for x in _providers() if (x.get("name") or x.get("bin")) == chosen["name"]), {})
    argv = [chosen["path"]] + list(p.get("exec_args") or []) + [task]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=(timeout or _timeout()), cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"ok": False, "degraded": True, "provider": chosen["name"],
                "reason": "超时", "command": " ".join(argv[:3]), "chain": [x.get("name") for x in usable]}
    except Exception as exc:
        return {"ok": False, "degraded": True, "provider": chosen["name"],
                "reason": "启动异常: %r" % exc, "command": " ".join(argv[:3]),
                "chain": [x.get("name") for x in usable]}
    body = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if (r.stderr or "").strip() else "")
    return {"ok": r.returncode == 0, "degraded": r.returncode != 0,
            "provider": chosen["name"], "exit_code": r.returncode,
            "command": " ".join(argv[:3]), "output_tail": _clip(body[-TEXT_LIMIT:]),
            "chars": len(body), "chain": [x.get("name") for x in usable],
            "reason": "退出码 %s" % r.returncode}


if __name__ == "__main__":
    # 自检 1：列表结构（本机可能一个都没有，也不许抛异常）
    lst = list_providers()
    assert isinstance(lst.get("providers"), list) and lst["count"] >= 1, lst
    assert isinstance(lst.get("chain"), list), lst
    assert all("available" in i for i in lst["providers"]), lst

    # 自检 2：不存在的 provider → 如实失败，不假装成功
    r2 = dispatch("probe", provider="__no_such_cli__")
    assert r2["ok"] is False and r2["degraded"] is True, r2
    assert "不在表内" in r2["reason"] or "不可用" in r2["reason"], r2

    # 自检 3：provider 表可由 env 覆盖，且坏 JSON 退回默认表
    os.environ["SUBAGENT_CLI_PROVIDERS"] = "{ not json"
    assert _providers() == DEFAULT_PROVIDERS, "坏 JSON 应退回默认表"
    os.environ["SUBAGENT_CLI_PROVIDERS"] = json.dumps([{"name": "fake", "bin": "__no_such_bin__"}])
    lst2 = list_providers()
    assert lst2["count"] == 1 and lst2["providers"][0]["name"] == "fake", lst2
    assert lst2["usable_count"] == 0 and lst2["chain"] == [], lst2
    del os.environ["SUBAGENT_CLI_PROVIDERS"]
    print("SELFCHECK PASS")
