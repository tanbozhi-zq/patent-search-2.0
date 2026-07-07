# 阶段五开发派工单

## 1. 角色与任务

执行角色：开发人员。

调度角色：项目总控。

验收角色：测试人员、项目总控。

本阶段任务是跑通最小检索链路：

```text
HTTP 请求 -> 参数校验 -> OpenSearch 查询 -> 字段映射 -> 返回结果
```

详细实施步骤以以下任务书为准：

```text
docs/superpowers/plans/2026-07-02-stage-5-minimal-search-chain.md
```

## 2. 开发目标

阶段五完成后，项目应具备：

1. `POST /api/patent/search` 初版。
2. 最小查询 DSL Builder。
3. OpenSearch 查询执行方法。
4. 搜索结果字段映射。
5. 搜索服务编排层。
6. 搜索接口自动化测试。
7. 搜索冒烟测试脚本。

## 3. 允许开发范围

| 模块 | 允许内容 |
|---|---|
| `app/schemas/search.py` | search 请求参数模型 |
| `app/query/dsl_builder.py` | 阶段五最小 DSL 构造 |
| `app/mappings/result_mapper.py` | 搜索结果字段映射 |
| `app/repositories/opensearch_repo.py` | 增加 `search()` 方法 |
| `app/services/search_service.py` | search 编排 |
| `app/api/search.py` | `POST /api/patent/search` |
| `scripts/smoke_search.py` | 真实搜索冒烟脚本 |
| `tests/` | 单元测试和接口测试 |

## 4. 禁止开发范围

阶段五禁止实现：

1. 专利详情接口业务逻辑。
2. 引证/相关文献接口业务逻辑。
3. `tscd` 全文语法。
4. `applicant`、`currentAssignee`、`legalStatus`、`type`、`documentYear` 查询语法。
5. 复杂 `AND/OR/NOT`。
6. 嵌套括号解析。
7. 高亮字段返回结构。
8. 外采服务结果对比优化。
9. 性能优化、缓存、日志审计扩展。

如开发中发现必须越界，必须先提交问题给项目总控确认。

## 5. 阶段五最小支持语法

| 语法 | 示例 |
|---|---|
| 普通关键词 | `阀门` |
| 标题检索 | `title:(阀门)` |
| 摘要检索 | `ab:(缓冲)` |
| IPC 检索 | `ipc:H02M` |
| 申请日范围 | `ad:[2020-01-01 TO 2020-12-31]` |

辅助参数：

| 参数 | 支持范围 |
|---|---|
| `ds` | `cn`、`all` |
| `sort` | `relation`、`!applicationDate` |
| `page` | `>=1` |
| `page_size` | `1..100` |
| `highlight` | 接收 `0/1`，本阶段不要求返回高亮片段 |

## 6. 返回结构要求

成功响应保持外采服务接近结构：

```json
{
  "total": 128,
  "page": 1,
  "page_size": 50,
  "records": []
}
```

记录字段至少包含：

```text
id
patent_id
applicationNumber
documentNumber
title
ti
abstract
ab
applicant
pa
currentAssignee
inventor
mainIpc
ipcMainList
applicationDate
ad
documentDate
legalStatus
currentStatus
type
score
```

## 7. 开发验收命令

开发人员提交前必须执行：

```bash
python3 -m pytest -q
```

有真实 `.env` 时执行：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
python3 scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
```

## 8. 完成定义

只有同时满足以下条件，阶段五开发才算完成：

1. 自动化测试通过。
2. `POST /api/patent/search` 可调用。
3. 有真实凭据时可查到 OpenSearch 数据。
4. 返回结构包含 `total` 和 `records`。
5. 返回字段符合 `docs/field_mapping.md`。
6. 不支持语法返回明确错误。
7. 未实现阶段六及以后能力。
8. 测试人员完成验收。
9. 项目总控审查通过。
