# 查询语法说明

## 1. 查询参数

专利检索接口使用 `q` 表达查询条件。

```http
POST /api/patent/search
```

请求示例：

```json
{
  "q": "ipc:H02M AND tscd:(\"均衡\" OR \"平衡\")",
  "ds": "cn",
  "sort": "relation",
  "page": 1,
  "page_size": 50,
  "highlight": 0
}
```

## 2. 第一阶段最小支持语法

阶段五先支持以下语法，用于跑通最小检索链路：

| 查询能力 | 示例 | OpenSearch 字段 |
|---|---|---|
| 普通关键词 | `阀门` | `Title`, `Abstract` |
| 标题检索 | `title:(阀门)` | `Title`, `TitleCN`, `TitleEN` |
| 摘要检索 | `ab:(缓冲)` | `Abstract`, `AbstractCN`, `AbstractEN` |
| IPC 检索 | `ipc:H02M` | `IPC`, `IPCList`, `IPCSmallCategory` |
| 申请日范围 | `ad:[2020-01-01 TO 2020-12-31]` | `ApplicationDate` |

## 3. 第一版完整支持语法

阶段六补齐以下语法。本范围已作为第一版查询语法冻结范围，开发不得在第一版中擅自扩展复杂查询语法。

| 查询能力 | 示例 | 第一版要求 |
|---|---|---|
| 全文检索 | `tscd:(均衡)` | 支持 |
| 全文 OR 检索 | `tscd:(\"均衡\" OR \"平衡\")` | 支持 |
| IPC + 全文 | `ipc:H02M AND tscd:(均衡)` | 支持 |
| 申请人 | `applicant:(华为技术有限公司)` | 支持 |
| 当前权利人 | `currentAssignee:(华为技术有限公司)` | 支持 |
| 法律状态 | `legalStatus:(有效专利)` | 支持基础映射 |
| 专利类型 | `type:(发明专利)` | 支持 |
| 公开年 | `documentYear:[2020 TO 2024]` | 支持 |
| 简单 AND | `ipc:H02M AND tscd:(均衡)` | 支持 |
| 简单 OR | `title:(均衡) OR title:(平衡)` | 支持 |
| 基础括号分组 | `(title:(均衡) OR title:(平衡)) AND ipc:H02M` | 支持 |

## 4. 字段语法

### 4.1 标题

```text
title:(阀门)
title:("电液比例阀")
```

### 4.2 摘要

```text
ab:(缓冲)
ab:("压力控制")
```

### 4.3 全文

`tscd` 检索范围：

```text
Title
Abstract
MainClaim
Requirement
Instructions
```

示例：

```text
tscd:(均衡)
tscd:("均衡" OR "平衡")
```

### 4.4 IPC

```text
ipc:H02M
ipc:H02M7/483
ipc:F16K
```

第一版 IPC 查询应覆盖：

```text
IPC
IPCList
IPCSmallCategory
IPCLargeGroup
IPCSmallGroup
```

### 4.5 申请人和当前权利人

```text
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
currentAssignee:(华为技术有限公司) OR currentAssignee:(杭州华为数字技术有限公司)
```

### 4.6 日期范围

申请日：

```text
ad:[2020-01-01 TO 2020-12-31]
```

公开年：

```text
documentYear:[2020 TO 2024]
```

### 4.7 法律状态

```text
legalStatus:(有效专利)
legalStatus:(在审)
legalStatus:(失效)
```

第一版法律状态使用基础映射，详见 `docs/field_mapping.md`。

### 4.8 专利类型

```text
type:(发明专利)
type:(实用新型)
type:(外观设计)
```

## 5. 辅助参数

### 5.1 `ds`

| 值 | 说明 |
|---|---|
| `cn` | 仅中国专利 |
| `all` | 全球专利 |

实现建议：

1. `ds=cn` 时按 `Country=CN` 或可确认的中国专利字段过滤。
2. `ds=all` 时不加国家过滤。

### 5.2 `sort`

| 值 | 说明 |
|---|---|
| `relation` | 按相关性排序 |
| `!applicationDate` | 按申请日倒序 |

### 5.3 `highlight`

| 值 | 说明 |
|---|---|
| `0` | 不高亮 |
| `1` | 开启基础高亮 |

第一版允许先兼容参数；若开启高亮，需在接口文档中补充高亮字段结构。

## 6. 不支持或暂缓语法

第一版暂缓：

1. 任意深度嵌套括号。
2. 复杂 NOT 组合。
3. 通配符查询。
4. 模糊匹配操作符。
5. 邻近词检索。
6. 字段级 boost 语法。
7. 外采服务私有语法的完全复刻。

对不支持语法应返回明确错误：

```json
{
  "success": false,
  "code": 40001,
  "message": "q 查询语法错误：暂不支持该语法",
  "data": null
}
```

## 7. 典型查询样例

```text
currentAssignee:(华为技术有限公司)
currentAssignee:(华为技术有限公司) OR currentAssignee:(杭州华为数字技术有限公司)
ipc:H02M AND tscd:("均衡" OR "平衡")
ipc:F15B AND tscd:("主线圈" OR "副线圈")
tscd:("电液比例阀") AND tscd:("主线圈" OR "副线圈")
ipc:H02M7/483
ad:[2020-01-01 TO 2020-12-31]
legalStatus:(有效专利)
documentYear:[2020 TO 2024]
type:(发明专利)
```
