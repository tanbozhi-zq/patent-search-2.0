# 阶段 9 手动测试用例

## 前置条件

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
export BASE_URL=http://127.0.0.1:8000
export API_TOKEN="$(grep -E '^API_TOKEN=' .env | cut -d= -f2-)"
```

## 用例 1：健康检查

```bash
python3 scripts/smoke_health.py "$BASE_URL"
```

期望：

- 输出 `health ok`

## 用例 2：查询样本集

逐条执行：

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page":1,"page_size":1}' | python3 -m json.tool
```

替换 `q` 覆盖：

```text
ipc:H02M AND tscd:(均衡)
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
legalStatus:(有效专利)
documentYear:[2020 TO 2024]
type:(发明专利)
tscd:("电液比例阀")
```

期望：

- HTTP 200
- 包含 `records`
- 首条记录包含 camelCase 与 snake_case 兼容字段

## 用例 3：详情

用 search 返回的 `patent_id` 执行：

```bash
curl -s "$BASE_URL/api/patent/detail/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 200
- 默认不含 `description`
- 包含 SaaS 工具层关键字段

## 用例 4：详情包含说明书

```bash
curl -s "$BASE_URL/api/patent/detail/$PATENT_ID?include_description=true" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 200
- 包含 `description`

## 用例 5：引证

```bash
curl -s "$BASE_URL/api/patent/citations/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 200
- 包含 `cited_by`、`patent_references`、`non_patent_references`
- 原始兼容字段仍保留

## 用例 6：错误响应

分页错误：

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page_size":101}' | python3 -m json.tool
```

查询语法错误：

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"ipc:H02M AND AND tscd:(均衡)"}' | python3 -m json.tool
```

期望：

- 分页错误 code `40003`
- 查询语法错误 code `40001`
- 响应不含外层 `detail`

## 用例 7：外采 live 对比

如果具备外采 PatentHub token，根据 `patent_harness_base_副本/backend/packages/harness/deerflow/community/patenthub/tools.py` 的工具入参执行相同查询。

记录：

1. 外采 total。
2. 外采首条 id。
3. 外采 search 字段。
4. 外采 detail 字段。
5. 外采 citations 字段。
6. 与自研服务差异类型。

如果不具备 token，报告中标记为：

```text
外采 live 对比未执行：缺少可用 PATENTHUB_API_TOKEN 或外采网络环境。
```

