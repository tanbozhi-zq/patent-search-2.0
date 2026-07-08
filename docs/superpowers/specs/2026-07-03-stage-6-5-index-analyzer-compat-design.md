# 阶段 6.5 索引分词缺陷兼容检索设计

## 1. 背景

阶段六已完成完整布尔查询解析器，并通过自动化测试和真实 OpenSearch 冒烟。随后发现 `patent_index` 存在索引分词器缺陷：大量中文、日文及部分非空格语言字段没有使用适合的 analyzer，导致普通 `match` / `multi_match` 查询在问题字段上误召回或召回不足。

已验证事实：

1. 当前 `patent_index` 自身没有定义自定义 `index.analysis`。
2. 字段实际 analyzer 分布为：`ik_max_word` 6 个字段、显式 `standard` 16 个字段、默认 `standard` 42 个 text 字段。
3. `Title`、`Abstract`、`Applicant`、`Assignee`、`Inventor`、`Agent` 使用 `ik_max_word`。
4. `MainClaim`、`Requirement`、`Instructions`、`TitleCN`、`AbstractCN`、`Type` 等字段对中文会产生单字 token。
5. `match_phrase` 可显著降低问题字段的误召回。真实查询验证：

```text
Instructions match        9044992
Instructions match_phrase 2

Requirement match         8223464
Requirement match_phrase 2

MainClaim match           41634784
MainClaim match_phrase    2
```

阶段 6.5 不重建索引，只在检索服务侧增加兼容模式，降低当前索引缺陷对 SaaS 检索效果的影响。

## 2. 目标

阶段 6.5 目标：

1. 新增接口参数 `index_analyzer_mode`，用于控制当前索引分词缺陷兼容策略。
2. 默认启用兼容模式，避免线上继续被问题字段误召回拖垮。
3. 在兼容模式下，对已确认存在 analyzer 风险的字段增加 `match_phrase` 查询。
4. 保留原普通匹配逻辑，避免召回过窄。
5. 保持阶段六语法、错误处理、分页、排序、鉴权和返回结构不变。

## 3. 非目标

阶段 6.5 不做：

1. 不重建 OpenSearch 索引。
2. 不修改 `patent_index` mapping。
3. 不改数据处理入库流程。
4. 不引入向量检索。
5. 不做语言自动识别。
6. 不做复杂召回排序学习。
7. 不做专利详情接口。
8. 不做 SaaS 联调。

## 4. 接口设计

`POST /api/patent/search` 请求体新增参数：

```json
{
  "q": "tscd:(口腔数字印模仪图像采集方法)",
  "index_analyzer_mode": "compat"
}
```

取值：

| 值 | 含义 |
|---|---|
| `compat` | 默认值。当前索引 analyzer 未修复时使用，对问题字段增加 phrase 兼容查询。 |
| `normal` | 保留阶段六普通匹配逻辑，供对比测试和未来新索引切换后使用。 |

参数校验：

1. 只允许 `compat` 和 `normal`。
2. 默认值为 `compat`。
3. 该参数不改变 `q` 语法，仅改变 DSL 构造策略。

## 5. 字段分组

### 5.1 正常字段

以下字段已确认或当前可接受使用普通 `match` / `multi_match`：

```text
Title
Abstract
Applicant
Assignee
ApplicantNormalized
FirstApplicant
AssigneeNormalized
TitleEN
AbstractEN
PatentTypeCode
Kind
```

说明：

1. `Title`、`Abstract`、`Applicant`、`Assignee` 使用 `ik_max_word`。
2. `ApplicantNormalized`、`FirstApplicant`、`AssigneeNormalized` 多为规范化名称，继续使用普通匹配。
3. `TitleEN`、`AbstractEN` 对英文基础可用，阶段 6.5 暂不作为问题字段处理。
4. `PatentTypeCode`、`Kind` 是代码或类型码字段，不走 phrase。

### 5.2 问题字段

以下字段在 `compat` 模式下需要增加 `match_phrase`：

```text
TitleCN
AbstractCN
MainClaim
Requirement
Instructions
Type
```

说明：

