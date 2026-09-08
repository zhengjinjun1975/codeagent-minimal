#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory_gate.py — 推共享记忆库前的唯一质量闸（本仓库单源）。

2026-09 收口：code-memory 原子(agents/memory/code-memory/main.py)与其旁路——
code_agent_engine 直写共享记忆库的 memory_save 路径，此前各带一套低置信度
(0.3/0.5)起步逻辑。本模块把规则收敛为**单份定义**，两端 import 复用，
不再复制两份规则；保持 code-memory 已验规则语义不变(拦什么/放什么/原因串逐字节一致)。

规则依据(据实，非凭空拍)：
  - 生产调用方 memory.save/sediment 一律 conf=None(见 agent_runtime.evolve_loop /
    codeagent.memory / CLI)；
  - findings 来自 review 产生器，severity 真实取值集合 = {critical, major, minor, info}，
    其中 info 只用于"未证实/试探"项(如 review.py 的"可能未定义"、LSP severity 旁路)；
  - 实测污染样本样例(probe- / 验证共享OK / verify 类自测探针 / 重复补None)
    都是探测/自测/未证实噪音。
故闸 = 低质(severity=info / 显式 conf<0.5) + 明显噪音标记(探测/自测/占位)。
只拦"推共享记忆库"这一步：调用方须保证本地/业务已先行写或可舍弃；不动 recall/refine。

无第三方依赖、纯标准库、不读网络、数据不出厂。
"""
import re

# 明显噪音/自测/占位标记(实测污染样本归纳)。
NOISE_MARKERS = (
    r"probe[-_:\s]|自测|验证.*共享|共享.*验证|placeholder|"
    r"\bxxx\b|TODO|占位|待补|测试.*验证"
)
LOW_SEVERITY = {"info"}          # conf≈0.3 试探/未证实级
CONF_FLOOR = 0.5                 # 普通经验线(0.5)，低于此=试探/噪音，不进共享


def is_noise(text):
    """命中明显噪音标记(探测/自测/占位等)→ 不进共享。保留本地写不受影响。"""
    return bool(re.search(NOISE_MARKERS, text or "", re.IGNORECASE))


def should_share(text, severity=None, conf=None):
    """推共享记忆库前的最小质量闸。

    返回 (share:bool, reason:str)。share=False 时调用方仍须保证本地/业务已写
    (数据不出厂)或可舍弃。阈值全部来自上面依据的真实调用形态：
      - severity 为低质集 {info}(真实 info 仅表达未证实/试探) → 拦；
      - 显式传 conf 且 < CONF_FLOOR(0.5, 试探级) → 拦(生产虽缺省 None，留作来源标注兜底)；
      - 文本命中噪音标记(probe/自测/占位等，实测污染样本) → 拦；
      - 其余(critical/major/minor/无 severity) 放行——因为 minor 带可执行 suggestion，
        且 optmem 对缺省 severity 默认 conf=0.5(普通经验)，不应误伤。
    """
    if not (text or "").strip():
        return False, "empty_text"
    if severity is not None and str(severity).lower() in LOW_SEVERITY:
        return False, f"low_severity={severity}"          # 未证实/试探级
    if conf is not None:
        try:
            if float(conf) < CONF_FLOOR:
                return False, f"low_conf={conf}"          # 显式试探级 conf
        except (TypeError, ValueError):
            pass                                          # 非法 conf 交 optmem 兜底
    if is_noise(text):
        return False, "noise_marker"                       # 探测/自测/占位噪音
    return True, "ok"
