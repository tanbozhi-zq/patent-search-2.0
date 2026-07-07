# 阶段五手动测试用例

> 适用页面：`http://127.0.0.1:8000/test/`
>
> 前置条件：后端服务已启动，`.env` 中已配置 `API_TOKEN`

---

## 通用填写说明

打开测试页面后，大部分用例只需要改 **查询式 q** 和 **页码/每页条数** 等字段，其他字段保持默认即可。

默认参数：

- `ds`: `cn`
- `sort`: `relation`
- `page`: `1`
- `page_size`: `10`
- `highlight`: `0`

API Token 必须填写（如果后端 `ENABLE_AUTH=true`）。

---

## 一、正向查询用例

### 用例 1：普通关键词查询

| 项 | 值 |
|---|---|
| API Token | 你的 token |
| q | `阀门` |
| ds | `cn` |
| sort | `relation` |
| page | `1` |
| page_size | `10` |

**预期结果：**

- HTTP 状态 200
- `total` 大于 0（约 50 万以上）
- `records` 数组非空
- 第一条记录包含 `patent_id`、`title`、`abstract`、`applicant`、`currentAssignee`、`mainIpc`、`ipcMainList`、`applicationDate`、`documentDate`、`legalStatus`、`type`、`score`
- `title` 和 `ti` 内容一致，`abstract` 和 `ab` 内容一致

**通过标准：** 返回结果非空，字段完整。

---

### 用例 2：标题检索

| 项 | 值 |
|---|---|
| q | `title:(阀门)` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- `total` 大于 0
- 返回结果的 `title` 字段包含“阀门”或相关标题

**通过标准：** 返回结果比用例 1 更聚焦（`total` 应小于用例 1）。

---

### 用例 3：摘要检索

| 项 | 值 |
|---|---|
| q | `ab:(缓冲)` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- `total` 大于 0
- 返回结果的 `abstract` 字段包含“缓冲”相关内容

**通过标准：** 能返回结果，字段完整。

---

### 用例 4：IPC 检索

| 项 | 值 |
|---|---|
| q | `ipc:H02M` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- `total` 大于 0（约 20 万以上）
- 返回结果的 `mainIpc` 或 `ipcMainList` 包含 `H02M`

**通过标准：** 结果与 IPC 分类相关。

---

### 用例 5：申请日范围检索

| 项 | 值 |
|---|---|
| q | `ad:[2020-01-01 TO 2020-12-31]` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- `total` 大于 0（约 500 万以上）
- 返回结果的 `applicationDate` 在 2020 年内

**通过标准：** 返回记录的 `applicationDate` 符合范围。

---

### 用例 6：无结果查询

| 项 | 值 |
|---|---|
| q | `title:(asdfghjklzxcvbnm)` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- `total` 等于 0
- `records` 为空数组 `[]`

**通过标准：** 正常返回，不报错。

---

## 二、参数与边界用例

### 用例 7：分页

| 项 | 值 |
|---|---|
| q | `阀门` |
| page | `2` |
| page_size | `2` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- `page` 等于 2
- `page_size` 等于 2
- `records` 长度为 2
- 返回的专利与 `page=1, page_size=2` 时不同

**通过标准：** 分页生效，结果不重复。

---

### 用例 8：申请日倒序排序

| 项 | 值 |
|---|---|
| q | `阀门` |
| sort | `!applicationDate` |
| page_size | `3` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- 返回记录按 `applicationDate` 从近到远排列
- `score` 字段为 `null`

**通过标准：** 第一条记录的申请日最晚。

---

### 用例 9：数据范围 `ds=all`

| 项 | 值 |
|---|---|
| q | `阀门` |
| ds | `all` |
| page_size | `3` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- `total` 大于 `ds=cn` 时的结果
- 可能返回非中国专利（如 `Country` 非 CN，但接口不返回 `Country` 字段）

**通过标准：** `all` 返回数量多于 `cn`。

---

### 用例 10：`page_size` 上限

| 项 | 值 |
|---|---|
| q | `阀门` |
| page_size | `100` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- `records` 长度不超过 100

**通过标准：** 返回条数符合限制。

