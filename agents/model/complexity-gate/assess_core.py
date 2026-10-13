# agents/model/complexity-gate/assess_core.py
"""编排前的复杂度闸：判断任务是否需要走长链编排。纯标准库，不调模型。"""

_COMPLEX_WORDS = ("重构", "迁移", "批量", "多个文件", "全库", "测试", "兼容", "新增能力")
_SIMPLE_WORDS = ("改名", "加一行", "查一下", "解释", "格式化", "打印")

_DEFAULT_RULES = {"complex": _COMPLEX_WORDS, "simple": _SIMPLE_WORDS}


def _conservative(reason):
    return {"complex": True, "reason": reason, "est_steps": 0,
            "layer": "fallback", "confident": False, "degraded": True}


def assess_complexity(task, rules=None, hints=None):
    try:
        rules = rules or _DEFAULT_RULES
        text = task if isinstance(task, str) else str(task)
        for word in rules.get("complex", ()):
            if word in text:
                return {"complex": True, "reason": "命中复杂规则: %s" % word,
                        "est_steps": 0, "layer": "rule", "confident": True, "degraded": False}
        for word in rules.get("simple", ()):
            if word in text:
                return {"complex": False, "reason": "命中简单规则: %s" % word,
                        "est_steps": 0, "layer": "rule", "confident": True, "degraded": False}
        return _conservative("规则未命中，保守判为复杂")
    except Exception as exc:  # 任何异常一律兜底
        return _conservative("评估异常，保守判为复杂: %r" % exc)


if __name__ == "__main__":
    r1 = assess_complexity("重构整个模块并迁移到新接口")
    assert r1["complex"] is True and r1["layer"] == "rule" and r1["confident"] is True, r1

    r2 = assess_complexity("帮我把变量改名")
    assert r2["complex"] is False and r2["layer"] == "rule" and r2["confident"] is True, r2

    r3 = assess_complexity("今天天气不错")
    assert r3["complex"] is True and r3["layer"] == "fallback" \
        and r3["confident"] is False and r3["degraded"] is True, r3

    # 异常兜底分支
    r4 = assess_complexity(None, rules={"complex": None})
    assert r4["complex"] is True and r4["degraded"] is True, r4

    print("OK")
