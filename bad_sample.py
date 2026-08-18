"""bad_sample.py — 含安全/复杂度/边界问题的文件（测试审查）"""
import os
import subprocess

api_key = "sk-123...cdef"

password = "mysecret123"


def run_cmd(user_input):
    os.system("ls " + user_input)


def unsafe_eval(code):
    return eval(code)


def long_function(a):
    if a > 0:
        b = 1
        c = 2
        d = 3
        e = 4
        f = 5
        g = 6
        h = 7
        i = 8
        j = 9
        k = 10
        l = 11
        m = 12
        n = 13
        o = 14
        p = 15
        q = 16
        r = 17
        s = 18
        t = 19
        u = 20
        v = 21
        w = 22
        x = 23
        y = 24
        z = 25
        return z
    return 0


def divide(a, b):
    return a / b
