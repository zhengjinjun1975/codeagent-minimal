---
title: "代码原子：segyio 读取 SEG-Y 地震道"
tags: "[code-atom, 地球物理, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 地球物理
source: segyio (https://github.com/equinor/segyio)
source_license: LGPL-3.0
status: draft
---

# 代码原子：segyio 读取 SEG-Y 地震道

> 从开源项目 `segyio` 提炼的最小可复用代码片段。遵循极简代码原则（Ponytail 阶梯）：标准库优先、一行能搞定就一行、只写最少必要代码。

## 解决的问题
流式读取 SEG-Y 格式地震数据文件，逐道取出振幅采样与道头关键字段（inline/crossline 编号），无需一次性载入整个大文件。

## 极简代码（核心）

```python
import segyio

with segyio.open("data.sgy") as f:
    ns = len(f.samples)          # 每道采样点数
    for i, trace in enumerate(f.trace):   # 逐道流式读取
        amp = trace.copy()       # numpy.ndarray，长度 ns；须 copy 才保留
        il = f.header[i][segyio.TraceField.INLINE_3D]
        xl = f.header[i][segyio.TraceField.CROSSLINE_3D]
        # 此处对 amp 做处理（增益、滤波、写盘…）
```

## 使用要点
- 用 `with segyio.open(...)` 管理句柄，退出自动关闭，适合大文件流式处理（不整读进内存）。
- `f.trace[i]` 返回的是**复用缓冲区的视图**，跨迭代不会保留；想长期持有数据必须 `.copy()`（见上方代码），这是 segyio 新手最常见的坑。
- 结构化文件可用 `f.iline[i+1]` 按 inline 取整条线；`f.tracecount` 给出总道数。无几何信息的文件会以 unstructured 模式打开，此时 `f.header[i]` 不可用。

## 来源
- 项目：segyio（https://github.com/equinor/segyio）
- 许可：LGPL-3.0（License.md）
- 提炼自：`python/segyio/open.py` 的 `open()` 函数；`README.md` 的 Quick start 示例；`python/examples/make-file.py` 的读写用法。

## 何时用 / 何时别用
- ✅ 用：处理单文件、但单文件可能很大的 3D/2D 地震数据；需要按道做批量处理、逐道写盘或抽样。
- ❌ 别用：数据已是 numpy array / 已解构到内存（直接用 numpy 即可）；需要频繁随机访问大量道且要保留缓存——此时改用 `f.trace[start:stop:step]` 切片并手动 `.copy()`。
