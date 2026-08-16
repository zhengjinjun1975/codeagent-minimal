# code-reuse 原子（open_source:true）

**复用（零改动核心）**：`review.py._list_code_atoms / _reuse_suggestion / _remote_reuse_suggestion` + `atoms/` 19个复用库。

**能力**：
- `reuse.local` — 本地代码复用检索（Obsidian atoms 命中 → GitHub 远端降级）
- `reuse.atom` — 列出 `atoms/` 19个复用库
- `reuse.remote` — GitHub 远端开源检索

**数据不出厂**：本地优先；远端检索仅在本地未命中时降级，全程静默。
