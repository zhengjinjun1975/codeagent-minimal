#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_arch_svg.py — 从 registry.json 生成「35 原子架构一张图」SVG(文档落盘 docs/)。

纯 stdlib, 只读 registry, 加壳不改核心。生成的 SVG 同时被前端"架构图"视图参考。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REG = os.path.join(ROOT, "registry.json")
OUT = os.path.join(ROOT, "docs", "arch_atoms.svg")

def load_atoms():
    with open(REG, encoding="utf-8") as f:
        reg = json.load(f)
    agents = reg.get("agents") or {}
    atoms = []
    for name, m in agents.items():
        atoms.append({
            "name": name,
            "domain": m.get("domain", "generic"),
            "description": m.get("description", ""),
            "provides": m.get("provides", []),
        })
    atoms.sort(key=lambda a: (a["domain"], a["name"]))
    return atoms

def build(atoms):
    W, H = 1400, 200
    groups = {}
    for a in atoms:
        groups.setdefault(a["domain"], []).append(a)
    domains = sorted(groups)
    cols, pad = 4, 14
    cellw = (W - 2 * 18 - 3 * pad) / 4
    rowh = 150
    rows = (len(domains) + cols - 1) // cols
    H = 120 + rows * rowh
    s = []
    s.append('<?xml version="1.0" encoding="UTF-8"?>')
    s.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Microsoft YaHei, sans-serif">')
    # 背景
    s.append(f'<rect width="{W}" height="{H}" fill="#f5f7fa"/>')
    # 标题
    s.append(f'<rect x="18" y="14" width="{W-36}" height="44" rx="8" fill="#0b6bcb"/>')
    s.append(f'<text x="36" y="34" fill="#ffffff" font-size="17" font-weight="bold">CodeAgent Lab — 原子化完整代码智能体 · 原子架构一张图({len(atoms)} 原子)</text>')
    s.append(f'<text x="36" y="51" fill="#cfe6fb" font-size="11">纯 stdlib 零依赖 · 数据不出私域 · 加壳不改核心 · 黑箱调试状态机 · 拖拽编排 · 模型本地/云端可配置</text>')
    # 壳层横条
    s.append(f'<rect x="18" y="66" width="{W-36}" height="24" rx="5" fill="#efe9fb"/>')
    s.append(f'<text x="36" y="82" fill="#5a3fb0" font-size="12">Interface 壳(lab/, 新增不改核心): 可视化客户端 · 拖拽编排 pipeline · 黑箱调试状态机 · 模型配置 · 报告聚合 · 对话路由 · 事件总线 · SQLite 历史库</text>')
    s.append(f'<rect x="18" y="94" width="{W-36}" height="20" rx="4" fill="#e6f2fc"/>')
    s.append(f'<text x="36" y="108" fill="#0b6bcb" font-size="11">统一入口 codeagent.py · agent_runtime 能力路由 · agent_loader 拓扑加载 · 组装链 chain / guard / evolve-loop</text>')
    # 原子分组网格
    y0 = 122
    for i, d in enumerate(domains):
        gx = 18 + (i % cols) * (cellw + pad)
        gy = y0 + (i // cols) * rowh
        atoms_d = groups[d]
        block_h = 38 + len(atoms_d) * 16
        s.append(f'<rect x="{gx:.0f}" y="{gy}" width="{cellw:.0f}" height="{block_h}" rx="7" fill="#ffffff" stroke="#d9e0ea"/>')
        s.append(f'<rect x="{gx:.0f}" y="{gy}" width="{cellw:.0f}" height="26" rx="7" fill="#7a3ff2"/>')
        s.append(f'<text x="{gx+9:.0f}" y="{gy+17:.0f}" fill="#ffffff" font-size="11.5" font-weight="600">{d} ({len(atoms_d)})</text>')
        for j, a in enumerate(atoms_d):
            ay = gy + 30 + j * 16
            s.append(f'<circle cx="{gx+11:.0f}" cy="{ay+5:.0f}" r="3" fill="#9c6cf2"/>')
            s.append(f'<text x="{gx+20:.0f}" y="{ay+9:.0f}" font-size="9.5" fill="#1f2a37">{a["name"]}</text>')
    # 图例
    ly = H - 34
    s.append(f'<text x="18" y="{ly}" fill="#6b7a90" font-size="11">图例: 6 大领域分组 · 每块=一个原子(manifest: name/domain/provides/depends_on) · 原子全部只读复用, 扩展走 lab/extensions 壳层挂接</text>')
    s.append(f'<text x="18" y="{ly+16}" fill="#6b7a90" font-size="11">架构: Interface 壳 → Application 入口 → {len(domains)} 领域原子 → 根层算法内核(review.py/security_scan.py/dep_scan.py/method_impact.py/...)</text>')
    s.append('</svg>')
    return "\n".join(s)

def main():
    atoms = load_atoms()
    svg = build(atoms)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"OK: {OUT} ({len(atoms)} atoms, {len(svg)} bytes)")

if __name__ == "__main__":
    main()