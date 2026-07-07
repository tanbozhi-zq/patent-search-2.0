# Stage 12 代码 Review 清单

## 1. 使用对象

本清单供测试人员在执行 Stage 12 测试前 review 开发提交使用，也供项目总控审查开发结果是否符合文档约定。

Review 结论必须先于测试结论输出。

## 2. 基础信息

Review 时记录：

| 项 | 内容 |
|---|---|
| 分支 | `feature/stage-12-deerflow-tool-mcp` 或开发人员实际分支 |
| Base commit | 开发前提交号 |
| Head commit | 待 review 提交号 |
| Review 人 | 测试人员或项目总控 |
| Review 时间 | 实际时间 |

建议命令：

```bash
git status --short --branch
git log --oneline -5
git diff --stat <base_commit>..<head_commit>
git diff --name-status <base_commit>..<head_commit>
```

## 3. 目录边界检查

| 检查项 | 通过标准 |
|---|---|
| `app/` | 只保留核心 API 能力，不依赖 `deerflow_tool/` 或 `mcp_server/` |
| `deerflow_tool/` | 只做 Flow / DeerFlow tool 适配 |
| `mcp_server/` | 只做 MCP 协议封装，且必须在 Tool 联调稳定后出现 |
| `patent_harness_base_副本/` | 不允许有任何修改 |
| `对外交付文档/` | 未经项目总控确认不修改 |
| `.env` / 密钥文件 | 不允许提交 |

检查命令：

```bash
git diff --name-only <base_commit>..<head_commit> -- patent_harness_base_副本
git diff --name-only <base_commit>..<head_commit> -- .env
```

## 4. API 边界检查

| 检查项 | 通过标准 |
|---|---|
| 路径稳定 | `/health`、`/api/patent/search`、`/api/patent/detail/{patent_id}`、`/api/patent/citations/{patent_id}` 不变 |
| search 返回 | HTTP API 继续返回 `records` |
| Tool 返回 | Tool 层返回 `patents` |
| 错误结构 | 继续返回扁平 `{success, code, message, data}` 或 Tool `{error, code}` |
| 鉴权 | 继续使用 `X-API-Key` |
| OpenSearch | 不修改 mapping，不重建索引 |

## 5. 核心 API 兼容检查

| 编号 | 检查项 | 通过标准 |
|---|---|---|
| A1 | sort 兼容 | 新增兼容值有测试，非法值仍可控 |
| A2 | `agency` / `agent` | 字段映射与 DSL 测试齐全 |
| A3 | 裸 IPC | 有正常 IPC 和普通关键词不误判测试 |
| A4 | 分页 metadata | `total_pages`、`next_page`、`took_ms` 有边界测试 |
| A5 | legal history | 基础返回结构稳定 |

## 6. DeerFlow Tool 检查

| 检查项 | 通过标准 |
|---|---|
| HTTP 调用 | Tool 通过 `PATENT_SEARCH_BASE_URL` 调用自研 API |
| 鉴权 | Tool 通过 `PATENT_SEARCH_API_TOKEN` 设置 `X-API-Key` |
| 字段映射 | 优先复用或对齐 `PatentHubToolAdapter` |
| 错误转换 | API 错误转换为 `{error, code}` |
| page_size | 受 `PATENT_SEARCH_PAGE_SIZE_LIMIT` 限制 |
| legal history | 第一版结构符合任务单 |
| 直接 OpenSearch | 不允许出现 |

可用检查：

```bash
rg -n "OpenSearch|OPENSEARCH|OpenSearchRepository" app -g '*.py'
test ! -d deerflow_tool || rg -n "OpenSearch|OPENSEARCH|OpenSearchRepository" deerflow_tool -g '*.py'
test ! -d mcp_server || rg -n "OpenSearch|OPENSEARCH|OpenSearchRepository" mcp_server -g '*.py'
```

如果命中 `deerflow_tool/` 或 `mcp_server/`，必须解释原因；通常应判定为不通过。

## 7. 测试覆盖检查

必须有测试覆盖：

1. 新增查询字段。
2. 裸 IPC 自动识别。
3. search metadata。
4. Tool search/detail/citations。
5. Tool 错误转换。
6. Tool page_size 限制。
7. legal history 基础结构。

必须执行：

```bash
.venv/bin/python -m pytest -q
```

## 8. 安全与日志检查

不允许出现：

1. 明文 `API_TOKEN`。
2. 明文 `PATENT_SEARCH_API_TOKEN`。
3. 明文 `OPENSEARCH_PASS`。
4. 真实服务器密码。
5. 真实客户密钥。

建议检查：

```bash
rg -n "API_TOKEN=|PATENT_SEARCH_API_TOKEN=|OPENSEARCH_PASS=|password|passwd|secret|token" .
```

命中 `.env.example` 或文档中的占位说明可以接受；真实值不接受。

## 9. Review 结论格式

输出到测试报告或单独问题清单：

```text
Review 结论：通过 / 不通过
Base commit:
Head commit:
阻塞问题:
重要问题:
建议问题:
是否允许进入功能测试:
```

阻塞问题必须由开发人员修复后重新 review。
