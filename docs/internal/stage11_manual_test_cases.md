# 阶段 11 手动测试用例

## 用例 1：服务状态

```bash
systemctl status patent-search-service
```

期望：

- `active (running)`

## 用例 2：健康检查

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

期望：

- `success=true`
- `data.status=healthy`

## 用例 3：搜索

```bash
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page":1,"page_size":1}' | python3 -m json.tool
```

期望：

- HTTP 200
- `records[0].patent_id` 非空

## 用例 4：详情与引证

```bash
python3 scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
```

期望：

- detail 200
- detail_description 200
- citations 200

## 用例 5：SaaS Adapter

```bash
python3 scripts/smoke_saas_adapter.py http://127.0.0.1:8000 "$API_TOKEN"
```

期望：

- adapter_search ok
- adapter_detail ok
- adapter_detail_description ok
- adapter_citations ok
- adapter_error ok

## 用例 6：鉴权错误

```bash
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -d '{"q":"阀门"}' | python3 -m json.tool
```

期望：

- HTTP 401
- code `40101`

## 用例 7：重启恢复

```bash
sudo systemctl restart patent-search-service
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

期望：

- 重启成功
- health ok

