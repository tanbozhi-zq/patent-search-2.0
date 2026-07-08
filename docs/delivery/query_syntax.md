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
| 代理机构 | `agency:(知识产权代理)` | 支持 |
| 代理人 | `agent:(张)` | 支持 |
| 法律状态 | `legalStatus:(有效专利)` | 支持基础映射 |
| 专利类型 | `type:(发明专利)` | 支持 |
| 公开年 | `documentYear:[2020 TO 2024]` | 支持 |
| 简单 AND | `ipc:H02M AND tscd:(均衡)` | 支持 |
| 简单 OR | `title:(均衡) OR title:(平衡)` | 支持 |
| 常规多级括号分组 | `(title:(均衡) OR (title:(平衡) AND ipc:H02M))` | 支持 |

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

### 4.4 细粒度文本字段

阶段 10.5 起支持对首权、完整权利要求书和说明书做单字段检索，且不改变 `tscd` 既有覆盖范围。

| 查询字段 | 含义 | OpenSearch 字段 |
|---|---|---|
| `mainClaim` | 首权/主权利要求 | `MainClaim` |
| `claims` | 完整权利要求书 | `Requirement` |
| `description` | 说明书 | `Instructions` |

示例：

```text
mainClaim:(均衡)
claims:(均衡)
description:(均衡)
mainClaim:("均衡" OR "平衡")
claims:("均衡" OR "平衡")
description:("均衡" OR "平衡")
ipc:H02M AND claims:(均衡)
mainClaim:(电路) AND NOT description:(外观)
```

`index_analyzer_mode=compat` 下，`mainClaim`、`claims`、`description` 使用 phrase 查询；`index_analyzer_mode=normal` 下使用普通 `multi_match`。

### 4.5 IPC

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

Stage 12.1 起支持裸 IPC 自动识别，以下输入会按 IPC 查询处理：

```text
H02M
H02M7/483
F16K
```

普通中文词如 `阀门` 仍按标题/摘要关键词检索；普通英文词不符合 IPC 基础格式时不自动转 IPC。

### 4.6 申请人、当前权利人、代理机构和代理人

```text
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
currentAssignee:(华为技术有限公司) OR currentAssignee:(杭州华为数字技术有限公司)
agency:(知识产权代理)
agent:(张)
```

### 4.7 日期范围

申请日：

```text
ad:[2020-01-01 TO 2020-12-31]
```

公开年：

```text
documentYear:[2020 TO 2024]
```

### 4.8 法律状态

```text
legalStatus:(有效专利)
legalStatus:(在审)
legalStatus:(失效)
```

第一版法律状态使用基础映射，详见 `docs/delivery/field_mapping.md`。

### 4.9 专利类型

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
| `rank` | 按相关性排序，兼容 PatentHub 原工具入参 |
| `relevance` | 按相关性排序，兼容 PatentHub 风格入参 |
| `score` | 按相关性排序，兼容 PatentHub 风格入参 |
| `applicationDate` | 按申请日升序 |
| `!applicationDate` | 按申请日倒序 |
| `documentDate` | 按公开日/公告日升序 |
| `!documentDate` | 按公开日/公告日倒序 |

### 5.3 `highlight`

| 值 | 说明 |
|---|---|
| `0` | 不高亮 |
| `1` | 兼容接收高亮参数 |

阶段 8 固定为兼容接收：`highlight=1` 不报错，但当前搜索响应不返回高亮片段或额外高亮字段。

## 6. 不支持或不承诺语法

当前不支持或不承诺：

1. 极端深度括号嵌套或明显不可读的超长表达式。
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
agency:(知识产权代理)
agent:(张)
ipc:H02M AND tscd:("均衡" OR "平衡")
ipc:F15B AND tscd:("主线圈" OR "副线圈")
H02M
H02M7/483
F16K
tscd:("电液比例阀") AND tscd:("主线圈" OR "副线圈")
ipc:H02M7/483
ad:[2020-01-01 TO 2020-12-31]
legalStatus:(有效专利)
documentYear:[2020 TO 2024]
type:(发明专利)
mainClaim:(均衡)
claims:("均衡" OR "平衡")
description:(均衡)
ipc:H02M AND claims:(均衡)
mainClaim:(电路) AND NOT description:(外观)
(title:(均衡) OR (title:(平衡) AND ipc:H02M))
((title:(均衡) OR title:(平衡)) AND (ipc:H02M OR ipc:F16K))
```

## 阶段 6.5 索引 analyzer 兼容参数

`index_analyzer_mode` 控制当前索引分词缺陷的查询兼容策略：

- `compat`：默认，对 `TitleCN`、`AbstractCN`、`MainClaim`、`Requirement`、`Instructions`、`Type` 等问题字段使用 phrase 查询降低误召回。
- `normal`：保留阶段六普通匹配逻辑，用于对比测试和未来索引修复后的切换。

## 阶段六已支持语法

- 字段查询：`title`、`ab`、`tscd`、`mainClaim`、`claims`、`description`、`ipc`、`applicant`、`currentAssignee`、`agency`、`agent`、`legalStatus`、`type`
- 日期范围：`ad:[YYYY-MM-DD TO YYYY-MM-DD]`
- 公开年范围：`documentYear:[YYYY TO YYYY]`
- 布尔运算：`AND`、`OR`、`NOT`
- 常规多级括号分组：`(title:(均衡) OR (title:(平衡) AND ipc:H02M))`
- 短语：`tscd:("均衡" OR "平衡")`
- 非法查询语法返回 `40001`，且不会访问 OpenSearch
