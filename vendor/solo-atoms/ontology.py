# -*- coding: utf-8 -*-
"""ontology.py — 本体工厂原子: CSV转OWL / N-Triples解析 / 类型推断 / 词典。

极简原则: 标准库, 零依赖。本体建模(CSV→本体→问答)全链路复用。
从 ontology-analysis/src/csv_to_owl.py + ontology_qa_v3.py 提炼极简原子。
"""
from __future__ import annotations

import csv
import os
import re
from datetime import datetime

# 本体命名空间
NS = "http://factory.example/ontology#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
OWL_INDIV = "http://www.w3.org/2002/07/owl#NamedIndividual"
OWL_DATAPROP = "http://www.w3.org/2002/07/owl#DatatypeProperty"
OWL_OBJPROP = "http://www.w3.org/2002/07/owl#ObjectProperty"
XSD = "http://www.w3.org/2001/XMLSchema#"


def guess_type_xsd(value) -> str:
    """从实际值推断 xsd 类型(数据驱动): string/integer/decimal/boolean/date。"""
    v = str(value).strip()
    if not v:
        return "xsd:string"
    try:
        int(v)
        return "xsd:integer"
    except ValueError:
        pass
    try:
        float(v)
        return "xsd:decimal"
    except ValueError:
        pass
    if v.lower() in ("true", "false"):
        return "xsd:boolean"
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return "xsd:date"
    except ValueError:
        pass
    return "xsd:string"


def local_name(col: str) -> str:
    """列名 -> 局部名: 去下划线/连字符, 首词小写后续驼峰。

    'device_type' -> 'deviceType'; 'line_id' -> 'lineId'
    """
    parts = [p for p in re.split(r"[-_]", col) if p]
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def nt_escape(v) -> str:
    """N-Triples 字面量转义并加引号。"""
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % s


def csv_to_nt(path: str, ns: str = NS) -> list:
    """CSV -> N-Triples 行列表。每表一类, 每行一实例, 每列一数据属性。

    返回: (lines, table_name, instance_count)
    核心: 数据驱动类型推断 + 实例/属性声明。
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []
    if not rows:
        return [], "", 0

    table = os.path.splitext(os.path.basename(path))[0]
    cls = table.capitalize()
    cls_uri = ns + cls

    # 列类型(取非空首值推断)
    prop_types = {}
    for h in headers:
        vals = [r[h] for r in rows if h in r and str(r[h]).strip()]
        prop_types[h] = guess_type_xsd(vals[0]) if vals else "xsd:string"

    L = []
    # 类声明
    L.append(f"<{cls_uri}> <{RDF_TYPE}> <{OWL_CLASS}> .")
    L.append(f"<{cls_uri}> <http://www.w3.org/2000/01/rdf-schema#label> {nt_escape(cls)} .")
    # 数据属性声明
    for h in headers:
        if h.lower() == "id":
            continue
        p = local_name(h)
        L.append(f"<{ns}{p}> <{RDF_TYPE}> <{OWL_DATAPROP}> .")
        L.append(f"<{ns}{p}> <http://www.w3.org/2000/01/rdf-schema#domain> <{cls_uri}> .")
        L.append(f"<{ns}{p}> <http://www.w3.org/2000/01/rdf-schema#range> <{XSD}{prop_types[h].split(':')[1]}> .")
    # 实例
    for i, row in enumerate(rows):
        inst_id = row.get("id") or str(i + 1)
        inst_uri = f"{cls_uri}_{inst_id}"
        L.append(f"<{inst_uri}> <{RDF_TYPE}> <{cls_uri}> .")
        for h in headers:
            if h.lower() == "id" or h not in row or not str(row[h]).strip():
                continue
            val = str(row[h]).strip()
            t = prop_types[h].split(":")[1]
            L.append(f"<{inst_uri}> <{ns}{local_name(h)}> {nt_escape(val)}^^<{XSD}{t}> .")
    return L, table, len(rows)


def parse_nt(path_or_lines) -> list:
    """解析 N-Triples 为三元组列表 [(s, p, o)]。单行格式, 每行一个三元组。"""
    triples = []
    lines = path_or_lines if isinstance(path_or_lines, list) else \
        open(path_or_lines, encoding="utf-8").read().splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 形如 <s> <p> <o> . 或 <s> <p> "o"^^<type> .
        m = re.match(r"<([^>]+)>\s+<([^>]+)>\s+<([^>]+)>\s*\.\s*$", line)
        if m:
            triples.append((m.group(1), m.group(2), m.group(3)))
            continue
        m2 = re.match(r"<([^>]+)>\s+<([^>]+)>\s+\"(.*)\"(?:\^\^<[^>]+>)?\s*\.\s*$", line)
        if m2:
            triples.append((m2.group(1), m2.group(2), m2.group(3)))
    return triples
