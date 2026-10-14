"""出网白名单判据：纯函数 + 计数器，不联网、不写文件。"""
import os
import sys
from urllib.parse import urlparse

# 模块级计数器：命中白名单记 allowed，未命中记 denied（audit 模式也记 denied）
_STATS = {"allowed": 0, "denied": 0}

# 固定允许的本机地址
_LOCAL = ("127.0.0.1", "localhost", "::1")


def _split_env(name):
    """读取环境变量，逗号分隔，strip、去空、去重、保序。"""
    raw = os.environ.get(name, "")
    out = []
    for item in raw.split(","):
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out


def allowed_hosts():
    """本机 + CODEAGENT_EGRESS_ALLOW + CODEAGENT_MODEL_HOSTS，去重保序。"""
    out = list(_LOCAL)
    for name in ("CODEAGENT_EGRESS_ALLOW", "CODEAGENT_MODEL_HOSTS"):
        for item in _split_env(name):
            if item not in out:
                out.append(item)
    return out


def _match(host, entries):
    """host 与条目相等，或以 "."+条目 结尾；大小写不敏感。

    条目允许带前缀点（如 .foo.cn，与 DNS 后缀写法一致）：先剥掉前缀点再比，
    否则 "." + ".foo.cn" 拼成 "..foo.cn" 永远匹配不上（自检 ④ 抓到的真缺陷）。
    """
    h = host.lower()
    for e in entries:
        e = e.lower().lstrip(".")
        if not e:
            continue
        if h == e or h.endswith("." + e):
            return True
    return False


def egress_check(url):
    """判定 URL 是否允许出网，返回 {allowed, host, reason, mode}。"""
    mode = os.environ.get("CODEAGENT_EGRESS_MODE", "audit")
    try:
        host = urlparse(url).hostname
    except Exception:
        host = None
    if not host:
        _STATS["denied"] += 1
        return {"allowed": False, "host": "", "reason": "URL 解析失败", "mode": mode}

    hit = _match(host, allowed_hosts())
    if hit:
        _STATS["allowed"] += 1
    else:
        _STATS["denied"] += 1

    if mode == "enforce":
        allowed = hit
        reason = "命中白名单" if hit else "不在白名单"
    else:
        allowed = True
        reason = "审计：实际未拦" + ("（命中白名单）" if hit else "（不在白名单）")
    return {"allowed": allowed, "host": host, "reason": reason, "mode": mode}


def egress_stats():
    """返回计数器副本，改它不影响内部状态。"""
    mode = os.environ.get("CODEAGENT_EGRESS_MODE", "audit")
    return {"allowed": _STATS["allowed"], "denied": _STATS["denied"], "mode": mode}


def reset_egress_stats():
    """计数器归零。"""
    _STATS["allowed"] = 0
    _STATS["denied"] = 0


if __name__ == "__main__":
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    # 保存并恢复环境变量
    saved = {k: os.environ.get(k) for k in
             ("CODEAGENT_EGRESS_MODE", "CODEAGENT_EGRESS_ALLOW", "CODEAGENT_MODEL_HOSTS")}

    def restore():
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    try:
        # ① enforce 下本机地址 allowed=True
        os.environ["CODEAGENT_EGRESS_MODE"] = "enforce"
        os.environ.pop("CODEAGENT_EGRESS_ALLOW", None)
        os.environ.pop("CODEAGENT_MODEL_HOSTS", None)
        r1 = egress_check("http://127.0.0.1:8791/x")
        r2 = egress_check("http://localhost/y")
        check("① 本机地址 enforce 允许", r1["allowed"] and r2["allowed"])

        # ② audit 模式下白名单外地址 allowed=True 且 denied 增 1
        os.environ["CODEAGENT_EGRESS_MODE"] = "audit"
        reset_egress_stats()
        before = egress_stats()["denied"]
        r3 = egress_check("https://evil.com/a")
        after = egress_stats()["denied"]
        check("② audit 放行且 denied+1", r3["allowed"] and after == before + 1)

        # ③ enforce 下白名单外地址 allowed=False
        os.environ["CODEAGENT_EGRESS_MODE"] = "enforce"
        r4 = egress_check("https://evil.com/a")
        check("③ enforce 拦截白名单外", r4["allowed"] is False)

        # ④ 自定义白名单后缀匹配
        os.environ["CODEAGENT_EGRESS_ALLOW"] = "api.example.com,.foo.cn"
        r5 = egress_check("https://api.example.com/v1")
        r6 = egress_check("https://x.foo.cn/a")
        r7 = egress_check("https://evil.com/a")
        check("④ 白名单相等/后缀匹配", r5["allowed"] and r6["allowed"] and not r7["allowed"])

        # ⑤ 非法 URL
        r8 = egress_check("not a url")
        check("⑤ 非法 URL 拒绝且提示解析失败",
              r8["allowed"] is False and "解析失败" in r8["reason"])

        # ⑥ egress_stats 返回副本
        s = egress_stats()
        s["allowed"] = 99999
        check("⑥ stats 返回副本", egress_stats()["allowed"] != 99999)
    finally:
        restore()

    if fails:
        print("SELFCHECK FAIL:", fails)
        sys.exit(1)
    print("SELFCHECK PASS")
    sys.exit(0)