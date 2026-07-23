# 部署与 OpenSearch v2 环境确认

## 1. 当前状态

专利检索 HTTP API 与远程 HTTP MCP 已在生产服务器以 systemd 方式运行：

- patent-search-service.service：FastAPI/Uvicorn，端口 8000。
- patent-mcp.service：Streamable HTTP MCP，端口 9000、路径 /mcp。
- API 健康检查可用；MCP 在未带 Bearer Token 时返回 401。

服务配置仍通过 OPENSEARCH_INDEX 直接决定读取的物理索引或读 alias。当前部署读取
patent_index；新索引 patent_index_v2_20260716 已用于入库迁移，但尚未完成 API/MCP
读路径切换。

不要把“新索引已创建”或“新索引正在写入”视为服务已切换：OpenSearch 的两个物理索引不会自动
同步。

## 2. 服务器与安全边界

| 项目 | 当前约定 |
|---|---|
| 运行账号 | work |
| 项目目录 | /opt/patent-search-service |
| 运行方式 | Python venv + systemd |
| API 鉴权 | X-API-Key |
| MCP 鉴权 | Authorization: Bearer <MCP_ACCESS_TOKEN> |
| 密钥存放 | 仅服务器 .env 或认可的密钥管理系统 |

不得把 API Token、MCP Token、OpenSearch 密码或私钥写入 Git、交付文档、日志或聊天记录。

## 3. OpenSearch v2 现状

| 项目 | 旧索引 | v2 目标索引 |
|---|---|---|
| 名称 | patent_index | patent_index_v2_20260716 |
| IPCList | text + .keyword | 直接 keyword |
| mapping 动态策略 | true | strict |
| 当前服务读路径 | 是 | 否 |
| serving 前目标副本数 | 1 | 1 |
| serving 前目标刷新周期 | 10s | 10s |

v2 在批量加载阶段可以使用 number_of_replicas=0、refresh_interval=-1，但这不是可直接
承接检索流量的设置。切换前必须恢复 serving 设置并显式 refresh。

## 4. v2 切换前检查

1. 核对 v2 的文档数、增量更新和删除结果，不只比较 _count。
2. 对 dynamic: strict 做真实入库样本验证；未知字段必须显式处理，不能依赖旧索引自动扩展 mapping。
3. 修改并测试 IPC DSL：v2 使用 term/terms 查询 IPCList，不能依赖 IPCList.keyword。
4. 在 v2 上执行固定检索、详情、引证、法律历史和 MCP smoke。
5. 设置副本与刷新、检查分片健康，再建立稳定读 alias。
6. 仅在业务验收通过后，把 OPENSEARCH_INDEX 切换为读 alias；旧索引保留用于回滚。

详细流程见 docs/ops/opensearch_v2_cutover.md。

## 5. 只读检查命令

    systemctl is-active patent-search-service.service patent-mcp.service
    curl -fsS http://127.0.0.1:8000/health
    ss -ltnp | grep -E ':(8000|9000)[[:space:]]'

OpenSearch 检查应使用服务器安全环境中已有的变量，且不要回显密码：

    curl -k -u "$OPENSEARCH_USER:$OPENSEARCH_PASS" \
      "https://$OPENSEARCH_HOST:$OPENSEARCH_PORT/$OPENSEARCH_INDEX/_count"

## 6. 放行条件

只有当 API/MCP 健康、v2 增量数据对齐、查询兼容性通过、serving 设置恢复、读 alias 已验证且回滚
步骤可执行时，才允许将生产读路径切至 v2。
