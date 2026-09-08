# -*- coding: utf-8 -*-
"""errors.py — 错误契约原子: 统一错误类(带 code + 非敏感 msg)。

极简原则: 错误契约集中一处, 所有模块依赖, 不重复定义。
agentskit 原则: 一个 contract 包拥有 error model, 其他都依赖它。
"""
from __future__ import annotations


class ApiError(Exception):
    """统一错误契约: 携带面向客户端的 code + 非敏感 msg。

    用途: Web API / CLI 等对外接口, 只回非敏感信息, 内部细节不泄露。
    """

    def __init__(self, code: int = 400, msg: str = "请求失败", internal: str = ""):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.internal = internal  # 内部细节, 不返回给客户端

    def to_dict(self) -> dict:
        return {"error": self.msg, "code": self.code}


class DataSourceError(ApiError):
    """数据源读取错误(替代静默返回空)。"""

    def __init__(self, msg: str = "数据源读取失败", internal: str = ""):
        super().__init__(400, msg, internal)
