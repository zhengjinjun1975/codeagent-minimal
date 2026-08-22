# ontology-review 原子

本体审查原子（`open_source:true`，P0+P2）：factory-ontology 链路断链审查（Web↔套件↔CSV↔NT）+ 本体数据质量（CSV/NT/lexicon）。纯 stdlib 数据不出厂。

## 能力

| 能力 | 说明 |
|---|---|
| `ontology.chain` | 链路断链审查：Web↔套件↔CSV↔NT 逐环 |
| `ontology.quality` | 本体数据质量审查（CSV/NT/lexicon） |

## 入参

- `web_dir` / `kit_dir` / `data_dir`：链路各环目录（本地路径参数化，不入厂）
- `target`：目标文件或目录

## 独立自测

```bash
python agents/ontology/ontology-review/main.py --capability ontology.chain --web_dir <dir> --kit_dir <dir> --data_dir <dir>
python agents/ontology/ontology-review/main.py --capability ontology.quality --target <file.csv>
```

## 依赖

- 无（纯 stdlib；本地路径已参数化，不硬编码本机路径）
