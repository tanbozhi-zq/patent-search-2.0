# 阶段四测试结论

## 1. 总体结论

阶段四测试结论：**通过**

阶段四交付物（服务骨架）已按 `docs/stage4_dev_assignment.md` 和 `docs/stage4_test_acceptance.md` 完成，自动化测试、健康检查、配置读取、鉴权、OpenSearch client 构造、systemd 模板和阶段边界检查均符合要求，未发现阻塞性问题。

---

## 2. 测试环境

| 项 | 结果 |
|---|---|
| 测试执行机器 | 本地开发机 |
| 本地 Python 版本 | 3.13.14 |
| 目标服务器 Python 版本 | 3.9.19（代码未使用 3.10+ 语法，兼容） |
| 虚拟环境 | `.venv/` 已创建 |
| 依赖来源 | `requirements.txt` |

---

## 3. 测试结果明细

### 3.1 项目结构

| 检查项 | 结果 |
|---|---|
| `app/` 后端代码目录 | 存在 |
| `tests/` 测试目录 | 存在 |
| `scripts/smoke_health.py` | 存在 |
| `deployment/patent-search-service.service` | 存在 |
| `requirements.txt` | 存在 |
| `pytest.ini` | 存在 |
| `.env.example` | 存在 |
| `README.md` | 存在 |

### 3.2 依赖安装

执行命令：

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

结果：依赖已安装，无报错。

### 3.3 pytest 自动化测试

执行命令：

```bash
python3 -m pytest -q
```

结果：

```text
10 passed in 0.08s
```

测试覆盖：

- 配置默认值与 `.env` 读取
- `/health` 接口响应结构
- `X-API-Key` 鉴权开启/关闭、正确/错误/缺失 Token
- OpenSearch client 构造（hosts、http_auth、verify_certs、timeout）
- 阶段四空 router 可导入

测试未依赖真实 OpenSearch 账号密码，未访问生产服务器。

### 3.4 健康检查接口

启动服务：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

执行：

```bash
curl -s http://127.0.0.1:8000/health
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

`curl` 响应：

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

冒烟脚本输出：

```text
health ok
```

结果：符合 `docs/api_spec.md` 中 `/health` 响应约定。

### 3.5 配置读取

`.env.example` 包含字段：

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

验收结果：

- 默认服务端口为 `8000`：符合
- 默认 OpenSearch 索引为 `patent_index`：符合
- 默认支持 HTTPS：符合
- 默认 `OPENSEARCH_VERIFY_CERTS=false`：符合
- `.env.example` 不包含真实账号密码：符合（密码字段为空）

备注：本地 `.env` 文件包含真实 OpenSearch 凭据，但已正确加入 `.gitignore`，未进入 Git。

### 3.6 鉴权验收

测试位置：`tests/test_security.py`

| 场景 | 预期 | 结果 |
|---|---|---|
| `ENABLE_AUTH=false` | 允许通过 | 通过 |
| `ENABLE_AUTH=true` + 正确 `X-API-Key` | 允许通过 | 通过 |
| `ENABLE_AUTH=true` + 缺失 `X-API-Key` | 返回 401 | 通过 |
| `ENABLE_AUTH=true` + 错误 `X-API-Key` | 返回 401 | 通过 |

错误响应结构：

```json
{
  "success": false,
  "code": 40101,
  "message": "missing or invalid X-API-Key",
  "data": null
}
```

结果：符合 `docs/api_spec.md` 通用失败响应结构。

### 3.7 OpenSearch Client 构造

测试位置：`tests/test_opensearch_repo.py`

验收结果：

- client 使用 `.env` 中的 host、port、HTTPS、账号密码、索引名：符合
- `OPENSEARCH_VERIFY_CERTS=false` 能传入 client：符合
- `OPENSEARCH_TIMEOUT_SECONDS` 能传入 client：符合
- 单元测试不输出 OpenSearch 密码：符合
- 不在代码中硬编码真实密码：符合

### 3.8 systemd 模板

检查文件：`deployment/patent-search-service.service`

| 检查项 | 预期 | 结果 |
|---|---|---|
| `User` | `work` | 符合 |
| `Group` | `work` | 符合 |
| `WorkingDirectory` | `/opt/patent-search-service` | 符合 |
| `EnvironmentFile` | `/opt/patent-search-service/.env` | 符合 |
| `ExecStart` | 使用 venv 内 `uvicorn` | 符合 |
| 监听地址 | `0.0.0.0:8000` | 符合 |
| `Restart` | `always` | 符合 |
| 日志输出 | `/var/log/patent-search-service/` | 符合 |

### 3.9 阶段边界检查

执行命令：

```bash
rg -n "query parser|dsl|match_all|multi_match|search\(|/api/patent/search|/api/patent/detail|/api/patent/citations" app tests
```

结果：未匹配到任何阶段五业务逻辑。`app/api/search.py`、`app/api/detail.py`、`app/api/citations.py` 均为空 router，符合阶段四范围。

---

## 4. 阻塞问题

无。

---

## 5. 非阻塞建议

1. **测试环境差异记录**：本地 Python 版本为 3.13.14，目标服务器为 3.9.19。阶段四代码目前未使用 3.10+ 语法，建议后续在 CI 或服务器上补充一次 pytest 验证，确保生产环境兼容性。
2. **`tests/test_config.py` 的稳健性**：`test_settings_defaults_match_stage_four_contract` 直接实例化 `Settings()`，会读取本地 `.env`。如果 `.env` 中某些值与默认值不同，测试可能失败。建议后续将该测试改为显式传入参数，避免依赖本地环境。
3. **README 启动命令**：README 中 `uvicorn app.main:app --host 0.0.0.0 --port 8000` 未区分是否已激活 venv，建议补充 `source .venv/bin/activate` 前缀，降低新手启动出错概率。

---

## 6. 是否建议进入项目总控审查

**是**

阶段四服务骨架已通过测试验收，建议提交项目总控进行最终审查，审查通过后可进入阶段五（最小检索链路）开发。
