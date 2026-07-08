# 专利检索 MCP 参数与字段依赖

> `patent_search` MCP 接口参数共 6 个: `q`, `ds`, `sort`, `page`, `page_size`, `highlight`

---

## 一、MCP 参数传送值

### 1.1 `q` — 查询式（按语法模式分类）

| 语法模式 | `q` 构造示例 | 用途 |
|---------|-------------|------|
| 申请人 | `currentAssignee:(华为技术有限公司)` | 按企业实体检索 |
| 申请人 多实体 OR | `currentAssignee:(华为技术有限公司) OR currentAssignee:(杭州华为数字技术有限公司)` | 多变体合并 |
| IPC + 术语(tscd) | `ipc:H02M AND tscd:("均衡" OR "平衡")` | IPC 范围内关键词 |
| IPC + 术语 中英文 | `ipc:H02M AND tscd:("均衡" OR "平衡")` / `ipc:H02M AND tscd:("balancing")` | 中英文双通道 |
| IPC + 对偶词 | `ipc:F15B AND tscd:("主线圈" OR "副线圈")` | 对偶检索 |
| IPC + 设备名锚 | `tscd:("电液比例阀") AND tscd:("主线圈" OR "副线圈")` | 设备名限定 |
| 纯 IPC（全代码精确） | `ipc:H02M7/483` | IPC 精确兜底 |
| IPC + 设备名称 | `ipc:F16K AND tscd:("比例换向阀" OR "比例阀")` | 设备词 anchor |
| IPC + 日期范围 | `ad:[2020-01-01 TO 2020-12-31]` | 按年分区 |
| IPC + 日期 + 法律状态 | `ad:[2020-01-01 TO 2020-12-31] AND legalStatus:(有效专利 OR 实质审查 OR 公开)` | 年度有效+在审统计 |
| IPC + 法律状态 | `legalStatus:(有效专利)` | 法律状态分类统计 |
| 纯 IPC | `ipc:F16K` | IPC 大类/小类/全代码兜底 |

### 1.2 `ds` — 数据范围

| 值 | 说明 |
|----|------|
| `"cn"` | 仅中国专利 |
| `"all"` | 全球专利 |

### 1.3 `sort` — 排序

| 值 | 说明 |
|----|------|
| `"relation"` | 按相关性排序（默认） |
| `"!applicationDate"` | 申请日倒序 |

### 1.4 `page` + `page_size` — 分页

| 模式 | `page` | `page_size` | 说明 |
|------|--------|-------------|------|
| 精确计数 | `1` | `1` | 只取 total，不拉数据 |
| 翻页 | `1..N` | `50` | 自动翻页直到无数据或满上限 |
| 单次 | `1` | `50` | 单次调用，不翻页 |
| 单次大页 | `1` | `50` | max_patents=200 实质是 4 页合计 |

### 1.5 `highlight`

| 值 | 说明 |
|----|------|
| `0` | 不高亮（默认） |
| `1` | 高亮匹配词 |

---

## 二、专利详情相关 MCP

### 2.1 `patent_get_detail`

| 参数 | 值 |
|------|----|
| `patent_id` | 从 search 结果获取 |
| `include_description` | `true` / `false` |

### 2.2 `patent_get_citations`

| 参数 | 值 |
|------|----|
| `patent_id` | 从 search 结果获取 |

---

## 三、`patent_search` 返回字段消费

### 3.1 返回记录字段（按应用维度分类）

| 字段 | 用于 |
|------|------|
| **标识字段** | |
| `id` / `patent_id` | 去重键、详情引用、评估标识 |
| `applicationNumber` | 去重键 |
| `documentNumber` | 公开号展示 |
| **文本字段** | |
| `title` / `ti` | 短标题扫描（≤15字判定+关键词命中）、评分维度(s_term)、评估主题 |
| `abstract` / `ab` | 功效矩阵输入、威胁评分维度(s_summary) |
| **权利人字段** | |
| `applicant` / `pa` | 实体归属验证、报告展示 |
| `currentAssignee` | 实体归属、红旗检测（单实体>500件或>30%预警） |
| `inventor` | 核心发明人反向提取 |
| **分类字段** | |
| `mainIpc` | IPC 主分类（评分/配额/Gate/统计分布分母） |
| `ipcMainList` | IPC 全列表（ipc_main 字段来源） |
| **日期字段** | |
| `applicationDate` / `ad` | 年度趋势统计、报告展示 |
| `documentDate` | 趋势参考、报告展示 |
| **法律字段** | |
| `legalStatus` | 统计口径3分类(有效/在审/失效)、报告展示 |
| `currentStatus` | 状态细分统计 |
| **类型字段** | |
| `type` | 专利类型3分类(发明/实用/外观)、Gate 禁止排除实用新型 |

### 3.2 返回 metadata 字段

| 字段 | 用于 |
|------|------|
| `total` | 翻页终止判定、count 统计、覆盖率校验 |

---

## 四、`q` 语法运算符清单

| 运算符 | 示例 | 说明 |
|--------|------|------|
| `currentAssignee:(...)` | `currentAssignee:(华为)` | 当前权利人精确匹配 |
| `applicant:(...)` | `applicant:(华为)` | 申请人匹配 |
| `ipc:(...)` | `ipc:H02M` | IPC 分类（大类/小类/主组/小组） |
| `ipc:{code}` | `ipc:H02M7/483` | IPC 精确到小组 |
| `tscd:(...)` | `tscd:("均衡" OR "平衡")` | 标题+摘要+权利要求+说明书全文 |
| `title:(...)` | `title:("阀门")` | 标题字段 |
| `ab:(...)` | `ab:("缓冲")` | 摘要字段 |
| `ad:[... TO ...]` | `ad:[2020-01-01 TO 2020-12-31]` | 申请日范围 |
| `documentYear:[... TO ...]` | `documentYear:[2020 TO 2024]` | 公开年范围 |
| `legalStatus:(...)` | `legalStatus:(有效专利)` | 法律状态 |
| `type:(...)` | `type:发明授权` | 专利类型 |
| `AND` | `A AND B` | 交集 |
| `OR` | `A OR B` | 并集 |
| `NOT` | `A NOT B` | 排除 |
| `""` | `"电液比例阀"` | 短语精确匹配 |
| `()` | `(A OR B) AND C` | 分组 |
