# 阶段 11 测试验收单

## 自动化回归

部署前或部署包生成前必须确认：

```bash
.venv/bin/python -m pytest -q
```

## 部署后 Smoke

必须执行：

```bash
python3 scripts/smoke_health.py http://127.0.0.1:8000
python3 scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
python3 scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
python3 scripts/smoke_saas_adapter.py http://127.0.0.1:8000 "$API_TOKEN"
```

## systemd 验收

必须验证：

```bash
systemctl status patent-search-service
systemctl restart patent-search-service
systemctl is-enabled patent-search-service
```

## 接口验收

| Case | Expected |
|---|---|
| `/health` | HTTP 200，healthy |
| search | HTTP 200，返回 records |
| detail | HTTP 200 |
| citations | HTTP 200 |
| adapter search | 返回 patents |
| 未授权 | HTTP 401，code `40101` |
| 查询语法错误 | HTTP 400，code `40001` |

## 日志验收

必须确认：

1. `/var/log/patent-search-service/service.log` 可写。
2. `/var/log/patent-search-service/error.log` 可写。
3. 日志不输出 `API_TOKEN`、`OPENSEARCH_PASS`、`PATENTHUB_API_TOKEN`。

## 回滚验收

必须确认：

1. 可停止当前 systemd 服务。
2. 可切回上一部署版本或提交。
3. SaaS 侧可通过配置回退外采。
4. 回滚后 health 和核心 smoke 可恢复。

## 通过标准

1. 自动化回归通过。
2. 部署后 smoke 通过。
3. systemd 验收通过。
4. 鉴权启用。
5. 日志和密钥检查通过。
6. 回滚方案验证通过。
7. 部署报告和运维交接文档齐全。

