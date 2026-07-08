# 阶段 8 开发派工单

## 角色

- 项目总控：维护阶段八边界、接口兼容清单、错误码约定、Git 状态和交付文档；审查开发实现是否符合 `docs/api_spec.md` 与 SaaS 契约审计结果。
- 开发人员：按本派工单实现接口兼容与异常处理完善，不修改 SaaS 副本源码，不修改 OpenSearch mapping。
- 测试人员：按阶段八验收单验证参数错误、鉴权错误、查询语法错误、OpenSearch 异常、字段兼容和回归链路。

## 开发目标

阶段 8 目标是把已完成的 search/detail/citations 接口从“能力可用”推进到“联调前契约稳定”：

```text
统一错误结构 + 补齐兼容边界 + 固化对外接口行为
```

完成后，SaaS 或业务后端在替换接口地址前，应能清楚知道：

1. 所有业务接口成功响应的字段结构。
2. 所有常见失败场景的 HTTP 状态码和业务 `code`。
3. 哪些 PatentHub 行为已兼容，哪些明确不复制。
4. 哪些参数已支持，哪些参数仅兼容接收或暂缓。

## 开发范围

### 1. 参数校验错误统一

将 FastAPI/Pydantic 默认参数校验错误统一转换为项目错误结构：

```json
{
  "success": false,
  "code": 40002,
  "message": "参数非法：page_size must be less than or equal to 100",
  "data": null
}
```

覆盖场景至少包括：

1. `q` 缺失或为空。
2. `q` 超过最大长度。
3. `ds` 不是 `cn` 或 `all`。
4. `sort` 不在支持列表。
5. `page < 1`。
6. `page_size < 1` 或 `page_size > 100`。
7. `highlight` 不为 `0/1`。
8. `index_analyzer_mode` 不为 `compat/normal`。

### 2. 业务异常结构复核

统一确认并补齐以下错误响应：

| 场景 | HTTP | code |
|---|---:|---:|
| 查询语法错误 | 400 | `40001` |
| 参数非法 | 400 | `40002` |
| 分页参数非法 | 400 | `40003` |
| 专利不存在 | 404 | `40401` |
| 鉴权缺失或错误 | 401 | `40101` |
| OpenSearch 查询异常 | 502 | `50001` |
| 服务内部异常 | 500 | `50002` |

阶段 8 需特别处理当前 FastAPI `HTTPException(detail={...})` 带来的外层 `detail` 包装问题。对外错误响应应与 `docs/api_spec.md` 的通用失败响应保持一致，直接返回：

```json
{
  "success": false,
  "code": 40001,
  "message": "q 查询语法错误：缺少右括号",
  "data": null
}
```

不得继续依赖以下框架默认结构作为正式契约：

```json
{
  "detail": {
    "success": false,
    "code": 40001,
    "message": "...",
    "data": null
  }
}
```

### 3. Search 兼容边界

对照 `docs/saas_patent_contract_audit.md` 与 `patent_harness_base_副本/backend/packages/harness/deerflow/community/patenthub/tools.py`，确认阶段八对 search 的兼容边界：

1. 当前 HTTP API 响应继续保留 `records`，不强行改成 PatentHub 工具层 `patents`。
2. 搜索记录补充 snake_case 别名：`application_number`、`document_number`、`application_date`、`document_date`、`legal_status`、`main_ipc`。
3. `page_size` 最大值固定为 100；PatentHub 工具层文档为最大 50，阶段八不收窄 HTTP API 上限。
4. `sort` 当前支持 `relation`、`!applicationDate`；PatentHub 支持更多排序。阶段八只允许补充文档明确的排序，不做无文档扩展。
5. `highlight=1` 当前仅兼容接收，成功响应结构与 `highlight=0` 一致，不返回高亮片段。

### 4. OpenSearch 异常包装

确保 OpenSearch 异常不会以框架默认 500 泄漏给调用方：

1. search 查询异常返回 `50001`。
2. detail 查询异常返回 `50001`。
3. citations 查询异常返回 `50001`。
4. 日志记录异常上下文，但响应不暴露敏感连接信息、账号、密码或完整内部堆栈。

### 5. 文档同步

开发完成后必须同步：

1. `docs/api_spec.md`
2. `docs/query_syntax.md`（如查询参数或错误行为变化）
3. `README.md`
4. `docs/stage8_test_report.md`

## 阶段边界

阶段 8 不做：

1. 不修改 `patent_harness_base_副本/` 源码。
2. 不做 SaaS 联调。
3. 不进入灰度发布。
4. 不修改 OpenSearch mapping。
5. 不重建索引。
6. 不实现企业专利画像。
7. 不实现完整法律历史独立接口，除非另行冻结需求。
8. 不追求与外采 PatentHub 召回结果、排序结果完全一致。

## 提交要求

开发人员应按小步提交，建议顺序：

1. `fix: normalize request validation errors`
2. `fix: wrap search opensearch failures`
3. `feat: add search compatibility aliases`（如总控确认需要）
4. `docs: update stage 8 api compatibility notes`
5. `test: add stage 8 error compatibility coverage`

每次提交前运行相关测试；最终提交前运行完整测试：

```bash
.venv/bin/python -m pytest -q
```
