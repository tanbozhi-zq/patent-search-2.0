# Stage 10.5 Fine-Grained Text Query Design

## 1. Background

当前 `q` 查询语法已支持：

```text
title
ab
tscd
```

其中 `tscd` 为综合全文检索字段，覆盖：

```text
Title
Abstract
MainClaim
Requirement
Instructions
```

业务侧现在需要在不改变 `tscd` 行为的前提下，支持对首权、完整权利要求书、说明书进行单字段检索，以便进行更精确的专利技术特征检索。

## 2. Goals

新增三个 `q` 字段：

| 查询字段 | 含义 | OpenSearch 字段 |
|---|---|---|
| `mainClaim` | 首权/主权利要求 | `MainClaim` |
| `claims` | 完整权利要求书 | `Requirement` |
| `description` | 说明书 | `Instructions` |

目标：

1. 支持 `mainClaim:(关键词)`。
2. 支持 `claims:(关键词)`。
3. 支持 `description:(关键词)`。
4. 支持短语和布尔组合，例如 `claims:("均衡" OR "平衡")`。
5. 支持与其他字段组合，例如 `ipc:H02M AND claims:(均衡)`。
6. 保持 `title`、`ab`、`tscd` 现有行为不变。
7. 不修改 OpenSearch mapping。

## 3. Non-Goals

本增强不做：

1. 不修改 OpenSearch 索引 mapping。
2. 不重建索引。
3. 不新增高亮片段。
4. 不新增排序字段。
5. 不改变 `tscd` 的字段范围。
6. 不改变 analyzer 兼容模式默认值。
7. 不进入部署上线任务。

## 4. Mapping Strategy

新增字段映射：

```python
TEXT_FIELD_MAPPING = {
    "mainClaim": ["MainClaim"],
    "claims": ["Requirement"],
    "description": ["Instructions"],
}
```

`SUPPORTED_FIELDS` 由 `TEXT_FIELD_MAPPING` 自动包含新增字段。

## 5. Analyzer Compatibility

阶段 6.5 已确认 `MainClaim`、`Requirement`、`Instructions` 属于 analyzer 风险字段。

因此新增字段采用：

| 查询字段 | normal mode | compat mode |
|---|---|---|
| `mainClaim` | `multi_match` on `MainClaim` | phrase `multi_match` on `MainClaim` |
| `claims` | `multi_match` on `Requirement` | phrase `multi_match` on `Requirement` |
| `description` | `multi_match` on `Instructions` | phrase `multi_match` on `Instructions` |

实现方式：

```python
NORMAL_ANALYZER_FIELDS_BY_QUERY_FIELD = {
    "mainClaim": [],
    "claims": [],
    "description": [],
}

RISKY_ANALYZER_FIELDS_BY_QUERY_FIELD = {
    "mainClaim": ["MainClaim"],
    "claims": ["Requirement"],
    "description": ["Instructions"],
}
```

`compat` 模式下只有 risky 字段时，DSL 直接返回 phrase `multi_match`。

## 6. Query Examples

合法示例：

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

非法示例沿用现有语法规则：

```text
mainClaim:
claims:()
description:(均衡) AND AND ipc:H02M
```

非法查询返回：

```json
{
  "success": false,
  "code": 40001,
  "message": "q 查询语法错误：...",
  "data": null
}
```

## 7. Documentation Updates

开发完成后需同步：

```text
docs/query_syntax.md
docs/field_mapping.md
docs/api_spec.md
```

## 8. Acceptance Criteria

通过标准：

1. 自动化测试全部通过。
2. `mainClaim:(均衡)` DSL 指向 `MainClaim`。
3. `claims:(均衡)` DSL 指向 `Requirement`。
4. `description:(均衡)` DSL 指向 `Instructions`。
5. `compat` 模式下新增字段使用 phrase 查询。
6. `normal` 模式下新增字段使用普通 `multi_match`。
7. `tscd` 字段映射保持 `Title`、`Abstract`、`MainClaim`、`Requirement`、`Instructions` 不变。
8. 不修改 OpenSearch mapping。

