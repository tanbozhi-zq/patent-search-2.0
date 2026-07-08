# Stage 9 Vendor Comparison Design

## 1. Background

阶段 8 已完成接口兼容与异常处理收口，自研服务的 search/detail/citations 主链路可用于联调前验证。

阶段 9 目标是对照外采 PatentHub/SaaS 工具契约与可用外采结果，验证自研服务是否具备进入 SaaS 联调和灰度验证的条件。

对照证据源：

```text
docs/saas_patent_contract_audit.md
patent_harness_base_副本/backend/packages/harness/deerflow/community/patenthub/tools.py
docs/api_spec.md
docs/field_mapping.md
docs/query_syntax.md
```

## 2. Goals

阶段 9 验证目标：

1. 对比自研 search/detail/citations 与 PatentHub 工具契约的入参、出参和错误行为。
2. 抽样对比外采服务与自研服务的搜索召回、字段完整性和详情/引证可用性。
3. 明确差异类型：阻塞、非阻塞、数据源差异、设计边界差异。
4. 输出阶段 9 外采服务对比报告和问题清单。
5. 给出是否允许进入阶段 10 SaaS 联调的总控建议。

## 3. Non-Goals

阶段 9 不做：

1. 不修改 SaaS 副本源码。
2. 不做 SaaS 正式联调。
3. 不修改 OpenSearch mapping。
4. 不重建索引。
5. 不要求召回结果、排序结果、高亮结果与外采 PatentHub 完全一致。
6. 不实现阶段 8 已明确暂缓的高亮片段。
7. 不实现企业专利画像。
8. 不实现完整独立法律历史接口，除非阶段 9 报告确认其为 SaaS 联调阻塞项。

## 4. Comparison Strategy

阶段 9 采用“三层对比”。

### 4.1 Contract comparison

基于 SaaS 副本中 `patenthub/tools.py` 和本项目 API 文档，对比：

1. 工具入参是否可映射到自研 HTTP API。
2. search 结果字段是否覆盖工具层关键字段。
3. detail 结果字段是否覆盖工具层关键字段。
4. citations 结果字段是否覆盖工具层关键字段。
5. 错误响应是否能被上游稳定识别。

### 4.2 Live sample comparison

若具备外采 PatentHub token 或可调用环境，抽样调用外采服务与自研服务。

若不具备外采 token，则执行离线契约对比，并把 live 对比标记为阻塞于凭据/网络条件，不阻塞代码层进入 SaaS 联调准备。

### 4.3 Difference classification

每个差异按以下类型标注：

| Type | Meaning |
|---|---|
| `blocking` | 会阻止 SaaS Agent 或业务后端完成核心调用 |
| `non_blocking` | 不影响核心调用，但需记录或后续优化 |
| `data_difference` | 外采与自研底层数据源差异导致 |
| `design_boundary` | 本项目已明确不复制或暂缓的行为 |
| `unknown` | 需要业务方或数据方进一步确认 |

## 5. Sample Set

阶段 9 至少覆盖以下查询：

```text
阀门
ipc:H02M AND tscd:(均衡)
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
legalStatus:(有效专利)
documentYear:[2020 TO 2024]
type:(发明专利)
tscd:("电液比例阀")
```

至少从 search 结果中选取：

1. 有申请号和公开号的普通专利。
2. 有 `ReferencesCited` 的专利。
3. 有 `RelatedDocuments` 的专利。
4. 有 `Instructions` 或可验证 `include_description=true` 行为的发明专利。
5. 一条不存在的 patent_id，用于错误响应对比。

## 6. Acceptance Criteria

阶段 9 通过标准：

1. 自动化测试全部通过。
2. 自研服务真实 OpenSearch smoke 通过。
3. search/detail/citations 字段覆盖 SaaS 工具层关键字段。
4. 已明确 `records` 与 PatentHub `patents` 的顶层结构差异，并说明适配策略。
5. 已明确 `page_size=100` 与 PatentHub 工具层 `page_size=50` 的差异。
6. 已明确 `highlight=1` 当前仅兼容接收。
7. 外采 live 对比如不可执行，必须记录阻塞原因和补测条件。
8. 阶段 9 报告输出阻塞问题清单；若无阻塞问题，总控可放行阶段 10 SaaS 联调。

## 7. Deliverables

阶段 9 交付文档：

```text
docs/stage9_test_assignment.md
docs/stage9_test_acceptance.md
docs/stage9_manual_test_cases.md
docs/stage9_vendor_comparison_report.md
```

阶段 9 结束时，项目总控需在报告中明确：

```text
是否建议进入阶段 10 SaaS 联调：是/否
```

