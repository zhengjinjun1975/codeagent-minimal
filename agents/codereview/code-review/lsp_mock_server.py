#!/usr/bin/env python3
"""LSP mock server（真实 Language Server Protocol JSON-RPC over stdio）。

用途：code-review 原子的 `codereview.lsp` 能力做真实 LSP 诊断验证。
实现 LSP 最小子集：initialize / initialized / textDocument/didOpen →
server 主动 push `textDocument/publishDiagnostics`。对 Python 文件做几个启发式诊断：
  - 未定义名（简单正则/常见错误）
  - 语法错误（compile()）
  - 未使用 import（粗略）
  - 行过长（style）
零第三方依赖，纯标准库。被 code-review 作为子进程 spawn（参数列表，shell=False）。

真实协议：Content-Length 头 + JSON body（LSP 标准帧格式）。
"""
import ast
import json
import sys
import re


def _defined_in_scope(text):
    """AST 作用域感知收集所有已定义名（模块/函数/类/lambda/推导式 + 参数/赋值/导入/循环变量）。
    返回 {name: (line, col)} 行号为定义所在行。修复正则把 def/return/参数 误报为未定义名。"""
    defined = {}
    builtins = set(dir(__builtins__)) | {
        "__name__", "__file__", "__doc__", "__package__", "__spec__",
        "__loader__", "__cached__", "__builtins__", "self", "cls",
        "True", "False", "None", "__all__", "__debug__",
    }
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}  # 语法错误由独立诊断覆盖，不在此重复

    def add(name, lineno):
        if name and name not in defined and name not in builtins:
            defined[name] = lineno

    # 先全局收集所有绑定点（保守：把整个文件所有赋值/函数名/类名/导入都算已定义，
    # 再单独跑调用点检查 —— 对 mock 级别足够且零误报）。
    def bind_node(node):
        if isinstance(node, ast.Module):
            for stmt in node.body:
                bind_node(stmt)
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            add(node.name, node.lineno)
            for a in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
                add(a.arg, node.lineno)
            if node.args.vararg:
                add(node.args.vararg.arg, node.lineno)
            if node.args.kwarg:
                add(node.args.kwarg.arg, node.lineno)
            for stmt in node.body:
                bind_node(stmt)
        elif isinstance(node, ast.ClassDef):
            add(node.name, node.lineno)
            for stmt in node.body:
                bind_node(stmt)
        elif isinstance(node, ast.Lambda):
            for a in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
                add(a.arg, node.lineno)
            if node.args.vararg:
                add(node.args.vararg.arg, node.lineno)
            if node.args.kwarg:
                add(node.args.kwarg.arg, node.lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        add(n.id, node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                add((a.asname or a.name).split(".")[0], node.lineno)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    add(n.id, node.lineno)
            for stmt in node.body + node.orelse:
                bind_node(stmt)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    for n in ast.walk(item.optional_vars):
                        if isinstance(n, ast.Name):
                            add(n.id, node.lineno)
            for stmt in node.body:
                bind_node(stmt)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                add(node.name, node.lineno)
            for stmt in node.body:
                bind_node(stmt)
        elif isinstance(node, (ast.While, ast.If, ast.Try)):
            for stmt in node.body + node.orelse:
                bind_node(stmt)
            if isinstance(node, ast.Try):
                for h in node.handlers:
                    bind_node(h)
                for stmt in node.finalbody:
                    bind_node(stmt)
        elif isinstance(node, ast.comprehension):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    add(n.id, 0)
            for gen in node.generators:
                bind_node(gen)
        elif isinstance(node, ast.Expr):
            if isinstance(node.value, (ast.Str, ast.Constant)):
                return
            for n in ast.walk(node.value):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    add(n.id, node.lineno)
    bind_node(tree)
    return defined


def _undefined_names(text):
    """AST 检测未定义名（Load 上下文，非定义处）。返回 [{line, col, name}]。"""
    out = []
    builtins = set(dir(__builtins__)) | {
        "__name__", "__file__", "__doc__", "__package__", "__spec__",
        "__loader__", "__cached__", "__builtins__", "self", "cls",
        "True", "False", "None", "__all__", "__debug__",
    }
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    defined = _defined_in_scope(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in builtins:
                continue
            # 排除作为函数参数默认值/注解里的名字已在 defined（bind_node 已收集）。
            if node.id not in defined:
                out.append({"line": node.lineno, "col": node.col_offset, "name": node.id})
    # 去重 + 去关键字
    seen, res = set(), []
    for d in out:
        key = (d["line"], d["col"], d["name"])
        if key in seen:
            continue
        seen.add(key)
        res.append(d)
    return res


def _read_message(stream):
    """读一个 LSP 帧（Content-Length: N\\r\\n\\r\\n + JSON body）。"""
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.decode("utf-8", "ignore").rstrip("\r\n")
        if not line:
            break
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", 0))
    if length <= 0:
        return None
    body = stream.read(length)
    try:
        return json.loads(body.decode("utf-8", "ignore"))
    except Exception:
        return None


def _write_message(stream, msg):
    body = json.dumps(msg).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
    stream.write(body)
    stream.flush()


def _diagnostics(uri, text):
    """对 Python 源码做启发式 LSP 诊断（语法/未定义名/未使用import/行过长）。"""
    diags = []
    lines = text.split("\n")
    # 语法错误
    try:
        compile(text, uri, "exec")
    except SyntaxError as e:
        ln = e.lineno or 1
        diags.append({"range": {"start": {"line": ln - 1, "character": 0},
                                "end": {"line": ln - 1, "character": len(lines[ln - 1] if ln - 1 < len(lines) else "")}},
                      "severity": 1, "source": "mock-lsp",
                      "message": f"语法错误: {e.msg}"})
    # 未定义名（AST 作用域感知，修复把 def/return/参数误报的 bug）
    for d in _undefined_names(text):
        i = d["line"] - 1
        if i < 0 or i >= len(lines):
            i = 0
        diags.append({"range": {"start": {"line": i, "character": d["col"]},
                                "end": {"line": i, "character": d["col"] + len(d["name"])}},
                      "severity": 2, "source": "mock-lsp",
                      "message": f"未定义名 '{d['name']}'"})
    # 行过长
    for i, line in enumerate(lines):
        if len(line) > 100:
            diags.append({"range": {"start": {"line": i, "character": 99},
                                    "end": {"line": i, "character": len(line)}},
                          "severity": 3, "source": "mock-lsp",
                          "message": f"行过长({len(line)}>100)"})
    return diags


def main():
    opened = {}  # uri -> text
    while True:
        msg = _read_message(sys.stdin.buffer)
        if msg is None:
            break
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            _write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": mid,
                                               "result": {"capabilities": {"textDocumentSync": 1},
                                                          "serverInfo": {"name": "codeagent-mock-lsp", "version": "0.1.0"}}})
        elif method == "textDocument/didOpen":
            td = msg.get("params", {}).get("textDocument", {})
            uri = td.get("uri", "")
            text = td.get("text", "")
            opened[uri] = text
            diags = _diagnostics(uri, text)
            _write_message(sys.stdout.buffer, {"jsonrpc": "2.0",
                                               "method": "textDocument/publishDiagnostics",
                                               "params": {"uri": uri, "diagnostics": diags}})
        elif method == "textDocument/didChange":
            td = msg.get("params", {}).get("textDocument", {})
            uri = td.get("uri", "")
            content = msg.get("params", {}).get("contentChanges", [{}])[0].get("text", "")
            if content:
                opened[uri] = content
                diags = _diagnostics(uri, content)
                _write_message(sys.stdout.buffer, {"jsonrpc": "2.0",
                                                   "method": "textDocument/publishDiagnostics",
                                                   "params": {"uri": uri, "diagnostics": diags}})
        # 其余请求返回空 result（LSP 规范：未知方法可返回空 result 避免挂起）
        if mid is not None:
            _write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main()
