# 专利检索服务部署与 v2 切换手册

## 1. 运行组件

| 组件 | systemd 服务 | 本地端口 | 鉴权 |
|---|---|---:|---|
| FastAPI 检索服务 | patent-search-service.service | 8000 | X-API-Key |
| Streamable HTTP MCP | patent-mcp.service | 9000 /mcp | Bearer Token |

MCP 不直接访问 OpenSearch，而是通过 FastAPI 调用。因此更换 FastAPI 的读索引会同时影响 HTTP API 与 MCP。

## 2. 常规代码发布

在服务器的 /opt/patent-search-service 目录执行：

    git rev-parse --short HEAD
    .venv/bin/pip install -r requirements.txt
    sudo systemctl restart patent-search-service.service
    sudo systemctl restart patent-mcp.service
    systemctl is-active patent-search-service.service patent-mcp.service

发布后至少验证：

    curl -fsS http://127.0.0.1:8000/health
    .venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000

真实 API Token、MCP Token 和 OpenSearch 凭据仅在服务器 .env 或密钥管理系统中维护。

## 3. 环境变量边界

服务读取 OpenSearch 的关键变量如下：

    OPENSEARCH_HOST=<managed in server environment>
    OPENSEARCH_PORT=9200
    OPENSEARCH_USE_HTTPS=true
    OPENSEARCH_USER=<managed in server environment>
    OPENSEARCH_PASS=<managed in server environment>
    OPENSEARCH_INDEX=patent_index
    OPENSEARCH_VERIFY_CERTS=false
    OPENSEARCH_TIMEOUT_SECONDS=30

OPENSEARCH_INDEX 是服务的读目标。切换完成后应设置为稳定读 alias，例如 patent_search_read，
而不是直接长期绑定到某个物理索引名。

## 4. OpenSearch v2 切换

不得仅修改 OPENSEARCH_INDEX 并重启服务。按以下顺序执行：

1. 确认新索引的历史、增量、更新和删除数据完整。
2. 验证 dynamic: strict 下入库没有未知字段失败。
3. 合入并验证 IPCList 的 v2 查询兼容代码。
4. 将新索引恢复为 serving 设置：1 副本、10s refresh，显式 refresh，检查分片健康。
5. 创建并验证读 alias。
6. 将 OPENSEARCH_INDEX 指向 alias，重启 FastAPI 和 MCP。
7. 在 alias 上执行搜索、详情、引证、法律历史及 MCP smoke。

完整 alias、回滚和验收规则见 docs/ops/opensearch_v2_cutover.md。

## 5. 运行状态与日志

    systemctl status patent-search-service.service patent-mcp.service
    journalctl -u patent-search-service.service -n 100 --no-pager
    journalctl -u patent-mcp.service -n 100 --no-pager
    ss -ltnp | grep -E ':(8000|9000)[[:space:]]'

日志不得打印 API Token、MCP Token、OpenSearch 密码或完整 Authorization 请求头。

## 6. 回滚

代码回滚使用已验证的提交号；索引回滚使用读 alias 原子切回旧索引。两者均完成后，重启两个服务并执行：

    curl -fsS http://127.0.0.1:8000/health
    .venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000

切换或回滚完成后，应记录提交号、alias 目标、时间、执行人和 smoke 结果。
