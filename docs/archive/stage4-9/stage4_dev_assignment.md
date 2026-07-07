# 阶段四开发派工单

## 1. 角色与任务

执行角色：开发人员。

调度角色：项目总控。

验收角色：测试人员、项目总控。

本阶段任务是搭建 `patent-search-service` 后端服务骨架，为后续最小检索链路开发提供稳定基础。

详细实施步骤以以下任务书为准：

```text
docs/superpowers/plans/2026-07-02-stage-4-service-skeleton.md
```

## 2. 开发目标

阶段四完成后，项目应具备：

1. 可启动的 FastAPI 后端项目。
2. `GET /health` 健康检查接口。
3. `.env` 配置读取能力。
4. `X-API-Key` 鉴权基础能力。
5. OpenSearch client 构造封装。
6. pytest 测试框架。
7. systemd 部署模板。
8. README 启动说明。

## 3. 允许开发范围

开发人员只允许实现以下内容：

| 模块 | 允许内容 |
|---|---|
| `app/main.py` | FastAPI app、路由注册、健康检查 |
| `app/core/config.py` | `.env` 配置读取 |
| `app/core/security.py` | `X-API-Key` 鉴权依赖 |
| `app/core/exceptions.py` | 统一错误结构基础函数 |
| `app/core/logging.py` | 基础日志初始化 |
| `app/repositories/opensearch_repo.py` | OpenSearch client 构造 |
| `app/api/*.py` | 空 router，占位给后续阶段 |
| `tests/` | 阶段四单元测试和接口测试 |
| `scripts/smoke_health.py` | 健康检查冒烟脚本 |
| `deployment/patent-search-service.service` | systemd 模板 |
| `README.md` | 本地启动、测试、部署说明 |

## 4. 禁止开发范围

阶段四禁止实现以下内容：

1. `POST /api/patent/search` 真实检索逻辑。
2. 查询语法解析器。
3. OpenSearch DSL Builder。
4. 专利详情查询逻辑。
5. 引证/相关文献查询逻辑。
6. 字段映射业务逻辑。
7. 法律状态映射业务逻辑。
8. SaaS 联调逻辑。
9. 生产入口、Nginx、API 网关配置。
10. 任何绕过 `X-API-Key` 生产鉴权约束的实现。

如开发中发现必须越界，必须先提交问题给项目总控确认，不得自行扩展范围。

## 5. 技术约束

| 项 | 约束 |
|---|---|
| Python | 兼容服务器 Python 3.9.19 |
| Web 框架 | FastAPI |
| 启动方式 | Uvicorn |
| 部署方式 | `work` 用户 + Python venv + systemd |
| 服务端口 | `8000` |
| 配置方式 | `.env` |
| 鉴权方式 | `X-API-Key` |
| OpenSearch 索引 | `patent_index` |
| OpenSearch HTTPS | `true` |
| OpenSearch 证书 | 第一阶段支持 `OPENSEARCH_VERIFY_CERTS=false` |

## 6. 开发环境信息

服务器连接信息：

| 项 | 值 |
|---|---|
| 公网 IP | `124.174.76.216` |
| 推荐部署用户 | `work` |
| root 用户 | 仅用于必要系统管理，不作为长期部署用户 |
| 服务目录 | `/opt/patent-search-service` |
| 日志目录 | `/var/log/patent-search-service` |

OpenSearch 配置：

```env
OPENSEARCH_HOST=opensearch-o-00gcv9almneh.escloud.volces.com
OPENSEARCH_PORT=9200
OPENSEARCH_USE_HTTPS=true
OPENSEARCH_USER=admin
OPENSEARCH_PASS=
OPENSEARCH_INDEX=patent_index
OPENSEARCH_VERIFY_CERTS=false
OPENSEARCH_TIMEOUT_SECONDS=30
```

密码处理要求：

1. `OPENSEARCH_PASS` 不得写入 Git。
2. root 密码和 work 密码不得写入 Git。
3. 开发本地如需连接 OpenSearch，由项目总控通过安全方式提供密码。
4. 服务器部署时，将真实密码写入服务器 `/opt/patent-search-service/.env`。
5. `.env.example` 只能保留空密码占位。

## 7. 开发交付物

开发人员完成后必须交付：

1. `app/` 后端代码目录。
2. `tests/` 测试目录。
3. `scripts/smoke_health.py`。
4. `deployment/patent-search-service.service`。
5. `requirements.txt`。
6. `pytest.ini`。
7. `.env.example` 更新版。
8. `README.md`。
9. 测试执行结果。
10. 变更说明。

## 8. 开发验收命令

开发人员提交前必须执行：

```bash
python3 -m pytest -q
```

本地启动服务：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

预期：

```text
health ok
```

## 9. 提交要求

阶段四建议按任务书分批提交：

1. `chore: scaffold python service test setup`
2. `feat: add health check endpoint`
3. `feat: add service configuration loading`
4. `feat: add api token security dependency`
5. `feat: add opensearch client repository`
6. `chore: add service delivery skeleton`

如果开发环境不便频繁提交，至少在最终交付时保证 Git diff 清晰，不能混入阶段五业务逻辑。

## 10. 完成定义

只有同时满足以下条件，阶段四开发才算完成：

1. 测试全部通过。
2. `/health` 本地可访问。
3. `.env.example` 覆盖服务、鉴权、OpenSearch 配置。
4. `X-API-Key` 鉴权单元测试通过。
5. OpenSearch client 构造测试通过，且测试不依赖真实 OpenSearch。
6. systemd 模板符合生产服务器现状。
7. 未实现任何阶段五业务逻辑。
8. 测试人员完成验收。
9. 项目总控审查通过。
