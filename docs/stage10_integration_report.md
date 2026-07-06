# 阶段 10 SaaS 联调报告

## 测试角色

本次按 `agents.md` 中“测试人员”职责执行：验证 SaaS Agent 调用链路、错误响应、回退外采和灰度范围，并输出测试结论或问题清单。

## 开发内容

本阶段新增 SaaS PatentHub 工具适配层：

```text
app/integrations/patenthub_adapter.py
scripts/smoke_saas_adapter.py
```

适配层提供：

1. `patent_search(q, ds, page, page_size, sort, highlight)`
2. `patent_get_detail(patent_id, include_description=false)`
3. `patent_get_citations(patent_id)`

适配目标：

1. SaaS 工具层调用自研 search/detail/citations。
2. search 将自研 `records` 转为工具层 `patents`。
3. detail/citations 直接消费自研 snake_case 字段。
4. 自研错误 `{success, code, message, data}` 转为工具层 `{error, code}`。
5. 通过 `PATENT_SEARCH_USE_SELF_HOSTED=false` 回退外采 PatentHub。

## 配置

自研模式：

```bash
export PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
export PATENT_SEARCH_API_TOKEN="$API_TOKEN"
export PATENT_SEARCH_USE_SELF_HOSTED=true
export PATENT_SEARCH_PAGE_SIZE_LIMIT=50
export PATENT_SEARCH_TIMEOUT_SECONDS=30
```

外采回退模式：

```bash
export PATENT_SEARCH_USE_SELF_HOSTED=false
export PATENTHUB_BASE_URL=https://www.patenthub.cn
export PATENTHUB_API_TOKEN="$PATENTHUB_API_TOKEN"
```

当前 `.env` 未配置 `PATENTHUB_API_TOKEN`，因此本轮只验证了“回退开关行为”和“缺 token 时返回工具层错误”，未执行外采 live 调用。

## 自动化测试

执行命令：

```bash
.venv/bin/python -m pytest -q
```

结果：

```text
120 passed in 0.07s
```

新增覆盖：

| 测试 | 覆盖点 |
|---|---|
| `tests/test_patenthub_adapter.py` | self-hosted search/detail/citations 适配 |
| `tests/test_patenthub_adapter.py` | `records` 转 `patents` |
| `tests/test_patenthub_adapter.py` | page_size 工具层限制为 50 |
| `tests/test_patenthub_adapter.py` | 错误响应转 `{error, code}` |
| `tests/test_patenthub_adapter.py` | `PATENT_SEARCH_USE_SELF_HOSTED=false` 时走外采端点 |

代码 review 结论：

1. `app/integrations/patenthub_adapter.py` 按阶段 10 设计实现自研/外采切换。
2. self-hosted search 将自研 `records` 转换为工具层 `patents`。
3. 工具层 `page_size` 按 `PATENT_SEARCH_PAGE_SIZE_LIMIT` 限制，默认 50。
4. self-hosted 错误响应可转换为 `{error, code}`。
5. 未修改 `patent_harness_base_副本/`、OpenSearch mapping 或索引。

## 联调 Smoke

脚本：

```bash
.venv/bin/python scripts/smoke_saas_adapter.py http://127.0.0.1:8000 "$API_TOKEN"
```

验证项：

1. `patent_search` 调用自研 search 成功。
2. `patent_search` 输出顶层 `patents`。
3. `patents[0]` 包含 `id`、`title`、`summary`、`application_number`、`document_number`、`application_date`、`document_date`、`legal_status`、`main_ipc`。
4. `patent_get_detail` 可使用 search 返回的 `id`。
5. `patent_get_detail` 默认不含 `description`。
6. `patent_get_detail(include_description=true)` 包含 `description`。
7. `patent_get_citations` 返回 `cited_by`、`patent_references`、`non_patent_references`。
8. 查询语法错误返回工具层 `{error, code=40001}`。

结果：

