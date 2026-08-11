---
title: "代码原子：lasio 读取 LAS 测井曲线"
tags: "[code-atom, 地球物理, python, 极简]"
created: 2026-08-11
type: code-atom
domain: 地球物理
source: lasio (https://github.com/kinverarity1/lasio)
source_license: MIT
status: draft
---

# 代码原子：lasio 读取 LAS 测井曲线

> 从开源项目 `lasio`（并被 `welly` 作为底层引擎）提炼的最小可复用代码片段。遵循极简代码原则（Ponytail 阶梯）：标准库优先、一行能搞定就一行、只写最少必要代码。

## 解决的问题
读取行业标准 LAS（Log ASCII Standard）测井文件，把按深度采样的多条测井曲线（GR、SP、电阻率等）一次性取成表格化数据，供后续计算/绘图。

## 极简代码（核心）

```python
import lasio

las = lasio.read("well.las")       # 解析 LAS 1.2 / 2.0 / 3.0
df = las.df()                      # pandas.DataFrame，索引=深度，列为各曲线
GR = df["GR"].values               # 取自然伽马曲线为 numpy 数组
depth = df.index.to_numpy()        # 对应的深度坐标
```

## 使用要点
- 这是 `welly.Well.from_las()` 的底层：welly 在其 `welly/las.py` 中封装 lasio 解析曲线与井头元数据。若只需曲线数值，直接 lasio 最简，无需引入 welly 对象体系。
- `las.df()` 默认把第一条曲线当深度索引列（常见是 `DEPT`），可直接得到以深度为行索引的表；用 `las.df(keys=['GR','SP'])` 可选列。
- 曲线缺失或为 NULL 值时，lasio 会按文件里的 null value 填 NaN，`numpy` 运算前记得过滤 NaN；编码异常可用 `lasio.read(path, encoding='cp1252')` 兜底。

## 来源
- 项目：lasio（https://github.com/kinverarity1/lasio）
- 许可：MIT
- 提炼自：`welly` 库 `welly/las.py` 的 `from_las()`（其注释明确依赖 lasio 完成解析）。

## 何时用 / 何时别用
- ✅ 用：把 LAS 测井文件读成曲线数组/DataFrame，做深度对齐、交会图、储层解释前的数值预处理。
- ❌ 别用：需要井名、井位、采样间距等完整井对象与坐标/合成记录功能时，用 `welly.Well.from_las('well.las')` 拿到完整 Well 对象更合适；只改几个头部字段做格式转换时，也可考虑 `welly` 的高级封装。
