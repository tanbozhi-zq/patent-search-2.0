# 阶段四测试验收单

## 1. 角色与任务

执行角色：测试人员。

配合角色：开发人员。

裁决角色：项目总控。

测试人员负责根据阶段四开发派工单、API 文档和部署环境文档，对后端服务骨架进行验收，输出测试结论或问题清单。

## 2. 测试依据

测试依据：

```text
agent.md
docs/requirement.md
docs/deploy_env_check.md
docs/api_spec.md
docs/stage4_dev_assignment.md
docs/superpowers/plans/2026-07-02-stage-4-service-skeleton.md
```

## 3. 阶段四验收范围

阶段四只验收服务骨架，不验收专利检索业务。

验收范围：

1. 项目结构。
2. 依赖安装。
3. 配置读取。
4. 健康检查接口。
5. `X-API-Key` 鉴权基础逻辑。
6. OpenSearch client 构造。
7. systemd 部署模板。
8. README 和冒烟测试脚本。
9. 阶段边界检查。

## 4. 阶段四不验收范围

以下内容不属于阶段四验收范围，若开发人员实现了，测试人员应标记为越界：

1. `POST /api/patent/search` 真实检索结果。
2. 查询语法解析。
3. OpenSearch DSL 查询构造。
4. 专利详情查询。
5. 引证/相关文献查询。
6. 字段映射业务转换。
7. 法律状态映射业务转换。
8. 外采服务对比测试。
9. SaaS 联调。
10. 生产正式流量验证。

## 5. 测试准备

测试人员在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

如本机 Python 版本不是 3.9，可记录为环境差异；阶段四代码必须兼容服务器 Python 3.9.19。

## 6. 自动化测试

执行：

```bash
python3 -m pytest -q
```

验收标准：

1. pytest 能正常收集测试。
2. 所有阶段四测试通过。
3. 测试不依赖真实 OpenSearch 账号密码。
4. 测试不要求访问生产服务器。

测试失败时，记录：

```text
失败测试文件：
失败测试用例：
失败原因：
是否阻塞阶段四：
```

## 7. 健康检查验收

启动服务：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

执行：

```bash
curl http://127.0.0.1:8000/health
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

`curl` 响应应包含：

```json
{
  "success": true,
  "code": 0,
  "message": "ok",
  "data": {
    "status": "healthy",
    "service": "patent-search-service"
  }
}
```

冒烟脚本预期输出：

```text
health ok
```

## 8. 配置读取验收

检查 `.env.example` 至少包含：

```text
SERVICE_NAME
SERVICE_HOST
SERVICE_PORT
ENABLE_AUTH
API_TOKEN
OPENSEARCH_HOST
OPENSEARCH_PORT
OPENSEARCH_USE_HTTPS
OPENSEARCH_USER
OPENSEARCH_PASS
OPENSEARCH_INDEX
OPENSEARCH_VERIFY_CERTS
OPENSEARCH_TIMEOUT_SECONDS
```

验收标准：

1. 默认服务端口为 `8000`。
2. 默认 OpenSearch 索引为 `patent_index`。
3. 默认支持 HTTPS。
4. 默认 `OPENSEARCH_VERIFY_CERTS=false`，符合当前证书现状。
5. `.env.example` 不包含真实账号密码。

## 9. 鉴权验收

测试人员应确认：

1. `ENABLE_AUTH=false` 时，鉴权依赖允许请求通过。
2. `ENABLE_AUTH=true` 且 `X-API-Key` 正确时，请求通过。
3. `ENABLE_AUTH=true` 且缺少 `X-API-Key` 时，返回 401。
4. `ENABLE_AUTH=true` 且 `X-API-Key` 错误时，返回 401。
5. 错误响应包含 `success=false`、业务错误码、错误消息和 `data=null`。

## 10. OpenSearch Client 验收

阶段四只验收 client 构造，不要求真实查询 OpenSearch。

验收标准：

1. client 使用 `.env` 中的 host、port、HTTPS、账号密码、索引名。
2. `OPENSEARCH_VERIFY_CERTS=false` 能传入 client。
3. `OPENSEARCH_TIMEOUT_SECONDS` 能传入 client。
4. 单元测试不输出 OpenSearch 密码。
5. 不在代码中硬编码真实密码。

## 11. systemd 模板验收

检查：

```text
deployment/patent-search-service.service
```

验收标准：

1. `User=work`。
2. `WorkingDirectory=/opt/patent-search-service`。
3. `EnvironmentFile=/opt/patent-search-service/.env`。
4. `ExecStart` 使用 venv 内的 `uvicorn`。
5. 监听 `0.0.0.0:8000`。
6. `Restart=always`。
7. 日志输出到 `/var/log/patent-search-service/`。

## 12. 阶段边界验收

测试人员需要检查代码中是否出现以下越界迹象：

```bash
rg -n "query parser|dsl|match_all|multi_match|search\\(|/api/patent/search|/api/patent/detail|/api/patent/citations" app tests
```

允许出现：

1. 空 router 文件名或模块名。
2. 文档字符串或注释中说明后续阶段。

不允许出现：

1. 真实 search/detail/citations 路由处理函数。
2. 对 OpenSearch `search()` 的真实业务调用。
3. 查询语法解析逻辑。
4. 字段映射业务逻辑。

## 13. 测试结论模板

测试完成后输出：

```text
阶段四测试结论：通过 / 不通过

测试环境：
Python 版本：
依赖安装结果：
pytest 结果：
健康检查结果：
鉴权测试结果：
OpenSearch client 构造测试结果：
systemd 模板检查结果：
阶段边界检查结果：

阻塞问题：
1.
2.

非阻塞建议：
1.
2.

是否建议进入项目总控审查：是 / 否
```

## 14. 通过标准

满足以下条件才可判定阶段四测试通过：

1. 自动化测试通过。
2. `/health` 可访问且响应符合文档。
3. 配置读取符合 `.env.example` 约定。
4. 鉴权基础逻辑正确。
5. OpenSearch client 构造正确且不泄露密码。
6. systemd 模板符合生产服务器现状。
7. 未发现阶段五业务逻辑越界。
8. 测试结论已输出。
