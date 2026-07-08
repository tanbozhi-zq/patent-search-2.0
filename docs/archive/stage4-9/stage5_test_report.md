# 阶段五测试结论

## 1. 总体结论

阶段五测试结论：**通过**

阶段五最小检索链路已跑通，`POST /api/patent/search` 可正常调用，真实 OpenSearch 数据可查询返回，字段映射、分页、排序、参数校验、不支持语法报错均符合 `docs/stage5_test_acceptance.md` 要求，未发现阶段六及以后能力越界。

---

## 2. 测试环境

| 项 | 结果 |
|---|---|
| 测试执行机器 | 本地开发机 |
| 本地 Python 版本 | 3.13.14 |
| 目标服务器 Python 版本 | 3.9.19（代码未使用 3.10+ 语法，兼容） |
| 虚拟环境 | `.venv/` 已创建并激活 |
| 依赖来源 | `requirements.txt` |
| OpenSearch 索引 | `patent_index` |

手动测试前，在本地 `.env` 中临时添加了 `API_TOKEN=test-token` 用于鉴权验证。`.env` 已加入 `.gitignore`，未进入版本控制。

---

## 3. 测试结果明细

### 3.1 自动化测试

执行命令：

```bash
python3 -m pytest -q
```

结果：

```text
20 passed in 0.02s
```

说明：阶段四的 10 个测试继续通过，阶段五新增 10 个测试通过，包括 search API、DSL builder、result mapper、search service。

### 3.2 搜索接口可用性

启动服务后执行：

```bash
python3 scripts/smoke_search.py http://127.0.0.1:8000 test-token
```

结果：

```text
search ok total=510569 records=1
```

`POST /api/patent/search` 可用，能查询到真实 OpenSearch 数据。

### 3.3 最小语法样例

| 语法类型 | 请求 `q` | HTTP 状态 | `total` | 结果 |
|---|---|---|---|---|
| 普通关键词 | `阀门` | 200 | 510569 | 通过 |
| 标题 | `title:(阀门)` | 200 | 77927 | 通过 |
| 摘要 | `ab:(缓冲)` | 200 | 1060005 | 通过 |
| IPC | `ipc:H02M` | 200 | 207492 | 通过 |
| 申请日范围 | `ad:[2020-01-01 TO 2020-12-31]` | 200 | 5892786 | 通过 |
| 无结果 | `title:(asdfghjklzxcvbnm)` | 200 | 0 | 通过，返回 `records: []` |

所有样例均返回包含 `total`、`page`、`page_size`、`records` 的结构。

### 3.4 字段映射结果

对非空记录检查字段，结果如下：

| 字段 | 是否出现 | 说明 |
|---|---|---|
| `id` / `patent_id` | 是 | 专利内部 ID |
| `applicationNumber` | 是 | 申请号 |
| `documentNumber` | 是 | 公开号 |
| `title` / `ti` | 是 | 标题及别名 |
| `abstract` / `ab` | 是 | 摘要及别名 |
| `applicant` / `pa` | 是 | 申请人及别名 |
| `currentAssignee` | 是 | 当前权利人，缺省时回退到 `Applicant` |
| `inventor` | 是 | 发明人 |
| `mainIpc` | 是 | 主 IPC |
| `ipcMainList` | 是 | 数组类型 |
| `applicationDate` / `ad` | 是 | 申请日及别名 |
| `documentDate` | 是 | 公开日 |
| `legalStatus` | 是 | 法律状态，来自 `LatestLegalStatus` 或 `LegalStatus` |
| `currentStatus` | 是 | 当前状态 |
| `type` | 是 | 专利类型 |
| `score` | 是 | `_score`，按申请日排序时为 `null` |

空值规则：

- 字符串缺失时返回 `""`：符合
- 数组缺失时返回 `[]`：符合
- `ipcMainList` 为数组：符合

### 3.5 分页和排序

| 场景 | 请求 | 结果 |
|---|---|---|
| 分页 | `page=2, page_size=2` | 返回第 2 页 2 条记录，`page=2` 正确 |
| 申请日倒序 | `sort=!applicationDate` | 返回记录按申请日倒序，最新申请日在先，`score=null` |
| 数据范围 `cn` | `ds=cn` | total=510569 |
| 数据范围 `all` | `ds=all` | total=698979，包含非中国专利 |

