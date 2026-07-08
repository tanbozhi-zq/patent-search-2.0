# Stage 11 Deployment and Delivery Design

## 1. Background

阶段 10 已完成 SaaS PatentHub 工具适配层和联调准备。阶段 11 目标是把自研专利检索服务从联调可用推进到可部署、可运维、可回滚、可交付沉淀。

部署前置依据：

```text
docs/deploy_env_check.md
deployment/patent-search-service.service
docs/stage10_integration_report.md
README.md
.env.example
```

## 2. Goals

阶段 11 目标：

1. 完成生产或准生产环境部署方案确认。
2. 固化 systemd 部署步骤、目录结构、配置项和日志路径。
3. 明确 SaaS 访问路径、鉴权 token、回滚方案和灰度范围。
4. 输出部署验收记录和运维交接说明。
5. 给出是否进入阶段 12 后续迭代优化的总控建议。

## 3. Non-Goals

阶段 11 不做：

1. 不修改 OpenSearch mapping。
2. 不重建索引。
3. 不做生产全量切流，除非另行冻结灰度放量计划。
4. 不实现新增业务能力。
5. 不实现高亮片段、企业专利画像或完整法律历史接口。
6. 不把密钥、服务器密码、OpenSearch 密码写入 Git。

## 4. Deployment Shape

第一版部署形态：

| Item | Value |
|---|---|
| User | `work` |
| Code dir | `/opt/patent-search-service` |
| Venv | `/opt/patent-search-service/.venv` |
| Config | `/opt/patent-search-service/.env` |
| Service | `patent-search-service` |
| Runtime | Uvicorn |
| Port | `8000` |
| Logs | `/var/log/patent-search-service/service.log` and `error.log` |
| Process manager | systemd |

阶段 11 先沿用 systemd 模板：

```text
deployment/patent-search-service.service
```

## 5. Configuration

生产 `.env` 必须包含：

```text
ENABLE_AUTH=true
API_TOKEN=<provided securely>
OPENSEARCH_HOST=...
OPENSEARCH_PORT=9200
OPENSEARCH_USE_HTTPS=true
OPENSEARCH_USER=...
OPENSEARCH_PASS=<provided securely>
OPENSEARCH_INDEX=patent_index
OPENSEARCH_VERIFY_CERTS=false
OPENSEARCH_TIMEOUT_SECONDS=30
PATENT_SEARCH_BASE_URL=<service url>
PATENT_SEARCH_API_TOKEN=<same or dedicated token>
PATENT_SEARCH_USE_SELF_HOSTED=true
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
PATENT_SEARCH_TIMEOUT_SECONDS=30
```

密钥规则：

1. `.env` 只放服务器本地。
2. 不提交 Git。
3. token 生成、保存和轮换责任人必须记录在交付文档中。

## 6. Deployment Steps

阶段 11 部署步骤：

1. 创建目录：`/opt/patent-search-service`。
2. 同步代码到部署目录。
3. 创建 Python venv。
4. 安装 `requirements.txt`。
5. 写入生产 `.env`。
6. 创建日志目录 `/var/log/patent-search-service`。
7. 安装 systemd service。
8. 启动服务并设置开机自启。
9. 执行 health/search/detail/citations smoke。
10. 记录部署版本、提交号、配置摘要和验证结果。

## 7. Verification

部署后必须验证：

```bash
curl http://127.0.0.1:8000/health
python3 scripts/smoke_health.py http://127.0.0.1:8000
python3 scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
python3 scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
python3 scripts/smoke_saas_adapter.py http://127.0.0.1:8000 "$API_TOKEN"
```

还需验证：

1. 未授权返回 `40101`。
2. 查询语法错误返回 `40001`。
3. systemd 重启后服务自动恢复。
4. 日志路径可写且无敏感密钥输出。

## 8. Rollback

回滚策略：

1. 保留上一版本代码目录或 Git commit。
2. systemd 服务可回退到上一版本。
3. SaaS 工具适配层可设置 `PATENT_SEARCH_USE_SELF_HOSTED=false` 回退外采。
4. 如服务异常，先关闭灰度流量，再回滚服务。

回滚必须记录：

```text
触发原因
回滚时间
回滚提交
影响范围
恢复验证结果
```

## 9. Acceptance Criteria

阶段 11 通过标准：

1. 部署步骤已执行或可由运维按文档复现。
2. systemd 服务启动、重启、开机自启验证通过。
3. 自研服务 smoke 通过。
4. SaaS adapter smoke 通过。
5. 鉴权启用。
6. 日志路径、配置路径、代码目录明确。
7. 密钥未进入 Git。
8. 回滚方案验证或演练通过。
9. `docs/stage11_deployment_report.md` 输出。
10. 运维交接说明输出。

