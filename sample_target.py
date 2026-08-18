"""sample_target.py — 测试 harness 的目标模块（含边界问题）"""


def divide(a, b):
    """除法：b=0 会抛 ZeroDivisionError（边界问题）。"""
    return a / b


def parse_int(s):
    """解析整数：非数字输入会抛 ValueError（边界问题）。"""
    return int(s)


def greet(name):
    """打招呼：name 为 None 会抛 AttributeError。"""
    return f"你好, {name.upper()}"


def total(items):
    """求和：items 含非数字会抛 TypeError。"""
    return sum(items)


def healthy(x):
    """健康函数：正确处理边界。"""
    if x is None:
        return "empty"
    return str(x)