---

## 三、异常用例

### 用例 11：`page` 非法

| 项 | 值 |
|---|---|
| q | `阀门` |
| page | `0` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 422
- 错误提示包含 `page` 必须大于等于 1

**通过标准：** 明确返回参数错误，不崩溃。

---

### 用例 12：`page_size` 超限

| 项 | 值 |
|---|---|
| q | `阀门` |
| page_size | `101` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 422
- 错误提示包含 `page_size` 必须小于等于 100

**通过标准：** 明确返回参数错误。

---

### 用例 13：`ds` 非法

| 项 | 值 |
|---|---|
| q | `阀门` |
| ds | `invalid` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 422
- 错误提示包含 `ds` 必须是 `cn` 或 `all`

**通过标准：** 明确返回参数错误。

---

### 用例 14：`sort` 非法

| 项 | 值 |
|---|---|
| q | `阀门` |
| sort | `invalid` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 422
- 错误提示包含 `sort` 必须是 `relation` 或 `!applicationDate`

**通过标准：** 明确返回参数错误。

---

### 用例 15：缺少 `q`

| 项 | 值 |
|---|---|
| q | 留空 |
| 其他 | 默认 |

**预期结果：**

- 页面提示“查询式 q 不能为空”，或
- 发送后 HTTP 状态 422，提示 `Field required`

**通过标准：** 给出明确错误提示。

---

### 用例 16：不支持语法 `tscd`

| 项 | 值 |
|---|---|
| q | `tscd:(均衡)` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 400
- 错误结构包含：
  ```json
  {
    "success": false,
    "code": 40001,
    "message": "q 查询语法错误：暂不支持该语法",
    "data": null
  }
  ```

**通过标准：** 返回统一错误结构，明确说明不支持。

---

### 用例 17：不支持语法 `AND` 组合

| 项 | 值 |
|---|---|
| q | `ipc:H02M AND tscd:(均衡)` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 400
- 错误码 40001
- 提示暂不支持该语法

**通过标准：** 明确返回不支持。

---

## 四、鉴权用例

### 用例 18：正确 Token

| 项 | 值 |
|---|---|
| API Token | 正确的 token |
| q | `阀门` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 200
- 正常返回结果

**通过标准：** 鉴权通过。

---

### 用例 19：缺失 Token

| 项 | 值 |
|---|---|
| API Token | 留空 |
| q | `阀门` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 401
- 错误结构包含：
  ```json
  {
    "success": false,
    "code": 40101,
    "message": "missing or invalid X-API-Key",
    "data": null
  }
  ```

**通过标准：** 明确返回鉴权失败。

---

### 用例 20：错误 Token

| 项 | 值 |
|---|---|
| API Token | `wrong-token` |
| q | `阀门` |
| 其他 | 默认 |

**预期结果：**

- HTTP 状态 401
- 错误码 40101

**通过标准：** 鉴权失败，不返回数据。

---

## 五、字段检查清单

每条非空返回记录，检查以下字段是否存在且类型正确：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` / `patent_id` | string | 专利内部 ID |
| `applicationNumber` | string | 申请号 |
| `documentNumber` | string | 公开号 |
| `title` / `ti` | string | 标题 |
| `abstract` / `ab` | string | 摘要 |
| `applicant` / `pa` | string | 申请人 |
| `currentAssignee` | string | 当前权利人 |
| `inventor` | string | 发明人 |
| `mainIpc` | string | 主 IPC |
| `ipcMainList` | array | IPC 列表 |
| `applicationDate` / `ad` | string | 申请日 |
| `documentDate` | string | 公开日 |
| `legalStatus` | string | 法律状态 |
| `currentStatus` | string | 当前状态 |
| `type` | string | 专利类型 |
| `score` | number / null | 相关度分数 |

---

## 六、测试结论模板

测试完成后，可以记录：

```text
阶段五手动测试结论：通过 / 不通过

通过用例数：
失败用例数：

失败用例详情：
1. 用例编号：
   问题描述：
   是否阻塞：

2. 用例编号：
   问题描述：
   是否阻塞：

建议进入阶段六：是 / 否
```
