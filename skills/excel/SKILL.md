---
name: excel
description: "处理 Excel 文件(.xlsx/.xls)。用于读取、合并、筛选、统计 Excel 数据, 生成新的 Excel 文件, 或在多个 sheet 之间合并数据。涉及关键词: Excel、表格、xlsx、sheet、单元格、数据透视。"
---

# Excel 处理 Skill

## 何时使用本 skill
- 用户提供了 .xlsx 或 .xls 文件, 要做读取、转换、合并
- 需要把多个 sheet 合并成一个
- 需要从多个 Excel 文件提取数据生成新表

## 核心约定
- **始终使用 `openpyxl` 库**(已预装), 不要用 pandas 之外的其他库
- 读 Excel 用 `openpyxl.load_workbook(path)`
- 写 Excel 时, 文件路径默认放到 `/tmp/` 下, 文件名以 `output_` 开头

## 现成脚本
本 skill 目录下有 `scripts/merge_sheets.py`, 功能: 把一个 Excel 文件的所有 sheet 合并成一个。
用法: `python <skill 目录>/scripts/merge_sheets.py <输入.xlsx> <输出.xlsx>`

合并多个 sheet 的需求, **优先用这个脚本**, 不要自己重写逻辑。

## 常见操作示例

### 读取单元格
```python
from openpyxl import load_workbook
wb = load_workbook("input.xlsx")
ws = wb.active
value = ws["A1"].value
```

### 遍历所有行
```python
for row in ws.iter_rows(min_row=2, values_only=True):
    print(row)
```

## 注意事项
- Excel 行列从 1 开始, 不是 0
- 处理完大文件后记得 `wb.close()`
- 中文表头不要硬编码, 从第一行动态读
