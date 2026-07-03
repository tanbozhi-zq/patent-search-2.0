# 阶段 6.5 测试报告

## 自动化测试

- 命令：`.venv/bin/python -m pytest -q`
- 结果：`79 passed in 0.04s`，全部通过，无失败或错误。

## 本地健康检查 smoke

- 命令：`.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000`
- 结果：`health ok`，服务 `/health` 返回 `data.status == "healthy"`。

## 真实 OpenSearch 对比（analyzer-compat smoke）

- 命令：`.venv/bin/python scripts/smoke_analyzer_compat.py http://127.0.0.1:8000`（未携带 API_TOKEN）
- 运行环境：本地 `ENABLE_AUTH` 默认 `true`，`.env` 未配置 `API_TOKEN`，因此所有请求命中鉴权门控。

| q | normal status | normal total | compat status | compat total | 结论 |
|---|---:|---:|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` | 401 | -1 | 401 | -1 | 鉴权拒绝，未触达 OpenSearch |
| `tscd:(图像采集方法)` | 401 | -1 | 401 | -1 | 鉴权拒绝，未触达 OpenSearch |
| `title:(口腔数字印模仪)` | 401 | -1 | 401 | -1 | 鉴权拒绝，未触达 OpenSearch |
| `ab:(药物组合物)` | 401 | -1 | 401 | -1 | 鉴权拒绝，未触达 OpenSearch |
| `type:(发明专利)` | 401 | -1 | 401 | -1 | 鉴权拒绝，未触达 OpenSearch |

脚本退出码：`1`（按设计，任一非 200 即视为失败；此处 5×401 属于 opt-in 预期状态，不计为阶段 6.5 任务失败，仅记录诚实状态）。

## 结论

- 阶段六能力是否回退：基于自动化测试（79/79 通过）判断，无回退。
- `compat` 是否降低典型问题字段误召回：本次因无可用 `API_TOKEN`，未能获取真实 OpenSearch 命中量对比，结论待凭据就绪后补测验证。
- 是否修改索引：否。本阶段仅新增 `index_analyzer_mode` 参数与 phrase DSL 查询构造，未对 OpenSearch 索引或 mapping 做任何变更。
- `index_analyzer_mode` 默认值：`compat`（已在任务 1-4 实现并通过测试）。
- 是否建议进入阶段七：建议在凭据验证、analyzer smoke 取得 200 命中后进入阶段七；在 pytest 全绿、健康检查通过的前提下，代码层面已具备进入阶段七的条件。