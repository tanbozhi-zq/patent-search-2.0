# Stage 12.3 DeerFlow / Flow 联调验收单

## 1. 阶段入口

本验收单由项目总控维护，供测试人员和联调人员在真实 Flow / DeerFlow 环境中执行。

进入 Stage 12.3 前必须满足：

1. `Stage 12.1` 核心 API 兼容补点已通过。
2. `Stage 12.2` DeerFlow Tool 本地封装和 smoke 已通过。
3. `docs/delivery/deerflow_tool_integration_guide.md` 已包含 Tool 名称、参数、返回字段和本地 smoke 结果。
4. 项目总控确认可以进入真实 agent 环境联调。

## 2. 本阶段目标

验证 Flow / DeerFlow agent 能加载并调用自研专利检索 Tool，完成：

```text
patent_search -> patent_get_detail -> patent_get_citations
```

本阶段不开发 MCP Server；MCP 归入 `Stage 12.4`。

## 3. 联调准备

联调前确认：

1. 自研 API 服务可被 Flow / DeerFlow 所在环境访问。
2. `PATENT_SEARCH_BASE_URL` 指向自研 API。
3. `PATENT_SEARCH_API_TOKEN` 已通过安全方式配置。
4. `PATENT_SEARCH_PAGE_SIZE_LIMIT` 已设置。
5. Flow / DeerFlow 侧已加载 `patent_search`、`patent_get_detail`、`patent_get_citations`、`patent_get_legal_history`。
6. 不再依赖 PatentHub 临时 session id。

## 4. 联调用例

### 用例 1：普通关键词主链路

输入：

```text
请检索阀门相关专利，选择第一件专利读取详情，并查看它的引证信息。
```

期望：

1. agent 调用 `patent_search`。
2. search 返回 `patents`。
3. agent 使用 `patents[0].id` 调用 `patent_get_detail`。
4. agent 调用 `patent_get_citations`。
5. agent 输出可读分析结论。

### 用例 2：IPC + 技术词

输入：

```text
请检索 ipc:H02M AND tscd:(均衡 OR 平衡) 相关专利，并读取第一件详情。
```

期望：

1. agent 调用 `patent_search`。
2. 入参 `q` 与用户查询式一致或等价。
3. detail 包含 `claims`。

### 用例 3：申请人检索

输入：

```text
请检索 applicant:宁德时代新能源科技股份有限公司 的专利，并总结首页结果。
```

期望：

1. agent 使用 `patent_search`。
2. 返回结果可供总结。
3. 没有工具调用异常。

### 用例 4：当前权利人检索

输入：

```text
请检索 currentAssignee:华为技术有限公司 的专利，并读取一件专利的详情。
```

期望：

1. search 正常返回。
2. detail 能继续查询。

### 用例 5：错误查询式

输入：

```text
请用 ipc:H02M AND AND tscd:(均衡) 检索专利。
```

期望：

1. Tool 返回 `{error, code}`。
2. agent 能说明查询式非法。
3. Flow / DeerFlow 不崩溃。

### 用例 6：法律历史基础能力

输入：

```text
请检索一件阀门相关专利，并查看其法律状态历史。
```

期望：

1. agent 调用 `patent_get_legal_history`。
2. 返回包含 `patent_id`、`transaction_count`、`transactions`。
3. 无历史数据时也返回稳定空数组。

## 5. 对比与记录

如 PatentHub 外采服务可用，建议抽样记录：

1. 首页结果重合度。
2. 排序差异。
3. 字段缺口。
4. agent 分析是否受字段缺口影响。
5. 是否需要回到 12.1 或 12.2 补能力。

## 6. 通过标准

Stage 12.3 通过需要同时满足：

1. Flow / DeerFlow 能加载 tool。
2. agent 能完成 search -> detail -> citations 主链路。
3. 错误查询式不会导致系统崩溃。
4. Tool 返回字段满足 agent 分析需要。
5. 已输出联调记录、问题清单和是否进入 MCP 阶段的建议。

## 7. 输出文档

联调完成后，测试人员或联调人员输出：

```text
docs/internal/stage12_3_integration_report.md
```

报告至少包含：

1. 联调环境。
2. API 服务地址脱敏说明。
3. Tool 配置方式。
4. 用例执行结果。
5. 工具调用日志摘要。
6. 问题清单。
7. 字段缺口清单。
8. 是否建议进入 `Stage 12.4 MCP Server`。
