"""上下文腐烂对策核心：工具输出头尾截断 + 全文落盘 + 技能渐进披露。

token 估算为启发式：tokens = max(1, len(text) // 4)。
所有函数返回普通 dict，不抛异常。
"""
import json
import os
import re
import time
import hashlib
from collections import OrderedDict


def _tokens(text):
    return max(1, len(text) // 4)


def _default_store_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_offload")


# ── 卸载目录的保留上限（T4：offload 不能只增不减）─────────────────────────
# 上限按**文件数**算，外加一个字节总帽。ponytail: 这是最省事的双帽；升级路径是
# 改成"按引用保活"（句柄被条目引用着就不删），需要额外记引用关系，眼下不值当。
_STORE_KEEP = 200
_STORE_MAX_BYTES = 32 * 1024 * 1024
_PRUNE_LEDGER = "_pruned.log"
_OFFLOAD_NAME_RE = re.compile(r"^.+_\d+_[0-9a-f]{8}\.txt$")


def _is_offload_file(name):
    """只认本模块写出来的卸载文件（<label>_<ts>_<digest>.txt）——别动目录里的别的东西。"""
    return bool(_OFFLOAD_NAME_RE.match(name or ""))


def prune_store(store_dir=None, keep=None, max_bytes=None):
    """把卸载目录压回上限内：按 mtime **从旧到新**删，删了哪些必须报出来（绝不静默）。

    为什么要有：offload 只写不删 → 目录无界涨（六面审计的 context 面缺口）。
    安全边界：① 只删认得名的卸载文件，目录里其它文件一律不碰；② 删掉的记进 `_pruned.log`
    台账，之后 reveal 那个句柄会**明确说"已被淘汰"**，不会静默给错内容。
    """
    # 上限在这里解析而不是写在默认参数里：默认参数是**定义时求值**，改了常量不会生效
    # （既改不动上限，也测不了边界）。见 codeagent-maintenance 的同类坑。
    keep = _STORE_KEEP if keep is None else int(keep)
    max_bytes = _STORE_MAX_BYTES if max_bytes is None else int(max_bytes)
    store_dir = os.path.abspath(store_dir or _default_store_dir())
    out = {"pruned": [], "kept": 0, "bytes": 0, "errors": []}
    try:
        names = [n for n in os.listdir(store_dir) if _is_offload_file(n)]
    except OSError as e:
        out["errors"].append("列目录失败: %s" % e)
        return out
    entries = []
    for n in names:
        fp = os.path.join(store_dir, n)
        try:
            st = os.stat(fp)
        except OSError:
            continue
        entries.append((st.st_mtime, n, fp, st.st_size))
    entries.sort(key=lambda t: (t[0], t[1]))              # 最旧在前
    total = sum(e[3] for e in entries)
    over_count = max(0, len(entries) - int(keep))
    victims = list(entries[:over_count])
    rest = entries[over_count:]
    total = sum(e[3] for e in rest)
    for st_m, n, fp, size in reversed(rest):              # 从剩下的里再挑最旧的删到字节帽内
        if total <= int(max_bytes):
            break
        victims.append((st_m, n, fp, size))
        total -= size
    kept_names = {e[1] for e in entries} - {v[1] for v in victims}
    for _st_m, n, fp, _size in victims:
        try:
            os.remove(fp)
            out["pruned"].append(n)
            _ledger_write(store_dir, n, "超保留上限(keep=%d, max_bytes=%d)" % (keep, max_bytes))
        except OSError as e:
            out["errors"].append("删不掉 %s: %s" % (n, e))
    out["kept"] = len(kept_names)
    out["bytes"] = int(total)
    return out


def _ledger_write(store_dir, name, why):
    """淘汰台账：一行一条（名字 + 时间 + 原因），供 reveal 解释"为什么取不到了"。"""
    try:
        with open(os.path.join(store_dir, _PRUNE_LEDGER), "a", encoding="utf-8") as f:
            f.write("%d\t%s\t%s\n" % (int(time.time()), name, why))
    except Exception:
        pass


def prune_ledger(store_dir=None, limit=50):
    """读淘汰台账（最近 limit 条，新的在前）。"""
    store_dir = os.path.abspath(store_dir or _default_store_dir())
    try:
        with open(os.path.join(store_dir, _PRUNE_LEDGER), encoding="utf-8") as f:
            rows = [ln.rstrip("\n") for ln in f if ln.strip()]
    except OSError:
        return []
    out = []
    for ln in reversed(rows[-500:]):
        parts = ln.split("\t")
        if len(parts) >= 3:
            out.append({"ts": parts[0], "name": parts[1], "why": parts[2]})
        if len(out) >= limit:
            break
    return out


def context_offload(content, label="tool_output", keep_head=800, keep_tail=400, store_dir=None):
    """头尾截断 + 全文落盘。短内容原样内联。"""
    try:
        if not isinstance(content, str):
            content = str(content)
        original_tokens = _tokens(content)
        if len(content) <= keep_head + keep_tail:
            return {
                "inlined": content,
                "offloaded_path": "",
                "original_tokens": original_tokens,
                "inlined_tokens": original_tokens,
            }
        if store_dir is None:
            store_dir = _default_store_dir()
        os.makedirs(store_dir, exist_ok=True)
        ts = int(time.time() * 1000)
        digest = hashlib.md5(content.encode("utf-8", "replace")).hexdigest()[:8]
        fname = "%s_%d_%s.txt" % (label, ts, digest)
        path = os.path.join(store_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        omitted = len(content) - keep_head - keep_tail
        head = content[:keep_head]
        tail = content[-keep_tail:] if keep_tail > 0 else ""
        marker = "\n...[已省略 %d 字符，全文见 %s]...\n" % (omitted, path)
        inlined = head + marker + tail
        pruned = prune_store(store_dir)
        return {
            "inlined": inlined,
            "offloaded_path": path,
            "original_tokens": original_tokens,
            "inlined_tokens": _tokens(inlined),
            "pruned": pruned.get("pruned", []),
            "store_kept": pruned.get("kept", 0),
        }
    except Exception:
        text = content if isinstance(content, str) else str(content)
        return {
            "inlined": text,
            "offloaded_path": "",
            "original_tokens": _tokens(text),
            "inlined_tokens": _tokens(text),
        }


def _task_terms(task):
    """中文 2-gram + 英文 \\w+ 小写。"""
    terms = set()
    if not isinstance(task, str):
        task = str(task)
    low = task.lower()
    for m in re.findall(r"[a-z0-9_]+", low):
        if m:
            terms.add(m)
    cjk = re.findall(r"[\u4e00-\u9fff]+", task)
    for seg in cjk:
        if len(seg) == 1:
            terms.add(seg)
        else:
            for i in range(len(seg) - 1):
                terms.add(seg[i:i + 2])
    return terms


def _normalize_registry(registry_desc):
    """统一成 [(name, desc), ...]。"""
    items = []
    if isinstance(registry_desc, dict):
        for k, v in registry_desc.items():
            items.append((str(k), "" if v is None else str(v)))
    elif isinstance(registry_desc, (list, tuple)):
        for entry in registry_desc:
            if isinstance(entry, dict):
                name = entry.get("name", "")
                desc = entry.get("desc", entry.get("description", ""))
                items.append((str(name), "" if desc is None else str(desc)))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                items.append((str(entry[0]), str(entry[1])))
    return items


def disclose(registry_desc, task, top_k=5):
    """渐进披露：只放最相关的 top_k 条目。"""
    try:
        items = _normalize_registry(registry_desc)
        total = len(items)
        if top_k <= 0 or total == 0:
            return {"loaded": [], "deferred_count": total}
        terms = _task_terms(task)
        scored = []
        for name, desc in items:
            hay = (name + " " + desc).lower()
            score = 0
            for t in terms:
                if t and t in hay:
                    score += 1
            scored.append((score, name, desc))
        hits = [s for s in scored if s[0] > 0]
        miss = [s for s in scored if s[0] == 0]
        hits.sort(key=lambda x: (-x[0], x[1]))
        miss.sort(key=lambda x: x[1])
        ordered = hits + miss
        k = min(top_k, total)
        loaded = [{"name": n, "desc": d} for _, n, d in ordered[:k]]
        return {"loaded": loaded, "deferred_count": total - k}
    except Exception:
        try:
            total = len(_normalize_registry(registry_desc))
        except Exception:
            total = 0
        return {"loaded": [], "deferred_count": total}


# ── 上下文编辑工具化（P1-5，依据 arXiv 2607.23809）────────────────────────
# 论文要点：别让"压缩"只由阈值触发——把编辑权交给 agent（何时压、压什么），
# 且**丢掉的内容必须无损落到外部存储、随时能取回**（短/长期记忆的分工）。
# 这一节就是那套工具的核心，全部纯函数、无模型调用、确定性。

def item_key(x):
    """条目身份键：优先 capability/label/step，其次截断字符串。"""
    if isinstance(x, dict):
        return str(x.get("capability") or x.get("label") or x.get("step") or "")[:60]
    return str(x)[:60]


def handle_of(x):
    """可恢复句柄：有落盘路径 → ("file", path)；有 capability → ("capability", name)。"""
    if isinstance(x, dict):
        p = x.get("offloaded_path") or x.get("path")
        if p:
            return "file", str(p)
        c = x.get("_capability")
        if c:
            return "capability", str(c)
    return None, None



_HEAVY_THRESHOLD_EXTRA = 200        # 比"头+尾"再宽一点才算重字段，避免把短元数据也搬走


def _shrink_item(cur, label, store_dir, keep_head, keep_tail, stored):
    """把一条里的重字段落盘，留下小元数据 + 最大那块的"头尾窗口"。

    返回 (新条目, 主句柄, 主窗口文本)。字符串条目会被包成 dict（否则句柄无处可放 = 取不回来）。
    """
    limit = keep_head + keep_tail + _HEAVY_THRESHOLD_EXTRA

    def heavy(v):
        if isinstance(v, str):
            return len(v) > limit
        return isinstance(v, (list, dict)) and len(json.dumps(v, ensure_ascii=False)) > limit

    def dump(v, name):
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        r = context_offload(text, label=name, keep_head=keep_head, keep_tail=keep_tail,
                            store_dir=store_dir)
        if r.get("offloaded_path"):
            stored.append(r["offloaded_path"])
            return "offloaded_path", r["offloaded_path"], r.get("inlined", text[:limit])
        return None, "", text                                    # 没落盘（短内容）→ 原样

    meta, biggest = {}, (0, None, "", "")
    src = cur if isinstance(cur, dict) else {"label": label, "value": cur}
    for k, v in src.items():
        if heavy(v):
            _kind, path, inlined = dump(v, "%s_%s" % (label, str(k)[:24]))
            n = len(inlined)
            if n > biggest[0]:
                biggest = (n, k, path, inlined)
            if path:
                meta[k] = {"_offloaded": True, "offloaded_path": path,
                           "head": inlined[:keep_head]}
            else:
                meta[k] = inlined
        else:
            meta[k] = v
    if biggest[1] is not None:
        meta[biggest[1]] = {"_offloaded": True, "offloaded_path": biggest[2],
                            "inlined": biggest[3]}
    return meta, biggest[2] or "", biggest[3] or ""

def context_edits(items, edits, store_dir=None, keep_head=200, keep_tail=120):
    """按声明式编辑表改一份上下文（工具输出/步骤）列表。**agent 自己决定何时压、压什么**。

    edits 是 [{op, key, ...}]：
      drop     丢掉该条（被 pin 的不丢，除非 force=True）
      collapse 压成一行（to 给替换文本，不给则用确定性占位：原 token 数）
      offload  全文落盘，条目换成"头尾 + 句柄"（**无损**：随时 store_reveal 取回）
      pin      标记不许丢（写入 _pinned=True）
      unpin    解除标记
    命中不了 key、op 不认、条目被 pin —— **一律记 skipped 带原因**，不静默吞。
    返回 {items, tokens_before, tokens_after, saved, applied, skipped, handles, stored}。
    """
    items = list(items or [])
    edits = list(edits or [])
    tokens_before = sum(_tokens(str(x)) for x in items)
    by_key = {}
    for i, x in enumerate(items):
        by_key.setdefault(item_key(x), i)

    applied, skipped, handles, stored = [], [], [], []
    for e in edits:
        if not isinstance(e, dict):
            skipped.append({"op": None, "key": "", "reason": "编辑项不是对象"})
            continue
        op = str(e.get("op") or "")
        key = str(e.get("key") or "")
        idx = by_key.get(key)
        if idx is None:
            skipped.append({"op": op, "key": key, "reason": "没有这个键的条目（键取 capability/label/step 或前 60 字）"})
            continue
        cur = items[idx]
        if isinstance(cur, dict) and cur.get("_pinned") and op in ("drop", "collapse", "offload") \
                and not e.get("force"):
            skipped.append({"op": op, "key": key, "reason": "条目已 pin（要改就显式 force=True）"})
            continue

        if op == "pin":
            nxt = dict(cur) if isinstance(cur, dict) else {"value": cur}
            nxt["_pinned"] = True
            items[idx] = nxt
        elif op == "unpin":
            if isinstance(cur, dict):
                nxt = dict(cur)
                nxt.pop("_pinned", None)
                items[idx] = nxt
        elif op == "drop":
            items[idx] = {"_dropped_by_edit": True, "key": key,
                          "_was_tokens": _tokens(str(cur)), "reason": str(e.get("reason") or "")}
        elif op == "collapse":
            to = e.get("to")
            if to is None:
                to = "[已压缩 %s：原 %d tokens]" % (key or "条目", _tokens(str(cur)))
            items[idx] = to
        elif op == "offload":
            # 只把"重字段"落盘，条目留下小元数据 + 头尾窗口——否则原大字段还留着，等于没省
            label = (key or "item").replace("/", "_")[:40]
            nxt, one_path, one_inlined = _shrink_item(cur, label, store_dir, keep_head, keep_tail,
                                                      stored)
            nxt["offloaded_path"] = one_path
            nxt["_offloaded"] = True
            nxt["_inlined"] = one_inlined
            items[idx] = nxt
        else:
            skipped.append({"op": op, "key": key, "reason": "不认的 op（drop/collapse/offload/pin/unpin）"})
            continue
        applied.append({"op": op, "key": key})
        if op == "offload":
            h = handle_of(items[idx])[1]
            if h:
                handles.append(h)

    tokens_after = sum(_tokens(str(x)) for x in items)
    return {"items": items, "tokens_before": tokens_before, "tokens_after": tokens_after,
            "saved": max(0, tokens_before - tokens_after), "applied": applied,
            "skipped": skipped, "handles": handles, "stored": stored}


def store_list(store_dir=None):
    """列出已卸载产物（句柄 / 字节 / 时间），供 agent 决定取回哪一份。"""
    store_dir = store_dir or _default_store_dir()
    out = []
    if not os.path.isdir(store_dir):
        return {"store_dir": store_dir, "count": 0, "items": []}
    for name in sorted(os.listdir(store_dir)):
        p = os.path.join(store_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"handle": name, "path": p, "bytes": st.st_size,
                    "tokens": _tokens("x" * st.st_size) if st.st_size else 1,
                    "mtime": int(st.st_mtime)})
    return {"store_dir": store_dir, "count": len(out), "items": out}


def _resolve_handle(handle, store_dir):
    """把句柄解析成 store 内的真实路径。**只允许 store 内**——否则这里就成了任意文件读。"""
    store_dir = os.path.abspath(store_dir or _default_store_dir())
    if not handle:
        return None, "缺 handle"
    raw = str(handle)
    cand = raw if os.path.isabs(raw) else os.path.join(store_dir, raw)
    cand = os.path.abspath(cand)
    try:
        inside = os.path.commonpath([store_dir, cand]) == store_dir
    except ValueError:                                    # 不同盘符
        inside = False
    if not inside:
        return None, "句柄越出卸载目录（只许取 store 内的产物）"
    if not os.path.isfile(cand):
        base = os.path.basename(cand)
        if any(r.get("name") == base for r in prune_ledger(store_dir)):
            return None, "这份产物已被淘汰（超过保留上限，见 _pruned.log）；内容不可恢复，重跑或改用小窗口卸载"
        return None, "没有这份产物（先看 store_list）"
    return cand, ""


def store_reveal(handle, offset=0, limit=4000, store_dir=None):
    """取回卸载产物的一段（默认 4000 字符窗），带 next_offset/done 供翻页。"""
    path, why = _resolve_handle(handle, store_dir)
    if path is None:
        return {"ok": False, "error": why, "text": "", "offset": 0, "total_chars": 0,
                "next_offset": None, "done": True, "handle": str(handle or "")}
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 4000
    limit = max(1, min(limit, 200000))                     # 防一次吐爆上下文
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        return {"ok": False, "error": "读不了产物: %s" % e, "text": "", "offset": offset,
                "total_chars": 0, "next_offset": None, "done": True, "handle": path}
    window = text[offset:offset + limit]
    nxt = offset + len(window)
    done = nxt >= len(text)
    return {"ok": True, "error": "", "text": window, "offset": offset,
            "total_chars": len(text), "next_offset": None if done else nxt,
            "done": done, "handle": path}
