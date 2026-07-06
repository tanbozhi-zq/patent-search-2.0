# 阶段 10.5 测试验收单

## 自动化测试

必须通过：

```bash
.venv/bin/python -m pytest -q
```

## DSL 验收

### normal 模式

| q | Expected field |
|---|---|
| `mainClaim:(均衡)` | `MainClaim` |
| `claims:(均衡)` | `Requirement` |
| `description:(均衡)` | `Instructions` |

### compat 模式

| q | Expected |
|---|---|
| `mainClaim:(均衡)` | phrase query on `MainClaim` |
| `claims:(均衡)` | phrase query on `Requirement` |
| `description:(均衡)` | phrase query on `Instructions` |

## 组合查询验收

以下查询应可构造 DSL：

```text
mainClaim:("均衡" OR "平衡")
claims:("均衡" OR "平衡")
description:("均衡" OR "平衡")
ipc:H02M AND claims:(均衡)
mainClaim:(电路) AND NOT description:(外观)
```

## 非法语法验收

以下查询必须返回 HTTP 400，code `40001`：

```text
mainClaim:
claims:()
description:(均衡) AND AND ipc:H02M
```

## 回归验收

必须确认：

1. `title` 行为不变。
2. `ab` 行为不变。
3. `tscd` 仍覆盖 `Title`、`Abstract`、`MainClaim`、`Requirement`、`Instructions`。
4. `index_analyzer_mode=compat` 默认值不变。
5. `index_analyzer_mode=normal` 仍可用。

## 真实 OpenSearch Smoke

如环境可用，建议抽样：

```bash
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"claims:(均衡)","page":1,"page_size":1}'
```

Smoke 不要求特定 total，只要求 HTTP 200 且响应结构正确。

## 通过标准

1. 自动化测试通过。
2. 新增字段 DSL 正确。
3. 既有字段不回退。
4. 文档已更新。
5. 未修改 OpenSearch mapping。

