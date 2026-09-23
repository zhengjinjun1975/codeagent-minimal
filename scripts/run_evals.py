import os
import sys
import json
import glob
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent_runtime import AgentRuntime


def load_cases(root):
    cases = []
    draft_count = 0
    pattern = os.path.join(root, "evals", "cases", "*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        if data.get("status") == "draft":
            draft_count += 1
            continue
        cases.append(data)
    return cases, draft_count


def _dig(data, path):
    if not path:
        return data
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def judge(data, expect):
    if not isinstance(expect, dict):
        return False
    value = _dig(data, expect.get("path"))
    if "equals" in expect:
        return value == expect["equals"]
    if "contains" in expect:
        needle = expect["contains"]
        if isinstance(value, (str, list)):
            return needle in value
        return needle in str(value)
    if "at_least" in expect:
        if isinstance(value, (list, tuple, dict, str)):
            value = len(value)
        try:
            return value >= expect["at_least"]
        except TypeError:
            return False
    return False


def run_case(case, runtime):
    call = case.get("call") or {}
    args = call.get("arguments") or {}
    # 题集用相对路径（可移植）：相对仓库根解析，避免绑定某台机器的绝对路径
    if isinstance(args.get("path"), str) and not os.path.isabs(args["path"]):
        args = dict(args, path=os.path.join(ROOT, args["path"]))
    env = runtime.run_capability(call.get("capability"), **args)
    passed = judge(env, case.get("expect") or {})
    if passed:
        return True, ""
    try:
        reason = json.dumps(env, ensure_ascii=False)[:300]
    except Exception as exc:
        reason = "unserializable: %s" % exc
    return False, reason


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--only", default=None)
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args()

    cases, draft_count = load_cases(ROOT)
    if opts.only:
        cases = [c for c in cases if c.get("id") == opts.only]

    runtime = AgentRuntime()
    results = []
    unstable = []
    details = {}

    for case in cases:
        cid = case.get("id")
        title = case.get("title", "")
        hits = 0
        last_reason = ""
        for _ in range(opts.k):
            try:
                ok, reason = run_case(case, runtime)
            except Exception as exc:
                ok, reason = False, "exception: %s" % exc
            if ok:
                hits += 1
            else:
                last_reason = reason
        stable = hits == opts.k
        results.append((cid, title, hits, stable))
        if not stable:
            unstable.append(cid)
            details[cid] = last_reason

    total = len(cases)
    passed = sum(1 for _, _, _, s in results if s)
    pass_k = (passed / total) if total else 0.0

    if opts.json:
        out = {
            "总题数": total,
            "通过数": passed,
            "pass_k": pass_k,
            "草稿数": draft_count,
            "不稳过题": unstable,
            "失败详情": details,
        }
        print(json.dumps(out, ensure_ascii=False))
    else:
        for cid, title, hits, stable in results:
            flag = "PASS" if stable else "FAIL"
            print("  %s  %s  %s  (%d 次 %d/%d)" % (flag, cid, title, opts.k, hits, opts.k))
        print("题目数 %d / 通过数 %d / pass_k %.2f / 草稿 %d" % (total, passed, pass_k, draft_count))
        if unstable:
            print("不稳过题: " + ", ".join(unstable))

    if total == 0:
        return 2
    if unstable:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())