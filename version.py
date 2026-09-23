#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""version.py — 版本单点（本库发布版本的唯一来源）。

改版本只改这一个地方；CHANGELOG 顶部条目应与它一致。
Lab 前端顶部显示的就是这里的 __version__（后端 /api/atoms 转发）。
"""

__version__ = "0.6.1"

VERSION = __version__        # 兼容旧读法

if __name__ == "__main__":
    print(__version__)