### 3.6 参数校验

| 场景 | HTTP 状态 | 说明 |
|---|---|---|
| `page=0` | 422 | `page` 必须 >= 1 |
| `page_size=101` | 422 | `page_size` 必须 <= 100 |
| `ds=invalid` | 422 | `ds` 只能是 `cn` 或 `all` |
| `sort=invalid` | 422 | `sort` 只能是 `relation` 或 `!applicationDate` |
| 缺少 `q` | 422 | `q` 必填 |

参数校验均生效，错误提示清晰。当前参数校验返回 FastAPI 默认 422 结构，未统一包装成 `success/code/message/data`，此行为在阶段五验收范围内可接受。

### 3.7 不支持语法

| 场景 | 请求 `q` | HTTP 状态 | 错误码 | 结果 |
|---|---|---|---|---|
| `tscd` | `tscd:(均衡)` | 400 | 40001 | 明确提示“暂不支持该语法” |
| `AND` 组合 | `ipc:H02M AND tscd:(均衡)` | 400 | 40001 | 明确提示“暂不支持该语法” |

错误结构：

```json
{
  "success": false,
  "code": 40001,
  "message": "q 查询语法错误：暂不支持该语法",
  "data": null
}
```

符合 `docs/api_spec.md` 通用失败响应结构。

### 3.8 鉴权

| 场景 | HTTP 状态 | 结果 |
|---|---|---|
| 正确 `X-API-Key` | 200 | 通过 |
| 缺失 `X-API-Key` | 401 | 返回统一错误结构 |
| 错误 `X-API-Key` | 401 | 返回统一错误结构 |

### 3.9 阶段边界检查

执行命令：

```bash
rg -n "tscd:|applicant:|currentAssignee:|legalStatus:|documentYear:|GET /api/patent/detail|GET /api/patent/citations" app tests
```

结果：

- `tscd:` 仅出现在 `tests/test_search_dsl_builder.py` 的不支持语法测试，以及 `app/query/dsl_builder.py` 的拒绝逻辑中，属于阶段五允许范围。
- 未发现 `applicant:`、`currentAssignee:`、`legalStatus:`、`documentYear:` 的真实实现。
- 未发现 `GET /api/patent/detail` 和 `GET /api/patent/citations` 的真实业务逻辑。

阶段边界检查通过。

---

## 4. 阻塞问题

无。

---

## 5. 非阻塞建议

1. **参数校验错误结构统一**：当前参数非法时返回 FastAPI 默认 422 结构（`detail` 数组），与 `docs/api_spec.md` 中统一的 `success/code/message/data` 错误结构不一致。建议在阶段六或阶段八补齐统一异常处理，将参数校验错误也包装成统一结构。

2. **数组字段字符串化问题**：部分 OpenSearch 字段（如 `Applicant`、`Inventor`）在源数据中可能是数组，当前 `_string()` 直接 `str(value)`，导致返回 `"['某某公司']"` 这种 Python 列表字符串。建议在字段映射层对数组做拼接处理（如用分号连接），提高字段可读性。

3. **`ds=all` 非中国专利字段缺失**：`ds=all` 查询返回的部分非中国专利存在 `type`、`legalStatus`、`mainIpc` 等字段为空的情况，这是数据本身的覆盖度问题，不是服务缺陷。建议后续在测试报告中说明该现象，避免业务方误解。

4. **测试配置依赖 `.env`**：`tests/test_config.py` 直接实例化 `Settings()`，会读取本地 `.env`。如果 `.env` 值与默认值不同，测试可能不稳定。建议后续改为显式传参测试。

---

## 6. 是否建议进入项目总控审查

**是**

阶段五最小检索链路已跑通，核心功能、字段映射、分页排序、异常处理均符合阶段五验收标准，未发现阻塞性问题。建议提交项目总控审查，审查通过后可进入阶段六（核心查询能力补齐）开发。
