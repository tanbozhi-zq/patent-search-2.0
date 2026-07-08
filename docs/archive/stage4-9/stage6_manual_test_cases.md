# 阶段六手动测试用例

> 适用页面：`http://127.0.0.1:8000/test/`
>
> 前置条件：后端服务已启动，`.env` 中已配置 `API_TOKEN=test-token`

## 默认参数

未特别说明时保持默认：

- `ds`: `cn`
- `sort`: `relation`
- `page`: `1`
- `page_size`: `10`
- `highlight`: `0`
- `API Token`: `test-token`

---

## 一、阶段五回归（确保未回退）

| 编号 | 名称 | q | 预期结果 |
|---|---|---|---|
| TC-01 | 普通关键词 | `阀门` | HTTP 200，`total` > 0，字段完整 |
| TC-02 | 标题 | `title:(阀门)` | HTTP 200，`total` > 0 |
| TC-03 | 摘要 | `ab:(缓冲)` | HTTP 200，`total` > 0 |
| TC-04 | IPC | `ipc:H02M` | HTTP 200，`total` > 0 |
| TC-05 | 申请日范围 | `ad:[2020-01-01 TO 2020-12-31]` | HTTP 200，`total` > 0 |

---

## 二、阶段六新增合法查询

| 编号 | 名称 | q | 预期结果 |
|---|---|---|---|
| TC-06 | 全文检索 | `tscd:(均衡)` | HTTP 200，`total` > 0 |
| TC-07 | 全文 OR 短语 | `tscd:("均衡" OR "平衡")` | HTTP 200，`total` > 0 |
| TC-08 | IPC + 全文 | `ipc:H02M AND tscd:(均衡)` | HTTP 200，`total` > 0 |
| TC-09 | 申请人 | `applicant:(华为技术有限公司)` | HTTP 200，`total` > 0 |
| TC-10 | 当前权利人 | `currentAssignee:(华为技术有限公司)` | HTTP 200，`total` > 0 |
| TC-11 | 法律状态 | `legalStatus:(有效专利)` | HTTP 200，`total` > 0 |
| TC-12 | 专利类型（修复后） | `type:(发明专利)` | HTTP 200，`total` > 0 |
| TC-13 | 公开年范围 | `documentYear:[2020 TO 2024]` | HTTP 200，`total` > 0 |
| TC-14 | 简单 OR | `title:(均衡) OR title:(平衡)` | HTTP 200，`total` > 0 |
| TC-15 | 括号分组 | `(title:(均衡) OR title:(平衡)) AND ipc:H02M` | HTTP 200，`total` > 0 |
| TC-16 | NOT 排除 | `NOT title:(外观)` | HTTP 200，`total` > 0 |
| TC-17 | AND + NOT | `ipc:H02M AND NOT tscd:(均衡)` | HTTP 200，`total` > 0 |

---

## 三、阶段六非法查询（必须返回 40001）

| 编号 | 名称 | q | 预期结果 |
|---|---|---|---|
| TC-18 | 双 AND | `ipc:H02M AND AND tscd:(均衡)` | HTTP 400，code=40001 |
| TC-19 | 开头 AND | `AND tscd:(均衡)` | HTTP 400，code=40001 |
| TC-20 | 结尾 OR | `tscd:(均衡) OR` | HTTP 400，code=40001 |
| TC-21 | 引号未闭合 | `tscd:("均衡)` | HTTP 400，code=40001 |
| TC-22 | 空括号 | `tscd:()` | HTTP 400，code=40001 |
| TC-23 | IPC 空值 | `ipc:` | HTTP 400，code=40001 |
| TC-24 | 不支持的字段 | `foo:(均衡)` | HTTP 400，code=40001 |
| TC-25 | 范围缺少 TO | `ad:[2020-01-01 2020-12-31]` | HTTP 400，code=40001 |
| TC-26 | 非法日期 | `ad:[2020-13-01 TO 2020-12-31]` | HTTP 400，code=40001 |
| TC-27 | 日期范围反向 | `ad:[2021-01-01 TO 2020-12-31]` | HTTP 400，code=40001 |
| TC-28 | 公开年反向 | `documentYear:[2024 TO 2020]` | HTTP 400，code=40001 |
| TC-29 | 单独 NOT | `NOT` | HTTP 400，code=40001 |
| TC-30 | NOT 位置错误 | `tscd:(均衡) NOT` | HTTP 400，code=40001 |

---

## 四、参数与边界

| 编号 | 名称 | 调整项 | 预期结果 |
|---|---|---|---|
| TC-31 | 分页 | `page=2`, `page_size=2` | 返回第 2 页 2 条 |
| TC-32 | 申请日倒序 | `sort=!applicationDate` | 按申请日倒序，`score=null` |
| TC-33 | ds=all | `ds=all` | `total` 比 `ds=cn` 大 |
| TC-34 | page_size 上限 | `page_size=100` | 返回 ≤100 条 |
| TC-35 | page 非法 | `page=0` | HTTP 422 |
| TC-36 | page_size 超限 | `page_size=101` | HTTP 422 |

---

## 五、鉴权

| 编号 | 名称 | API Token | 预期结果 |
|---|---|---|---|
| TC-37 | 正确 Token | `test-token` | HTTP 200 |
| TC-38 | Token 缺失 | 留空 | HTTP 401 |
| TC-39 | Token 错误 | `wrong-token` | HTTP 401 |

---

## 六、字段检查

任意非空返回记录检查是否包含：

```text
id / patent_id
applicationNumber
documentNumber
title / ti
abstract / ab
applicant / pa
currentAssignee
inventor
mainIpc
ipcMainList
applicationDate / ad
documentDate
legalStatus
currentStatus
type
score
```

---

## 测试结论模板

```text
阶段六手动测试结论：通过 / 不通过

通过用例数：__/39
失败用例数：__

失败用例：
1. TC-XX：问题描述
2. TC-XX：问题描述

是否建议进入项目总控审查：是 / 否
```
