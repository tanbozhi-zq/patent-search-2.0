# 阶段 11 部署派工单

## 角色

- 项目总控：维护部署边界、上线计划、回滚策略、Git 状态和交付文档。
- 开发人员：协助部署脚本、配置样例、systemd 服务和 smoke 工具。
- 测试人员：验证部署后接口、适配层、错误响应、日志和回滚。
- 运维/部署执行人：按部署文档在目标环境执行部署并反馈结果。

## 部署目标

将自研专利检索服务部署到生产或准生产服务器，使其具备 SaaS 灰度联调条件。

## 部署任务

1. 确认部署提交号。
2. 同步代码到 `/opt/patent-search-service`。
3. 创建 `.venv` 并安装依赖。
4. 写入服务器本地 `.env`。
5. 创建 `/var/log/patent-search-service`。
6. 安装并启动 `deployment/patent-search-service.service`。
7. 执行 smoke 验证。
8. 记录部署报告。
9. 验证回滚方案。

## 阶段边界

阶段 11 不做：

1. 不修改 OpenSearch mapping。
2. 不重建索引。
3. 不提交任何密钥。
4. 不直接全量切生产 SaaS 流量。
5. 不新增阶段 12 优化项。

## 交付文档

阶段 11 必须输出：

```text
docs/stage11_deployment_report.md
docs/stage11_operations_handoff.md
```

