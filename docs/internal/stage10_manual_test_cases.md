# 阶段 10 手动测试用例

## 前置条件

1. 自研服务已启动。
2. SaaS 工具适配层已配置自研服务 base_url 和 API token。
3. 回退外采开关可配置。

示例：

```bash
export PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
export PATENT_SEARCH_API_TOKEN=...
export PATENT_SEARCH_USE_SELF_HOSTED=true
export PATENT_SEARCH_PAGE_SIZE_LIMIT=50
```

## 用例 1：SaaS 工具 search

调用：

```text
patent_search(q="阀门", ds="cn", page=1, page_size=10)
```

期望：

- 返回 JSON 字符串。
- 顶层包含 `patents`。
- `patents[0].id` 非空。
- `patents[0].summary` 存在。

## 用例 2：SaaS 工具 detail

使用用例 1 返回的 `id`：

```text
patent_get_detail(patent_id="<id>", include_description=false)
```

期望：

- 返回详情 JSON。
- 包含 `application_number`、`document_number`、`legal_status`、`main_ipc`、`claims`。
- 默认不包含 `description` 或 `description` 为空。

## 用例 3：SaaS 工具 detail 带 description

```text
patent_get_detail(patent_id="<id>", include_description=true)
```

期望：

- 响应包含 `description` 字段。

## 用例 4：SaaS 工具 citations

```text
patent_get_citations(patent_id="<id>")
```

期望：

- 包含 `cited_by`。
- 包含 `patent_references`。
- 包含 `non_patent_references`。

## 用例 5：错误响应

```text
patent_search(q="ipc:H02M AND AND tscd:(均衡)")
```

期望：

- 返回工具层错误 JSON。
- 包含 `error` 和 `code=40001`。

## 用例 6：回退外采

切换：

```bash
export PATENT_SEARCH_USE_SELF_HOSTED=false
```

重新调用 `patent_search`。

期望：

- 调用路径回到外采 PatentHub。
- 自研服务故障不影响工具层回退。