1. `TitleCN`、`AbstractCN` 当前为 `standard`，中文会单字切分。
2. `MainClaim` 当前为 `standard`，中文会单字切分。
3. `Requirement`、`Instructions` 未显式 analyzer，实际走默认 `standard`。
4. `Type` 未显式 analyzer，`type:(发明专利)` 应避免被拆成单字弱匹配。

### 5.3 后续观察字段

以下字段不纳入阶段 6.5 查询改造，但记录为后续索引审计对象：

```text
TitleOriginal
AbstractOriginal
IndependentClaimsCN
DependentClaimsCN
ReferencesCitedRaw
ReferencesCitedText
RelatedObligee
Agency
Examiner
地址、省、市、区县类 text 字段
```

这些字段当前不属于阶段六主要查询字段，暂不扩大本阶段改造范围。

## 6. DSL 设计

### 6.1 `normal` 模式

`normal` 模式保持阶段六行为：

```json
{
  "multi_match": {
    "query": "口腔数字印模仪图像采集方法",
    "fields": ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"]
  }
}
```

### 6.2 `compat` 模式

`compat` 模式将字段拆为正常字段和问题字段：

```json
{
  "bool": {
    "should": [
      {
        "multi_match": {
          "query": "口腔数字印模仪图像采集方法",
          "fields": ["Title", "Abstract"]
        }
      },
      {
        "multi_match": {
          "query": "口腔数字印模仪图像采集方法",
          "fields": ["MainClaim", "Requirement", "Instructions"],
          "type": "phrase"
        }
      }
    ],
    "minimum_should_match": 1
  }
}
```

原则：

1. 正常字段保留普通匹配，保障基础召回。
2. 问题字段使用 phrase 查询，降低单字误召回。
3. 组合表达式中的 `AND`、`OR`、`NOT` 结构保持不变，只替换叶子字段查询的构造方式。
4. `match_phrase` 或 `multi_match type=phrase` 均可使用；同一批字段优先使用 `multi_match type=phrase`，单字段可使用 `match_phrase`。

### 6.3 字段级策略

| q 字段 | `normal` | `compat` |
|---|---|---|
| plain keyword | `Title`, `Abstract` 普通 `multi_match` | 不变 |
| `title` | `Title`, `TitleCN`, `TitleEN` 普通 `multi_match` | `Title`, `TitleEN` 普通匹配 + `TitleCN` phrase |
| `ab` | `Abstract`, `AbstractCN`, `AbstractEN` 普通 `multi_match` | `Abstract`, `AbstractEN` 普通匹配 + `AbstractCN` phrase |
| `tscd` | `Title`, `Abstract`, `MainClaim`, `Requirement`, `Instructions` 普通 `multi_match` | `Title`, `Abstract` 普通匹配 + `MainClaim`, `Requirement`, `Instructions` phrase |
| `type` | `Type`, `PatentTypeCode`, `Kind` 普通 `multi_match` | `Type` phrase + `PatentTypeCode`, `Kind` 原有匹配 |
| `applicant` | 普通 `multi_match` | 不变 |
| `currentAssignee` | 普通 `multi_match` | 不变 |
| `legalStatus` | 法律状态映射 | 不变 |
| `ipc` | IPC 字段组合 | 不变 |
| `ad` / `documentYear` | range | 不变 |

### 6.4 引号短语

阶段六已支持 `"xxx"` 语法，但当前实现中 `PhraseNode` 仍会走普通 `multi_match`。阶段 6.5 要把引号短语落到真正的 phrase 查询：

1. `title:("口腔数字印模仪")` 在 `compat` 模式下对 `TitleCN` 使用 phrase。
2. `tscd:("口腔数字印模仪")` 在 `compat` 模式下对 `MainClaim`、`Requirement`、`Instructions` 使用 phrase。
3. `normal` 模式保持阶段六行为，不额外改变显式引号短语的 DSL 构造；后续如需把引号语义统一升级为 phrase，应作为独立优化项处理。

## 7. 排序与权重

阶段 6.5 先不引入复杂 boost 策略。初版规则：

