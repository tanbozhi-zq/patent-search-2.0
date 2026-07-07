# 部署环境确认清单

## 1. 当前结论

部署环境按生产环境处理，服务器、OpenSearch 连通性、服务端口和第一阶段部署方式已完成初步确认。

当前生产约束：

1. 服务器公网 `8000` 端口已验证可访问，但生产环境不建议长期使用公网 IP + 裸端口暴露服务。
2. 第一版服务必须预留并支持 `X-API-Key` 鉴权。
3. OpenSearch 使用 HTTPS，但当前存在自签名证书链问题；测试和初期联调可使用 `verify_certs=False`，生产长期运行建议配置可信 CA 证书。
4. 当前 Docker 和 Nginx 未安装，第一阶段部署方式采用 `work` 用户 + Python venv + FastAPI/Uvicorn + systemd。
5. SaaS 后端正式访问方式仍需确认：内网直连 / Nginx / 公司 API 网关。

## 2. 服务器环境

| 类别 | 确认项 | 当前状态 |
|---|---|---|
| 部署环境 | 生产环境 | 已确认 |
| 公网 IP | `124.174.76.216` | 已确认 |
| 内网 IP | `192.168.1.149` | 已确认，SaaS 是否可内网访问待确认 |
| 主机名 | `patent-search-mcp` | 已确认 |
| 操作系统 | CentOS Stream 9 | 已确认 |
| 内核版本 | `5.14.0-312.el9.x86_64` | 已确认 |
| 架构 | `x86_64` | 已确认 |
| CPU | 4 vCPU | 已确认 |
| 内存 | 15 GiB | 已确认 |
| 磁盘 | 根目录约 394G，可用约 375G | 已确认 |
| root SSH | 可登录 | 已确认 |
| work SSH | 可登录 | 已确认 |
| 推荐部署账号 | `work` | 已确认 |
| 凭据处理 | root、work、OpenSearch 密码只写入安全凭据或服务器 `.env`，不写入 Git 文档 | 已确认 |
| Python | Python 3.9.19 | 已确认 |
| pip / venv | 可用 | 已确认 |
| Docker | 未安装 | 当前不可用 |
| Docker Compose | 未安装 | 当前不可用 |
| systemd | 可用，版本 250 | 已确认 |
| sudo 权限 | `work` 用户可通过 sudo 配置 systemd | 已确认 |
| Nginx | 未安装 | 当前不可用 |
| 服务端口 | `8000` | 已确认 |
| 8000 占用情况 | 未被占用 | 已确认 |
| firewalld | inactive / dead | 已确认 |
| 8000 公网访问 | `http://124.174.76.216:8000` 已验证可访问 | 已确认 |
| 鉴权 | 第一版预留 `X-API-Key` | 已确认 |
| 日志目录 | `/var/log/patent-search-service` | 建议值 |
| 代码目录 | `/opt/patent-search-service` | 建议值 |

## 3. OpenSearch 连接信息

当前 `.env.example` 中约定如下：

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

需要确认：

| 确认项 | 当前建议 | 当前状态 |
|---|---|---|
| OpenSearch Host | `opensearch-o-00gcv9almneh.escloud.volces.com` | 已确认 |
| OpenSearch Port | `9200` | 已确认 |
| 是否 HTTPS | `true` | 已确认 |
| 用户名 | `admin` | 已确认 |
| 密码 | 通过 `.env` 配置，不提交 Git | 已确认处理方式 |
| 索引名 | `patent_index` | 已确认 |
| 索引存在 | `patent_index` 存在 | 已确认 |
| 索引可查询 | `_count` 和 `match_all size=1` 成功 | 已确认 |
| 连通性 | 部署服务器可访问 OpenSearch | 已验证 |
| 查询权限 | 服务账号可读取索引 | 已验证 |
| 数据量 | 143,773,810 条 | 已确认 |
| OpenSearch 版本 | 3.3.0 | 已确认 |
| 证书 | 自签名证书链，普通校验失败 | 待生产化处理 |

## 4. 建议执行的环境命令

### 4.1 服务器登录信息

服务器公网 IP：

```text
124.174.76.216
```

推荐部署用户：

```text
work
```

root 用户仅用于必要的系统管理操作，不作为长期部署维护用户。root、work 和 OpenSearch 密码不得写入 Git 文档；开发或运维需要时，由项目总控通过安全方式提供，或直接写入服务器 `/opt/patent-search-service/.env`。

服务器基础信息：

```bash
uname -a
cat /etc/os-release
lscpu
free -h
df -h
ip addr
```

运行环境：

```bash
python3 --version
pip3 --version
docker --version
systemctl --version
nginx -v
```

OpenSearch 连通性：

```bash
curl -k -u "$OPENSEARCH_USER:$OPENSEARCH_PASS" "https://$OPENSEARCH_HOST:$OPENSEARCH_PORT"
curl -k -u "$OPENSEARCH_USER:$OPENSEARCH_PASS" "https://$OPENSEARCH_HOST:$OPENSEARCH_PORT/patent_index/_count"
```

端口检查：

```bash
ss -lntp
```

## 5. 推荐部署约定

第一版建议：

| 项 | 建议 |
|---|---|
| 服务名称 | `patent-search-service` |
| 运行方式 | `work` 用户 + Python venv + FastAPI/Uvicorn + systemd |
| 默认端口 | `8000` |
| 健康检查 | `GET /health` |
| 配置方式 | `.env` |
| 日志输出 | 控制台 + 文件日志，后续接入日志平台 |
| 代码目录 | `/opt/patent-search-service` |
| 日志目录 | `/var/log/patent-search-service` |
| 鉴权 | `ENABLE_AUTH=true/false` + `X-API-Key` |
| OpenSearch 证书 | 初期可 `verify_certs=False`，生产长期建议配置 CA |

## 6. 阻塞项判断

以下任一项未确认时，不进入生产正式流量：

1. SaaS 后端正式访问方式未确认。
2. 生产入口仍使用公网 IP + `8000` 裸端口。
3. 接口鉴权未启用。
4. OpenSearch 证书校验长期关闭且无运维确认。
5. 缺少 systemd 服务配置和运行日志路径。
6. 缺少服务异常恢复、重启策略和运维交接说明。

## 7. 当前仍需确认

1. SaaS 后端是否能访问服务器内网 IP `192.168.1.149`。
2. 生产入口采用内网直连、Nginx 反向代理，还是公司 API 网关。
3. 是否安装 Nginx 并提供 HTTPS 入口。
4. 是否由运维提供 OpenSearch CA 证书。
5. 生产 API Token 的生成、保存和轮换责任人。
6. 服务器 `.env` 的最终维护责任人。

## 8. 部署结论

阶段二“部署环境前置确认”可以进入初步验收状态。

允许进入阶段三冻结和阶段四骨架准备的依据：

1. 生产服务器资源满足第一版服务部署。
2. `work` 用户、Python、pip、venv、systemd 可用。
3. OpenSearch 可访问，`patent_index` 可查询。
4. `8000` 端口可用并已验证公网可访问。
5. 第一阶段部署方式明确为 venv + systemd。

不允许直接承接生产正式流量的原因：

1. SaaS 正式访问路径未确认。
2. 生产入口安全方案未最终确认。
3. OpenSearch CA 证书问题未生产化解决。
