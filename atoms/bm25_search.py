#!/usr/bin/env python3
"""极简代码原子（来自 Obsidian 代码原子库）。复用优先·极简落地方法论。"""
import math, re
from collections import defaultdict, Counter

def tokenize(text):
    text = text.lower()
    han = re.findall(r'[\u4e00-\u9fff]', text)   # 中文单字
    en  = re.findall(r'[a-z0-9_]+', text)        # 英文/数字词
    toks = en + han
    for i in range(len(han) - 1):                # 中文双字 bigram
        toks.append(han[i] + han[i+1])
    return toks

class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.n = len(docs)
        self.dl = [len(tokenize(d)) for d in docs]
        self.avgdl = sum(self.dl) / max(1, self.n)
        self.df = defaultdict(int); self.tf = []
        for d in docs:
            c = Counter(tokenize(d)); self.tf.append(c)
            for t in c: self.df[t] += 1
        self.k1, self.b = k1, b
    def score(self, q):
        s = [0.0] * self.n
        for t in set(tokenize(q)):
            if t not in self.df: continue
            idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i, c in enumerate(self.tf):
                tf = c.get(t, 0)
                if tf:
                    s[i] += idf * tf * (self.k1+1) / (tf + self.k1*(1 - self.b + self.b*self.dl[i]/self.avgdl))
        return sorted(range(self.n), key=lambda i: -s[i])

if __name__ == '__main__':
    # 演示用法（提取自代码原子的'使用要点'）
    pass

