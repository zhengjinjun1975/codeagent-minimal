"""规则层选链：关键词命中模板表，直接给出能力序列，省掉一次模型调用。纯标准库、确定性。"""
import json
import os
import sys

_TEMPLATES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chain_templates.json")


def load_templates(path=None):
    try:
        with open(path or _TEMPLATES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data["templates"]
        if not isinstance(raw, list):
            return []
    except Exception:
        return []
    out = []
    for tpl in raw:
        if not isinstance(tpl, dict):
            continue
        name, kws, chain = tpl.get("name"), tpl.get("keywords"), tpl.get("chain")
        if not isinstance(name, str) or not isinstance(kws, list) or not isinstance(chain, list):
            continue
        if not chain:
            continue
        out.append({"name": name, "keywords": kws, "chain": chain, "note": tpl.get("note", "")})
    return out


def score_template(task, tpl):
    if not isinstance(task, str) or not isinstance(tpl, dict):
        return {"score": 0, "hits": []}
    kws = tpl.get("keywords")
    if not isinstance(kws, list):
        return {"score": 0, "hits": []}
    low = task.lower()
    hits, seen = [], set()
    for kw in kws:
        if not isinstance(kw, str) or kw in seen:
            continue
        if kw.lower() in low:
            seen.add(kw)
            hits.append(kw)
    return {"score": len(hits), "hits": hits}


def filter_available(chain, available):
    if available is None:
        return list(chain), []
    try:
        avail = set(available)
    except TypeError:
        return list(chain), []
    kept, dropped = [], []
    for step in chain:
        cap = step.get("capability") if isinstance(step, dict) else None
        if cap in avail:
            kept.append(step)
        else:
            dropped.append(cap)
    return kept, dropped


def _build_chain(caps):
    chain, used = [], {}
    for item in caps:
        if isinstance(item, dict):            # 模板可写能力名，或 {"capability":..,"inputs":{..}}
            cap = item.get("capability")
            extra = {k: v for k, v in item.items() if k != "capability"}
        else:
            cap, extra = item, {}
        if not isinstance(cap, str):
            continue
        step = cap.split(".")[-1]
        n = used.get(step, 0) + 1
        used[step] = n
        entry = {"step": step if n == 1 else "%s-%d" % (step, n), "capability": cap}
        entry.update(extra)
        chain.append(entry)
    return chain


def select_chain(task, templates=None, available=None):
    result = {"matched": False, "source": "none", "template": "", "score": 0,
              "hits": [], "chain": [], "dropped": [], "reason": "规则未命中，需模型兜底"}
    if templates is None:
        templates = load_templates()
    if not isinstance(templates, list):
        return result
    best, best_score, best_hits = None, 0, []
    for tpl in templates:
        if not isinstance(tpl, dict):
            continue
        sc = score_template(task, tpl)
        if sc["score"] > best_score:
            best, best_score, best_hits = tpl, sc["score"], sc["hits"]
    if best is None:
        return result
    caps = [c for c in best.get("chain", []) if isinstance(c, (str, dict))]
    full = _build_chain(caps)
    kept, dropped = filter_available(full, available)
    result["template"] = best.get("name", "")
    result["score"] = best_score
    result["hits"] = best_hits
    result["dropped"] = dropped
    if not kept:
        result["reason"] = "规则命中的链在可用能力里没有一步能用"
        return result
    result["matched"] = True
    result["source"] = "rule"
    result["chain"] = kept
    result["reason"] = "规则命中模板 %s（命中 %d 词，链 %d 步）" % (result["template"], best_score, len(kept))
    return result


if __name__ == "__main__":
    def _fail(msg):
        print("self-check FAILED: " + msg)
        sys.exit(1)

    tpls = [
        {"name": "impl", "keywords": ["实现", "写代码"], "chain": ["code.implement", "test.gen", "test.run"]},
        {"name": "review", "keywords": ["审查"], "chain": ["codereview.review", "codereview.deep"]},
    ]

    r1 = select_chain("帮我实现一个功能", templates=tpls)
    if not (r1["matched"] is True and r1["source"] == "rule" and len(r1["chain"]) == 3):
        _fail("命中分支: %r" % r1)

    r2 = select_chain("今天天气不错", templates=tpls)
    if not (r2["matched"] is False and r2["chain"] == [] and "模型兜底" in r2["reason"]):
        _fail("未命中分支: %r" % r2)

    r3 = select_chain("帮我实现一个功能", templates=tpls, available=["plan.think"])
    if not (r3["matched"] is False and r3["dropped"] and "没有一步能用" in r3["reason"]):
        _fail("过滤分支: %r" % r3)

    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cs_bad.json")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("{ not valid json ")
        if load_templates(tmp) != []:
            _fail("坏 JSON 容错")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print("chain_selector self-check OK")