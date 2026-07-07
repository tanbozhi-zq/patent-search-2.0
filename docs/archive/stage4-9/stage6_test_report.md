# 阶段六测试报告

## 测试范围

- 完整布尔查询解析器
- 字段查询和范围查询
- `AND` / `OR` / `NOT`
- 括号优先级
- 非法查询返回 `40001`
- 阶段五查询能力回归
- 真实 OpenSearch 冒烟

## 代码审查结论

阶段六代码审查发现的阻塞问题为 `type` 查询字段错误映射到不存在的 `PatentType` 字段。开发已修复为实际存在的 OpenSearch 字段：

```python
"type": ["Type", "PatentTypeCode", "Kind"]
```

修复后复查通过：

- `app/mappings/query_field_mapping.py` 已改为正确字段。
- `tests/test_legal_status_mapping.py` 已同步断言。
- `tests/test_query_dsl_builder_stage6.py` 已同步断言。
- `docs/field_mapping.md` 已同步字段映射。
- `docs/superpowers/plans/2026-07-02-stage-6-boolean-query-parser.md` 已同步说明和断言。

仍存在的非阻塞事项：

- `agent.md` 被替换为 `agents.md`，两者内容一致；本次收口提交按文件重命名纳入 Git。
- 参数校验错误仍返回 FastAPI 默认 422 结构，建议阶段八统一异常结构时处理。

## 自动化测试

执行命令：

```bash
source .venv/bin/activate
python3 -m pytest -q
```

结果：

```text
66 passed in 0.04s
```

结论：自动化测试全部通过。

## 真实 OpenSearch 冒烟

服务启动命令：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

结果：

```json
{
  "success": true,
  "code": 0,
  "message": "ok",
  "data": {
    "status": "healthy",
    "service": "patent-search-service"
  }
}
```

搜索冒烟命令：

```bash
python3 scripts/smoke_search.py http://127.0.0.1:8000 test-token
```

结果：

```text
search ok q=tscd:(均衡) total=17973831 records=1
search ok q=ipc:H02M AND tscd:(均衡) total=68810 records=1
search ok q=applicant:(华为技术有限公司) total=45186249 records=1
search ok q=currentAssignee:(华为技术有限公司) total=45187007 records=1
search ok q=legalStatus:(有效专利) total=27284721 records=1
search ok q=documentYear:[2020 TO 2024] total=28854473 records=1
search ok q=ipc:H02M AND NOT tscd:(均衡) total=138682 records=1
search ok q=NOT title:(外观) total=65642722 records=1
```

结论：阶段六合法查询真实 OpenSearch 冒烟通过。

## 阶段五回归与重点合法查询

以下请求均通过 `POST /api/patent/search`，携带 `X-API-Key: test-token`。

| 用例 | q | HTTP | total | records | 结果 |
|---|---|---:|---:|---:|---|
| 阶段五普通关键词 | `阀门` | 200 | 510569 | 1 | 通过 |
| 阶段五标题 | `title:(阀门)` | 200 | 77927 | 1 | 通过 |
| 阶段五摘要 | `ab:(缓冲)` | 200 | 1060005 | 1 | 通过 |
| 阶段五 IPC | `ipc:H02M` | 200 | 207492 | 1 | 通过 |
| 阶段五申请日 | `ad:[2020-01-01 TO 2020-12-31]` | 200 | 5892786 | 1 | 通过 |
| 修复验证 type | `type:(发明专利)` | 200 | 30769770 | 1 | 通过 |
| 短语 OR | `tscd:("均衡" OR "平衡")` | 200 | 23207472 | 1 | 通过 |
| 括号分组 | `(title:(均衡) OR title:(平衡)) AND ipc:H02M` | 200 | 2508 | 1 | 通过 |

结论：阶段五能力无回退，阶段六新增合法查询通过。

## 非法查询验收

以下非法查询均返回 HTTP 400，业务错误码 `40001`。

| q | HTTP | code | 结果 |
|---|---:|---:|---|
| `ipc:H02M AND AND tscd:(均衡)` | 400 | 40001 | 通过 |
| `AND tscd:(均衡)` | 400 | 40001 | 通过 |
| `tscd:(均衡) OR` | 400 | 40001 | 通过 |
| `tscd:("均衡)` | 400 | 40001 | 通过 |
| `tscd:()` | 400 | 40001 | 通过 |
| `ipc:` | 400 | 40001 | 通过 |
| `foo:(均衡)` | 400 | 40001 | 通过 |
| `ad:[2020-01-01 2020-12-31]` | 400 | 40001 | 通过 |
| `ad:[2020-13-01 TO 2020-12-31]` | 400 | 40001 | 通过 |
| `ad:[2021-01-01 TO 2020-12-31]` | 400 | 40001 | 通过 |
| `documentYear:[2024 TO 2020]` | 400 | 40001 | 通过 |
| `NOT` | 400 | 40001 | 通过 |
| `tscd:(均衡) NOT` | 400 | 40001 | 通过 |

自动化测试中已覆盖“非法查询不访问 OpenSearch”的场景。

## 测试结论

阶段六测试结论：**通过**

通过依据：

1. 自动化测试全部通过：`66 passed in 0.04s`。
2. 阶段五查询能力无回退。
3. 阶段六合法查询可访问真实 OpenSearch 并返回 200。
4. `type:(发明专利)` 字段映射修复后真实查询通过。
5. 非法查询全部返回 HTTP 400，业务 code 为 `40001`。
6. 非法查询不访问真实 OpenSearch 已由自动化测试覆盖。

是否建议进入项目总控审查：**是**。
