# 阶段六完整布尔查询解析器设计

## 1. 背景

阶段五已经跑通最小检索链路：

```text
POST /api/patent/search -> 参数校验 -> 最小 DSL -> OpenSearch 查询 -> 字段映射 -> 返回结果
```

阶段五的查询构造主要依赖正则识别单一查询式，适合最小链路验证，但不足以支撑 SaaS 高频检索场景。阶段六需要补齐 `tscd`、申请人、当前权利人、法律状态、专利类型、公开年、简单布尔组合和括号分组。

本阶段采用已确认的方案：**完整布尔解析器**。

## 2. 目标

阶段六目标是把 `q` 从字符串解析为 AST，再由 AST 构造 OpenSearch DSL：

```text
q 字符串 -> Tokenizer -> Parser -> AST -> DSL Builder -> OpenSearch
```

完成后应支持：

1. 字段查询：`title`、`ab`、`tscd`、`ipc`、`applicant`、`currentAssignee`、`legalStatus`、`type`。
2. 日期范围：`ad:[YYYY-MM-DD TO YYYY-MM-DD]`。
3. 公开年范围：`documentYear:[YYYY TO YYYY]`。
4. 布尔运算：`AND`、`OR`、`NOT`。
5. 括号分组：`(A OR B) AND C`。
6. 短语：`"均衡"`、`"电液比例阀"`。
7. 清晰的查询语法错误：统一返回 `40001`。

## 3. 非目标

阶段六不做：

1. 通配符查询。
2. 邻近词查询。
3. 模糊匹配操作符。
4. 字段 boost 语法。
5. 向量相似检索。
6. 完全复刻外采服务召回排序。
7. 专利详情接口。
8. 引证/相关文献接口。
9. SaaS 联调。
10. 生产入口、Nginx、API 网关改造。

## 4. 语法范围

### 4.1 字段查询

支持：

```text
title:(阀门)
ab:(缓冲)
tscd:(均衡)
tscd:("均衡" OR "平衡")
ipc:H02M
ipc:H02M7/483
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
legalStatus:(有效专利)
type:(发明专利)
ad:[2020-01-01 TO 2020-12-31]
documentYear:[2020 TO 2024]
```

### 4.2 布尔组合

支持：

```text
ipc:H02M AND tscd:(均衡)
title:(均衡) OR title:(平衡)
(title:(均衡) OR title:(平衡)) AND ipc:H02M
tscd:("均衡" OR "平衡") AND legalStatus:(有效专利)
NOT title:(外观)
ipc:H02M AND NOT tscd:(均衡)
```

### 4.3 运算优先级

解析优先级：

```text
括号 > NOT > AND > OR
```

说明：

1. `A AND B OR C` 解析为 `(A AND B) OR C`。
2. `A OR B AND C` 解析为 `A OR (B AND C)`。
3. `NOT A AND B` 解析为 `(NOT A) AND B`。

## 5. 架构设计

### 5.1 模块划分

| 文件 | 职责 |
|---|---|
| `app/query/tokens.py` | Token 类型定义 |
| `app/query/tokenizer.py` | 把 `q` 字符串转为 token 列表 |
| `app/query/ast.py` | AST 节点定义 |
| `app/query/parser.py` | 递归下降解析 token 为 AST |
| `app/query/dsl_builder.py` | AST 转 OpenSearch DSL |
| `app/mappings/query_field_mapping.py` | 查询字段到 OpenSearch 字段映射 |
| `app/mappings/legal_status_mapping.py` | 法律状态基础映射 |

### 5.2 数据流

```text
SearchRequest.q
  -> tokenize(q)
  -> parse(tokens)
  -> build_search_dsl(request)
  -> OpenSearchRepository.search(body)
  -> map_search_response(raw)
```

`SearchService` 继续作为编排层，不直接关心 token 和 AST 细节。

## 6. AST 设计

AST 使用小而明确的节点：

```text
FieldQuery(field, value)
RangeQuery(field, start, end)
Phrase(value)
AndNode(left, right)
OrNode(left, right)
NotNode(child)
```

字段查询中 `value` 可以是：

1. 普通字符串。
2. 短语字符串。
3. 布尔子表达式，例如 `tscd:("均衡" OR "平衡")`。

## 7. DSL 设计

### 7.1 普通文本字段

字段映射：

