# 专利检索 2.0 云端文档索引

本仓库上云内容只保留部署和协作所需的最小文档。开发过程资料、测试数据、会议记录和交付材料可以继续放在同一个本地工作文件夹内，但不进入 GitHub。

## 云端保留

| 路径 | 用途 |
|---|---|
| `README.md` | 项目概览、本地启动、服务接口和当前交付边界 |
| `.env.example` | 环境变量模板，不包含真实密钥 |
| `docs/README.md` | 云端文档范围说明 |
| `docs/ops/deployment_runbook.md` | systemd 部署、日志、验证和回滚步骤 |
| `docs/ops/deploy_env_check.md` | 部署前环境检查项 |
| `deployment/` | systemd 服务模板 |

## 本地保留

以下内容属于本地工作材料，默认不提交到 GitHub：

- `会议记录/`
- `相关测试/`
- `test数据集/`
- `对外交付文档/`
- `docs/archive/`
- `docs/delivery/`
- `docs/internal/`
- `docs/superpowers/`
- `frontend/`
- `tests/`
- `artifacts/`、`outputs/`、`reports/`

这些文件不会被删除，只是不再作为云端仓库内容维护。