```text
{"name": "adapter_search", "ok": true, "total": 510569, "patents": 10, "patent_id": "cn-95474fd9e61ba99b", "missing_fields": []}
{"name": "adapter_detail", "ok": true, "missing_fields": [], "has_description": false}
{"name": "adapter_detail_description", "ok": true, "has_description": true}
{"name": "adapter_citations", "ok": true, "missing_fields": []}
{"name": "adapter_error", "ok": true, "code": 40001}
```

以上 smoke 使用真实自研服务与真实 OpenSearch 数据执行，验证 search/detail/citations 主链路和查询语法错误转换。

## 边界验证

使用真实自研服务执行 adapter 边界测试：

| Case | 结果 |
|---|---|
| `patent_search(q="阀门", page_size=99)` | 返回 `page_size=50`，`patents=50`，无必填字段缺失 |
| search 顶层结构 | 包含 `patents`，不包含 `records` |
| 自研鉴权错误 | 返回 `{"error": "missing or invalid X-API-Key", "code": 40101}` |
| 查询语法错误 | 返回 `code=40001` 的工具层错误 |
| `PATENT_SEARCH_USE_SELF_HOSTED=false` 且缺外采 token | 返回 `{"error": "PATENTHUB_API_TOKEN is not configured", "code": 40101}` |

自研服务 smoke：

```text
health ok
search smoke 8 条查询均通过
detail status=200
detail_description status=200
citations status=200
```

## 灰度与回滚

建议灰度范围：

1. 测试环境。
2. 单 Agent 或单测试账号。
3. 仅启用 search/detail/citations。
4. 不启用企业专利画像、高亮片段或完整法律历史接口。

回滚方式：

```bash
export PATENT_SEARCH_USE_SELF_HOSTED=false
```

如外采 token 已配置，适配层将回退到外采 PatentHub 原始端点。若外采 token 缺失，适配层返回：

```json
{
  "error": "PATENTHUB_API_TOKEN is not configured",
  "code": 40101
}
```

## 边界确认

1. 未修改 `patent_harness_base_副本/` 作为交付源码。
2. 未修改 OpenSearch mapping，未重建索引。
3. 未生产全量切流。
4. 未实现高亮片段。
5. 未实现企业专利画像。

## 结论

阶段 10 本地自动化测试、自研服务 smoke、SaaS 工具适配层 smoke、错误转换、鉴权错误转换和回退开关缺 token 行为均已通过。

当前 `.env` 未提供 `PATENTHUB_API_TOKEN`，因此未执行外采 PatentHub live 调用；外采 token 配置后的 live 回退验证仍需补测。该项归类为测试条件限制，不作为当前适配层代码阻塞项。

测试侧建议进入阶段 11 部署上线与交付沉淀准备：是。最终是否放行阶段 11，由项目总控确认。

---

## 项目总控验收意见

验收时间：2026-07-06

总控复核结论：

1. 阶段 10 实现符合 `docs/superpowers/specs/2026-07-06-stage-10-saas-integration-design.md`。
2. 适配层已覆盖 self-hosted search/detail/citations，并提供 PatentHub-like 工具函数。
3. search 已完成 `records` -> `patents` 转换，工具层 `page_size` 默认限制为 50。
4. 自研错误响应已转换为工具层 `{error, code}`。
5. `PATENT_SEARCH_USE_SELF_HOSTED=false` 的外采回退路径已实现；缺少 `PATENTHUB_API_TOKEN` 时返回稳定错误。
6. 未修改 `patent_harness_base_副本/` 作为交付源码，未修改 OpenSearch mapping，未重建索引。
7. 外采 live 回退验证因缺少 `PATENTHUB_API_TOKEN` 未执行，归类为测试条件限制，不阻塞进入阶段 11 部署准备。

阶段 10 判定：**通过并收口**。

是否建议进入阶段 11 部署上线与交付沉淀：**是**。

阶段 11 前置要求：

1. 先补阶段 11 部署交付设计文档，再派发部署与验收任务。
2. 正式生产流量前必须确认 SaaS 访问路径、鉴权 token 责任人、回滚方式和运维交接责任人。
3. 若后续提供 `PATENTHUB_API_TOKEN`，阶段 10 外采 live 回退验证应作为补测项追加记录。