| q 字段 | OpenSearch 字段 |
|---|---|
| `title` | `Title`, `TitleCN`, `TitleEN` |
| `ab` | `Abstract`, `AbstractCN`, `AbstractEN` |
| `tscd` | `Title`, `Abstract`, `MainClaim`, `Requirement`, `Instructions` |

文本字段使用 `multi_match`。

### 7.2 IPC

`ipc:H02M` 构造为 `should`：

```text
IPC
IPCList
IPCSmallCategory
IPCLargeGroup
IPCSmallGroup
```

第一版优先使用 `term` / `match` 的组合，不做复杂前缀优化。若测试发现召回不足，再在阶段六内用 `match_phrase_prefix` 调整 IPC 字段。

### 7.3 申请人和当前权利人

字段映射：

| q 字段 | OpenSearch 字段 |
|---|---|
| `applicant` | `Applicant`, `ApplicantNormalized`, `FirstApplicant` |
| `currentAssignee` | `Assignee`, `AssigneeNormalized` |

使用 `multi_match`。

### 7.4 日期和公开年

`ad:[2020-01-01 TO 2020-12-31]`：

```json
{"range": {"ApplicationDate": {"gte": "2020-01-01", "lte": "2020-12-31"}}}
```

`documentYear:[2020 TO 2024]` 转为公开日范围：

```json
{"range": {"PublicationDate": {"gte": "2020-01-01", "lte": "2024-12-31"}}}
```

### 7.5 法律状态

基础映射：

| 查询值 | OpenSearch 条件 |
|---|---|
| `有效专利` | `LatestLegalStatus` 包含 `授权` 或 `有效` |
| `在审` | `LatestLegalStatus` 包含 `公开` 或 `实质审查` |
| `失效` | `LatestLegalStatus` 包含 `终止`、`届满`、`撤回`、`驳回` |

如果查询值不是这三类，则对 `LatestLegalStatus` 和 `LegalStatus` 做普通文本匹配。

### 7.6 布尔节点

AST 到 OpenSearch：

| AST | DSL |
|---|---|
| `AndNode` | `bool.must` |
| `OrNode` | `bool.should` + `minimum_should_match=1` |
| `NotNode` | `bool.must_not` |

顶层仍保留阶段五已有的分页、排序、`ds=cn/all` 过滤。

`NOT` 是阶段六第一版必须支持的正式语法，不只是非法输入识别。实现时将 `NOT A` 构造为 `bool.must_not`，并允许它参与组合表达式，例如 `ipc:H02M AND NOT tscd:(均衡)`。

## 8. 错误处理

新增 `QuerySyntaxError`，包含用户可读错误消息。

错误应在进入 OpenSearch 前拦截，不把不规范表达式转交给 OpenSearch。Tokenizer、Parser、DSL Builder 负责识别并抛出 `QuerySyntaxError`，`SearchService` 或 API 层只做统一捕获和响应转换，返回 `40001`。

错误来源分为三类：

| 层级 | 典型错误 | 处理 |
|---|---|---|
| Tokenizer | 引号未闭合、非法字符、无法识别的符号 | 抛出 `QuerySyntaxError` |
| Parser | `AND` / `OR` 位置错误、缺少查询条件、括号不匹配、表达式为空 | 抛出 `QuerySyntaxError` |
| DSL Builder | 不支持字段、字段值为空、日期格式非法、范围起止倒置 | 抛出 `QuerySyntaxError` |

示例：

| 错误 | 返回 message |
|---|---|
| 缺少右括号 | `q 查询语法错误：缺少右括号` |
| 引号未闭合 | `q 查询语法错误：引号未闭合` |
| `AND` 后缺少查询条件 | `q 查询语法错误：AND 后缺少查询条件` |
| `OR` 后缺少查询条件 | `q 查询语法错误：OR 后缺少查询条件` |
| 范围缺少 TO | `q 查询语法错误：范围表达式缺少 TO` |
| 不支持字段 | `q 查询语法错误：不支持字段 xxx` |
| 字段值为空 | `q 查询语法错误：字段 xxx 的值不能为空` |
| 日期格式非法 | `q 查询语法错误：日期格式非法` |
| 范围起止倒置 | `q 查询语法错误：范围起始值不能晚于结束值` |
| 空查询 | `q 查询语法错误：查询式不能为空` |

日期校验必须使用真实日期解析，不能只依赖 `YYYY-MM-DD` 正则格式；例如 `2020-13-01` 必须判定为非法日期。

