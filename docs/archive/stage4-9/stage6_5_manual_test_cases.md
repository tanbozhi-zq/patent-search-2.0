# 阶段 6.5 手动测试用例

> 适用地址：`http://127.0.0.1:8000/test/` 或 curl
>
> 前置条件：后端服务已启动，`.env` 中已配置 `API_TOKEN=test-token`

## 背景

阶段 6.5 新增了 `index_analyzer_mode` 参数：

- `compat`（默认）：对问题字段使用 phrase 查询，降低误召回
- `normal`：保留阶段六普通 `multi_match` 查询

问题字段包括：`TitleCN`、`AbstractCN`、`MainClaim`、`Requirement`、`Instructions`、`Type`。

---

## 一、默认模式验证（compat）

打开测试页面 `http://127.0.0.1:8000/test/`，API Token 填 `test-token`，其他默认，只改 `q`。

| 编号 | q | 预期结果 |
|---|---|---|
| TC-01 | `tscd:(口腔数字印模仪图像采集方法)` | HTTP 200，`total` > 0 |
| TC-02 | `tscd:(图像采集方法)` | HTTP 200，`total` > 0 |
| TC-03 | `title:(口腔数字印模仪)` | HTTP 200，`total` > 0 |
| TC-04 | `ab:(药物组合物)` | HTTP 200，`total` > 0 |
| TC-05 | `type:(发明专利)` | HTTP 200，`total` > 0 |

说明：由于页面默认不传 `index_analyzer_mode`，后端使用默认 `compat`。

---

## 二、normal 与 compat 对比

测试页面已提供 `Analyzer 模式` 下拉框，可以直接在页面上切换。

### 页面操作方式

1. 打开 `http://127.0.0.1:8000/test/`
2. q 填入：`tscd:(口腔数字印模仪图像采集方法)`
3. `Analyzer 模式` 先选 `normal`，点击搜索，记录 `total`
4. `Analyzer 模式` 再选 `compat`，点击搜索，记录 `total`
5. 对比两次 `total`

**通过标准：** compat 模式的 `total` 小于 normal 模式。

### curl 方式（可选）

如果更喜欢命令行，也可以用 curl：

```bash
# normal 模式
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-token" \
  -d '{"q":"tscd:(口腔数字印模仪图像采集方法)","index_analyzer_mode":"normal","page":1,"page_size":1}' | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])"

# compat 模式
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-token" \
  -d '{"q":"tscd:(口腔数字印模仪图像采集方法)","index_analyzer_mode":"compat","page":1,"page_size":1}' | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])"
```

### 建议对比的查询

| q | normal total | compat total | 说明 |
|---|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` | 较大 | 较小 | compat 误召回降低明显 |
| `tscd:(图像采集方法)` | 较大 | 较小 | compat 误召回降低明显 |
| `title:(口腔数字印模仪)` | 较大 | 较小 | compat 误召回降低 |
| `ab:(药物组合物)` | 较大 | 较小或持平 | 取决于具体数据 |
| `type:(发明专利)` | 相同 | 相同 | type 的 normal 字段为 keyword，实际由 phrase 字段贡献 |

---

## 三、阶段六回归（compat 默认模式下）

| 编号 | q | 预期结果 |
|---|---|---|
| TC-06 | `ipc:H02M AND tscd:(均衡)` | HTTP 200 |
| TC-07 | `NOT title:(外观)` | HTTP 200 |
| TC-08 | `applicant:(华为技术有限公司)` | HTTP 200 |
| TC-09 | `currentAssignee:(华为技术有限公司)` | HTTP 200 |
| TC-10 | `legalStatus:(有效专利)` | HTTP 200 |
| TC-11 | `documentYear:[2020 TO 2024]` | HTTP 200 |
| TC-12 | `tscd:("均衡" OR "平衡")` | HTTP 200 |
| TC-13 | `foo:(均衡)` | HTTP 400，code=40001 |
| TC-14 | `ad:[2020-13-01 TO 2020-12-31]` | HTTP 400，code=40001 |

---

## 四、参数非法

| 编号 | 调整项 | 预期结果 |
|---|---|---|
| TC-15 | `index_analyzer_mode=fast` | HTTP 422，提示仅支持 compat/normal |
| TC-16 | `index_analyzer_mode` 不传 | 默认 compat，HTTP 200 |

---

## 五、一键运行冒烟脚本

```bash
source .venv/bin/activate
python3 scripts/smoke_analyzer_compat.py http://127.0.0.1:8000 test-token
```

预期输出中所有查询的 `normal_status` 和 `compat_status` 均为 200，且典型问题查询的 `compat_total` 小于 `normal_total`。

---

## 测试结论模板

```text
阶段 6.5 手动测试结论：通过 / 不通过

compat 默认模式用例通过数：__/5
normal/compat 对比通过数：__/4
阶段六回归通过数：__/9
参数非法通过数：__/2

失败用例：
1. TC-XX：问题描述
2. TC-XX：问题描述

是否建议进入阶段七：是 / 否
```
