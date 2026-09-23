#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lab_app.py — 本地原子库可视化客户端与编排系统 统一入口（FR7 独立运行）。

用法：
  python lab_app.py [--port 8087] [--host 127.0.0.1] [--token <可选>]
                    [--codeagent-root <原子库路径>] [--target-root <目标仓库>]
  [--data-dir <运行数据目录>]

一个进程、一个目录、零第三方依赖；数据全在本机（SQLite + JSON），无外发流量。
访问: http://127.0.0.1:<port>  →  编排画布/代码浏览/黑箱调试/报告/模型配置/对话
"""
import argparse
import json
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main():
    ap = argparse.ArgumentParser(description="CodeAgent Lab — 本地原子库可视化客户端与编排系统")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--codeagent-root", default=None,
                    help="35 原子库根目录(默认读 lab_config.json)")
    ap.add_argument("--target-root", default=None,
                    help="目标仓库(默认=原子库自身, 可在前端切换)")
    ap.add_argument("--data-dir", default=None, help="运行数据目录(默认 lab/lab_data)")
    args = ap.parse_args()

    from lab_config import get_config, LabConfig
    cfg = get_config()
    if args.host:
        cfg.values["host"] = args.host
    if args.port:
        cfg.values["port"] = args.port
    if args.token is not None:
        cfg.values["token"] = args.token
    if args.codeagent_root:
        cfg.values["codeagent_root"] = os.path.realpath(args.codeagent_root)
    if args.target_root:
        cfg.values["target_root"] = os.path.realpath(args.target_root)
    if args.data_dir:
        for k in ("db_path", "report_dir", "backup_dir", "models_path",
                  "known_defects_extra"):
            cfg.values[k] = os.path.join(os.path.realpath(args.data_dir),
                                         os.path.basename(cfg.values[k]))
    cfg.save()

    from history_db import HistoryDB
    from atom_loader_ext import merged_registry
    from web_viz import serve

    os.makedirs(cfg.get("data_dir", os.path.join(HERE, "lab_data")), exist_ok=True)
    db = HistoryDB(cfg.get("db_path"))
    reg = merged_registry(cfg.codeagent_root())
    server, port = serve(cfg=cfg, db=db)
    host = cfg.get("host") or "127.0.0.1"

    banner = f"""
╔══════════════════════════════════════════════════════════════╗
║  CodeAgent Lab — 本地原子库可视化客户端与编排系统             ║
╠══════════════════════════════════════════════════════════════╣
║  原子库: {cfg.codeagent_root()}
║  目标仓库: {cfg.target_root()}
║  原子: {reg['count']} 个 (核心 {reg['core_count']} + 扩展 {reg['ext_count']})
║  访问: http://{host}:{port}      (Ctrl+C 退出)
║  数据: {cfg.get('db_path')} (全部本机, 无外发流量)
╚══════════════════════════════════════════════════════════════╝"""
    print(banner)
    if reg["conflicts"]:
        print("⚠ 冲突/提示:")
        for c in reg["conflicts"][:8]:
            print("   -", c)
    print("原子:", ", ".join(a["name"] for a in reg["atoms"])[:200], "...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.server_close()


if __name__ == "__main__":
    main()