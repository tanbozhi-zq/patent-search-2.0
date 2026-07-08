# 阶段六测试验收单

## 测试目标

验证阶段六完整布尔查询解析器可用，并确认非法查询在访问 OpenSearch 前返回 `40001`。

## 自动化测试

必须通过：

```bash
.venv/bin/python -m pytest -q
```

重点测试文件：

1. `tests/test_query_tokenizer.py`
2. `tests/test_query_parser.py`
3. `tests/test_query_dsl_builder_stage6.py`
4. `tests/test_legal_status_mapping.py`
5. `tests/test_search_api.py`
6. `tests/test_search_dsl_builder.py`
7. `tests/test_search_service.py`

## 合法查询验收

以下查询应返回 200，且响应结构保持阶段五口径：

```text
tscd:(均衡)
ipc:H02M AND tscd:(均衡)
tscd:("均衡" OR "平衡")
NOT title:(外观)
ipc:H02M AND NOT tscd:(均衡)
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
legalStatus:(有效专利)
type:(发明专利)
ad:[2020-01-01 TO 2020-12-31]
documentYear:[2020 TO 2024]
```

## 非法查询验收

以下查询必须返回 HTTP 400，业务 code 为 `40001`，并且测试中不得调用真实 OpenSearch：

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

## 真实 OpenSearch 冒烟

生产 OpenSearch 凭据和 `API_TOKEN` 配置完成后运行：

```bash
.venv/bin/python scripts/smoke_search.py
```

冒烟至少覆盖：

```text
tscd:(均衡)
ipc:H02M AND tscd:(均衡)
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
legalStatus:(有效专利)
documentYear:[2020 TO 2024]
ipc:H02M AND NOT tscd:(均衡)
```

## 通过标准

1. 自动化测试全部通过。
2. 阶段五查询能力不回退。
3. 阶段六合法查询可构造正确 DSL。
4. 阶段六非法查询返回 `40001`。
5. 非法查询不会访问真实 OpenSearch。
6. 真实 OpenSearch 冒烟通过。
7. 测试报告落到 `docs/stage6_test_report.md`。