1. 正常字段普通匹配和问题字段 phrase 匹配共同放入 `bool.should`。
2. `minimum_should_match=1`。
3. 保持 OpenSearch 默认相关性排序。
4. 如果测试发现 phrase 命中排序不够靠前，再追加字段 boost，不在第一版直接引入。

## 8. 测试设计

### 8.1 单元测试

新增或扩展测试：

1. `SearchRequest` 接收 `index_analyzer_mode`，默认 `compat`。
2. `index_analyzer_mode=normal` 时 DSL 与阶段六一致。
3. `index_analyzer_mode=compat` 时：
   - `tscd:(口腔数字印模仪图像采集方法)` 对 `MainClaim`、`Requirement`、`Instructions` 生成 phrase 查询。
   - `title:(口腔数字印模仪)` 对 `TitleCN` 生成 phrase 查询。
   - `ab:(口腔数字印模仪)` 对 `AbstractCN` 生成 phrase 查询。
   - `type:(发明专利)` 对 `Type` 生成 phrase 查询，同时保留 `PatentTypeCode`、`Kind` 匹配。
4. `AND`、`OR`、`NOT` 下的字段叶子查询仍正确应用 compat 策略。
5. 阶段六非法输入仍返回 `40001`。

### 8.2 API 测试

扩展 `tests/test_search_api.py`：

1. 不传 `index_analyzer_mode` 时按 `compat` 处理。
2. 传 `normal` 返回 200。
3. 传 `compat` 返回 200。
4. 传非法值返回参数校验错误。

### 8.3 真实 OpenSearch 对比测试

新增阶段 6.5 对比用例，记录 `normal` 与 `compat` 的命中数：

```text
tscd:(口腔数字印模仪图像采集方法)
tscd:(图像采集方法)
title:(口腔数字印模仪)
ab:(药物组合物)
type:(发明专利)
```

验收时至少记录：

| q | normal total | compat total | 预期 |
|---|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` | 大幅偏高 | 显著下降 | compat 降低误召回 |
| `title:(口腔数字印模仪)` | 可对比 | 不异常归零 | compat 保留召回 |
| `type:(发明专利)` | 可对比 | 不异常归零 | compat 可用 |

## 9. 文档更新

阶段 6.5 需要新增或更新：

1. `docs/stage6_5_dev_assignment.md`
2. `docs/stage6_5_test_acceptance.md`
3. `docs/stage6_5_test_report.md`
4. `docs/query_syntax.md`：补充 `index_analyzer_mode`。
5. `docs/api_spec.md`：补充请求参数。

## 10. 风险与控制

| 风险 | 控制 |
|---|---|
| phrase 查询导致召回过窄 | 保留正常字段普通匹配，问题字段才 phrase |
| DSL 复杂度上升 | 只改字段叶子查询构造，不改 Parser/AST |
| 排序变化不可控 | 初版不引入 boost，先用对比测试观察 |
| SaaS 不理解新参数 | 默认 `compat`，SaaS 可不传 |
| 未来索引修复后行为需要切换 | 保留 `normal`，后续可把默认值切回 `normal` |
| 参数名暴露内部实现 | 使用 `index_analyzer_mode`，不使用 `bug` 等临时命名 |

## 11. 验收标准

阶段 6.5 通过需满足：

1. 自动化测试全部通过。
2. 阶段六合法查询不回退。
3. 阶段六非法查询仍返回 `40001`。
4. 默认 `index_analyzer_mode=compat`。
5. `index_analyzer_mode=normal` 保留阶段六普通匹配逻辑。
6. `compat` 模式下问题字段生成 phrase 查询。
7. 真实 OpenSearch 对比测试显示典型问题查询的误召回明显下降。
8. 不修改 OpenSearch 索引，不重建索引。
9. 不实现阶段七详情接口。

## 12. 项目节奏

阶段七暂停，先插入阶段 6.5：

```text
阶段六完成
  -> 阶段 6.5 设计
  -> 阶段 6.5 实施计划
  -> 阶段 6.5 开发
  -> 阶段 6.5 测试验收
  -> 阶段七详情接口
```

阶段 6.5 完成前，不进入阶段七开发。
