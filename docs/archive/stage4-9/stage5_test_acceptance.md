# 阶段五测试验收单

## 1. 角色与任务

执行角色：测试人员。

配合角色：开发人员。

裁决角色：项目总控。

测试人员负责验证阶段五最小检索链路是否跑通，并输出测试结论或问题清单。

## 2. 测试依据

```text
agent.md
docs/api_spec.md
docs/field_mapping.md
docs/query_syntax.md
docs/stage5_dev_assignment.md
docs/superpowers/plans/2026-07-02-stage-5-minimal-search-chain.md
```

## 3. 验收范围

阶段五验收：

1. `POST /api/patent/search`。
2. 请求参数校验。
3. 最小 DSL Builder。
4. OpenSearch 查询执行。
5. 字段映射。
6. 分页和排序。
7. 真实 OpenSearch 冒烟测试。
8. 阶段边界检查。

## 4. 不验收范围

以下内容不属于阶段五：

1. 专利详情接口。
2. 引证/相关文献接口。
3. 复杂查询语法。
4. 外采服务结果一致性对比。
5. SaaS 联调。
6. 生产正式流量。

## 5. 自动化测试

执行：

```bash
python3 -m pytest -q
```

验收标准：

1. 所有阶段四测试继续通过。
2. 阶段五新增测试通过。
3. 单元测试不依赖真实 OpenSearch。
4. 不输出真实 OpenSearch 密码。

## 6. 接口测试

启动服务：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

执行：

```bash
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","ds":"cn","sort":"relation","page":1,"page_size":1,"highlight":0}'
```

响应必须包含：

```text
total
page
page_size
records
```

## 7. 语法验收样例

每类至少验证 1 个请求：

| 类型 | q |
|---|---|
| 普通关键词 | `阀门` |
| 标题 | `title:(阀门)` |
| 摘要 | `ab:(缓冲)` |
| IPC | `ipc:H02M` |
| 申请日范围 | `ad:[2020-01-01 TO 2020-12-31]` |

## 8. 字段验收

当 `records` 非空时，第一条记录必须检查：

```text
patent_id
applicationNumber
documentNumber
title
abstract
applicant
currentAssignee
mainIpc
ipcMainList
applicationDate
documentDate
legalStatus
type
```

空值规则：

1. 字符串缺失返回 `""`。
2. 数组缺失返回 `[]`。
3. `ipcMainList` 必须为数组。

## 9. 阶段边界检查

执行：

```bash
rg -n "tscd:|applicant:|currentAssignee:|legalStatus:|documentYear:|GET /api/patent/detail|GET /api/patent/citations" app tests
```

允许出现：

1. 错误测试中验证不支持语法。
2. 文档字符串说明后续阶段。

不允许出现：

1. 真实详情接口业务逻辑。
2. 真实引证接口业务逻辑。
3. 阶段六语法真实支持。

## 10. 测试结论模板

```text
阶段五测试结论：通过 / 不通过

pytest 结果：
搜索接口可用性：
真实 OpenSearch 冒烟：
语法样例结果：
字段映射结果：
分页排序结果：
阶段边界结果：

阻塞问题：
1.

非阻塞建议：
1.

是否建议进入项目总控审查：是 / 否
```

## 11. 通过标准

1. 自动化测试通过。
2. `POST /api/patent/search` 可用。
3. 真实 OpenSearch 冒烟测试通过。
4. 最小语法样例可执行。
5. 返回字段符合字段映射文档。
6. 不支持语法有明确错误。
7. 未发现阶段六越界实现。
