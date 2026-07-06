# 阶段 10.5 开发派工单

## 角色

- 项目总控：维护本次增强边界、字段映射、查询语法清单、Git 状态和交付文档。
- 开发人员：按设计文档实现新增查询字段映射、DSL 构造和测试。
- 测试人员：验证新增字段查询、兼容模式、非法语法、既有查询不回退。

## 开发目标

在不修改 OpenSearch mapping 的前提下，扩展 `q` 查询语法，新增细粒度文本字段检索：

```text
mainClaim
claims
description
```

## 开发任务

1. 更新 `app/mappings/query_field_mapping.py`：
   - `mainClaim -> MainClaim`
   - `claims -> Requirement`
   - `description -> Instructions`
2. 更新 analyzer 兼容字段分组：
   - 新增字段在 `compat` 模式下使用 phrase 查询。
   - 新增字段在 `normal` 模式下使用普通 `multi_match`。
3. 增加 DSL builder 测试：
   - 单字段查询。
   - OR 短语查询。
   - 与 IPC / NOT 组合查询。
   - `tscd` 不回退。
4. 增加 API 非法语法测试：
   - `mainClaim:`
   - `claims:()`
   - `description:(均衡) AND AND ipc:H02M`
5. 更新文档：
   - `docs/query_syntax.md`
   - `docs/field_mapping.md`
   - `docs/api_spec.md`

## 阶段边界

本任务不做：

1. 不修改 OpenSearch mapping。
2. 不重建索引。
3. 不新增高亮片段。
4. 不新增排序。
5. 不改变 `tscd` 现有字段范围。
6. 不进入阶段 11 部署上线。

## 提交建议

建议提交：

```text
feat: add fine grained text query fields
docs: update fine grained query syntax
```

提交前运行：

```bash
.venv/bin/python -m pytest -q
```