API 层捕获后返回：

```json
{
  "success": false,
  "code": 40001,
  "message": "q 查询语法错误：缺少右括号",
  "data": null
}
```

### 8.1 第一版必须覆盖的非法输入

以下输入不得访问真实 OpenSearch，应直接返回 `40001`：

```text
ipc:H02M AND AND tscd:(均衡)
AND tscd:(均衡)
tscd:(均衡) OR
tscd:("均衡)
tscd:()
ipc:
foo:(均衡)
ad:[2020-01-01 2020-12-31]
ad:[2020-13-01 TO 2020-12-31]
ad:[2021-01-01 TO 2020-12-31]
documentYear:[2024 TO 2020]
NOT
tscd:(均衡) NOT
```

## 9. 测试设计

### 9.1 单元测试

新增测试：

1. `tests/test_query_tokenizer.py`
2. `tests/test_query_parser.py`
3. `tests/test_query_dsl_builder_stage6.py`
4. `tests/test_legal_status_mapping.py`

覆盖：

1. token 识别。
2. 字段查询。
3. 短语。
4. 范围。
5. `AND` / `OR` / `NOT`。
6. 括号优先级。
7. 语法错误和边界输入。
8. 法律状态映射。
9. 阶段五已有查询回归。

### 9.2 API 测试

扩展 `tests/test_search_api.py`：

1. `tscd:(均衡)` 返回 200。
2. `ipc:H02M AND tscd:(均衡)` 返回 200。
3. `tscd:("均衡" OR "平衡")` 返回 200。
4. `NOT title:(外观)` 返回 200。
5. `ipc:H02M AND NOT tscd:(均衡)` 返回 200。
6. 缺少右括号返回 40001。
7. `AND` / `OR` 位置错误返回 40001。
8. 不支持字段返回 40001。
9. 日期格式非法返回 40001。

API 测试使用 fake service 或 fake repository，不访问真实 OpenSearch。

### 9.3 真实 OpenSearch 冒烟

新增或扩展 `scripts/smoke_search.py` 的样例：

1. `tscd:(均衡)`
2. `ipc:H02M AND tscd:(均衡)`
3. `applicant:(华为技术有限公司)`
4. `currentAssignee:(华为技术有限公司)`
5. `legalStatus:(有效专利)`
6. `documentYear:[2020 TO 2024]`
7. `ipc:H02M AND NOT tscd:(均衡)`

真实冒烟需要 `.env` 中配置有效凭据和 `API_TOKEN`。

## 10. 文档更新

阶段六需要更新：

1. `docs/query_syntax.md`：标记阶段六语法已支持。
2. `docs/field_mapping.md`：补充查询字段映射确认。
3. `docs/stage6_dev_assignment.md`：开发派工单。
4. `docs/stage6_test_acceptance.md`：测试验收单。
5. `docs/stage6_test_report.md`：测试完成后输出。

## 11. 风险与控制

| 风险 | 控制 |
|---|---|
| 完整解析器复杂度上升 | 只支持冻结语法，不做通配符、boost、邻近词 |
| IPC 召回不足 | 初版使用现有字段组合，测试不足时再局部调整 |
| 法律状态口径不准 | 明确为基础映射，后续业务口径再迭代 |
| 错误提示不清晰 | Tokenizer、Parser、DSL Builder 都抛出明确 `QuerySyntaxError` |
| 影响阶段五已通链路 | 保留阶段五测试，新增回归测试 |
| 上游传入非规范表达式 | 在解析或 DSL 构建阶段抛出 `QuerySyntaxError`，API 统一返回 `40001`，不访问 OpenSearch |

## 12. 验收标准

阶段六通过需满足：

1. 自动化测试通过。
2. 阶段五所有查询仍可用。
3. `tscd` 可以检索标题、摘要、权利要求、说明书。
4. `applicant` 和 `currentAssignee` 可以检索。
5. `legalStatus` 基础映射可用。
6. `type` 和 `documentYear` 可用。
7. 简单 `AND`、`OR`、`NOT` 和括号分组可用。
8. 语法错误返回 `40001`。
9. 第一版非法输入清单均返回 `40001`，且不访问真实 OpenSearch。
10. 真实 OpenSearch 冒烟通过。
11. 未实现阶段七详情和引证业务逻辑。
