# solo-atoms — 领域原子代码库

> 为代码生成准备的可复用**极简原子**。零依赖、标准库、单职责。
> CodeAgent 生成代码时通过 `DOMAIN_LIBS` 自动路由复用，避免从零重写。

## 领域覆盖（对应主业）

| 领域 | 原子模块 | 覆盖能力 |
|------|---------|---------|
| **通用基础** | `storage.py` | 原子写/JSON读/并发锁（持久化） |
| **通用基础** | `errors.py` | 统一错误契约（ApiError/DataSourceError） |
| **通用基础** | `data.py` | 数值判断/列类型/流式CSV读 |
| **通用基础** | `stats.py` | 描述统计/趋势/异常检测/SPC控制图/相关 |
| **通用基础** | `clean.py` | 类型推断/去重/缺失处理/异常剔除 |
| **本体工厂** | `ontology.py` | CSV转OWL/N-Triples解析/类型推断/词典 |
| **地球物理** | `geophys.py` | LAS解析/深度-时间/曲线归一化/平滑/极值 |

## 设计原则（代码极简）

- **标准库零依赖**：仅 import stdlib（csv/json/math/re/os），可带到任何现场
- **单职责**：每函数做一件事，可独立 import
- **可审计**：小到能装进脑袋，几分钟追溯根因
- **不造第三方轮子**：大型处理用 domain-libs 的 obspy/welly/networkx 等已路由库；此处只沉淀第三方缺失的轻量原子

## 用法（CodeAgent 自动复用）

```python
import sys; sys.path.insert(0, os.environ.get("CODEAGENT_DOMAIN_LIBS_DIR", ""))  # 或本仓库 vendor/solo-atoms 实际路径
from stats import describe, detect_anomaly
from clean import clean, load_csv
from ontology import csv_to_nt, local_name
from storage import atomic_write, json_load
from geophys import las_parse, curve_normalize
```

CodeAgent `implement()` 任务含对应关键词（数据分析/数据清洗/本体建模/测井/曲线）时，自动路由到本库注入复用。

## 路由映射（DOMAIN_LIBS）

见仓库 `code_agent_engine.py` 的 `DOMAIN_LIBS`，本库关键词：
- 通用：`原子` `原子函数` `数据分析` `数据清洗` `统计` `异常检测` `spc` `控制图` `持久化` `存储` `json` `错误契约`
- 本体：`本体` `ontology` `owl` `ntriples` `csv转owl` `词典`
- 地球物理：`测井` `las` `曲线` `深度` `地震` `geophys` `归一化`

## 验证

每模块 `python -c "import 模块"` + 功能冒烟（见本目录验证记录）。
