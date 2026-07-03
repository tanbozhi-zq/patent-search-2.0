# 阶段 6.5 测试验收单

## 测试目标

验证 `index_analyzer_mode` 能在当前索引 analyzer 缺陷存在时降低问题字段误召回，并确保阶段六能力不回退。

## 自动化测试

必须通过：

```bash
.venv/bin/python -m pytest -q
```

重点测试文件：

1. `tests/test_search_api.py`
2. `tests/test_search_dsl_builder.py`
3. `tests/test_query_dsl_builder_stage6.py`
4. `tests/test_query_dsl_builder_stage6_5.py`
5. `tests/test_legal_status_mapping.py`

## 请求参数验收

| 场景 | 预期 |
|---|---|
| 不传 `index_analyzer_mode` | 默认 `compat` |
| `index_analyzer_mode=compat` | 请求可用，问题字段走 phrase |
| `index_analyzer_mode=normal` | 请求可用，保留阶段六普通 DSL |
| `index_analyzer_mode=broken` | 参数校验失败 |

## DSL 验收

### `normal`

`tscd:(口腔数字印模仪图像采集方法)` 应保持阶段六 DSL：

```text
Title, Abstract, MainClaim, Requirement, Instructions 普通 multi_match
```

### `compat`

`tscd:(口腔数字印模仪图像采集方法)` 应拆成：

```text
Title, Abstract 普通 multi_match
MainClaim, Requirement, Instructions phrase multi_match
```

`title:(口腔数字印模仪)` 应拆成：

```text
Title, TitleEN 普通 multi_match
TitleCN phrase multi_match
```

`ab:(口腔数字印模仪)` 应拆成：

```text
Abstract, AbstractEN 普通 multi_match
AbstractCN phrase multi_match
```

`type:(发明专利)` 应拆成：

```text
PatentTypeCode, Kind 普通 multi_match
Type phrase multi_match
```

## 阶段六回归

以下能力不得回退：

1. `AND`、`OR`、`NOT`。
2. 括号分组。
3. `ad` 日期范围。
4. `documentYear` 公开年范围。
5. `legalStatus` 基础映射。
6. 非法查询返回 `40001`。
7. 非法查询不访问真实 OpenSearch。

## 真实 OpenSearch 对比

测试人员需记录：

| q | normal total | compat total | 结论 |
|---|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` |  |  |  |
| `tscd:(图像采集方法)` |  |  |  |
| `title:(口腔数字印模仪)` |  |  |  |
| `ab:(药物组合物)` |  |  |  |
| `type:(发明专利)` |  |  |  |

## 通过标准

1. 自动化测试全部通过。
2. 阶段六合法查询不回退。
3. 阶段六非法查询仍返回 `40001`。
4. 默认 `index_analyzer_mode=compat`。
5. `normal` 保留阶段六普通匹配逻辑。
6. `compat` 对问题字段生成 phrase 查询。
7. 典型问题查询的 `compat total` 明显低于 `normal total`，且不异常归零。
8. 未修改 OpenSearch 索引。
9. 未实现阶段七详情接口。
