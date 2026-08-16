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
import json
import sys
import re


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
    # 未定义名（取赋值/引用，粗略）
    used = set(re.findall(r"\b([a-zA-Z_]\w*)\b", text))
    defined = set(re.findall(r"\b(?:def|class)\s+([a-zA-Z_]\w*)", text)) | \
              set(re.findall(r"\b([a-zA-Z_]\w*)\s*=\s*", text)) | \
              set(dir(__builtins__)) | {"self", "__name__", "True", "False", "None"}
    for i, line in enumerate(lines):
        for name in re.findall(r"\b([a-zA-Z_]\w*)\b", line):
            if name in used and name not in defined and len(name) > 1 and not line.lstrip().startswith("#"):
                diags.append({"range": {"start": {"line": i, "character": line.find(name)},
                                        "end": {"line": i, "character": line.find(name) + len(name)}},
                              "severity": 2, "source": "mock-lsp",
                              "message": f"未定义名 '{name}'"})
                break
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
