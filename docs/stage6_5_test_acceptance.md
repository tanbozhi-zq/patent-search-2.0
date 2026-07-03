# 阶段 6.5 测试验收单

## 自动化测试

必须通过：

.venv/bin/python -m pytest -q

## DSL 验收

- 不传 `index_analyzer_mode` 时默认 `compat`。
- `index_analyzer_mode=normal` 保留阶段六普通 `multi_match`。
- `index_analyzer_mode=compat` 对问题字段生成 phrase 查询。

## 真实 OpenSearch 对比

至少记录：

| q | normal total | compat total | 结论 |
|---|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` |  |  |  |
| `tscd:(图像采集方法)` |  |  |  |
| `title:(口腔数字印模仪)` |  |  |  |
| `ab:(药物组合物)` |  |  |  |
| `type:(发明专利)` |  |  |  |

## 通过标准

1. 自动化测试通过。
2. 阶段六合法/非法查询不回退。
3. `compat` 对典型问题查询明显降低误召回。
4. 不修改 OpenSearch 索引。
5. 不实现阶段七详情接口。