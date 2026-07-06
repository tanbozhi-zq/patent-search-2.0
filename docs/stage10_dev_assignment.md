# 阶段 10 开发派工单

## 角色

- 项目总控：维护阶段十接入边界、适配契约、配置开关、回滚策略、Git 状态和交付文档。
- 开发人员：按设计文档实现 SaaS 工具适配或联调所需配置，不修改 OpenSearch mapping，不直接改只读副本作为交付源码。
- 测试人员：验证 SaaS Agent 调用链路、错误响应、回退外采和灰度范围。

## 开发目标

完成自研服务与 SaaS PatentHub 工具层之间的适配，使 SaaS Agent 能通过配置切换调用自研 search/detail/citations。

## 开发范围

1. 实现或准备 PatentHub 工具适配层。
2. 支持配置自研服务 base_url 和 `X-API-Key`。
3. search 工具将自研 `records` 转为工具层 `patents`。
4. detail/citations 工具消费自研接口已输出的 snake_case 字段。
5. 错误响应转为工具层 `{error, code}`。
6. 支持配置开关回退外采 PatentHub。
7. 输出联调说明和部署配置示例。

## 阶段边界

阶段 10 不做：

1. 不生产全量切流。
2. 不修改 OpenSearch mapping。
3. 不重建索引。
4. 不实现企业专利画像。
5. 不实现高亮片段，除非总控重新冻结为阻塞项。
6. 不要求召回排序与外采完全一致。

## 验证要求

开发完成后至少运行：

```bash
.venv/bin/python -m pytest -q
```

并配合测试人员完成 SaaS Agent 联调 smoke。

