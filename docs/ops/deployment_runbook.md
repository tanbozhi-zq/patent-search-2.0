# 专利检索服务部署执行手册

## 1. 部署目标

将专利检索服务部署为长期运行的 HTTP API 服务，由接入方通过 `BASE_URL` 和 `X-API-Key` 调用。

第一版部署形态：

| 项 | 约定 |
|---|---|
| 部署用户 | `work` |
| 部署目录 | `/opt/patent-search-service` |
| 日志目录 | `/var/log/patent-search-service` |
| 运行方式 | Python venv + Uvicorn + systemd |
| 默认端口 | `8000` |
| 健康检查 | `GET /health` |
| systemd 服务名 | `patent-search-service` |

## 2. 前置条件

服务器需要满足：

- Linux 服务器可登录。
- `work` 用户可用，并具备必要的 sudo 权限。
- Python、pip、venv 可用。
- 服务器可访问 OpenSearch。
- 服务端口可用。
- 真实密钥仅写入服务器 `.env`，不得提交 Git。

检查命令：

```bash
python3 --version
pip3 --version
systemctl --version
ss -lntp
```

## 3. 目录准备

```bash
sudo mkdir -p /opt/patent-search-service
sudo mkdir -p /var/log/patent-search-service
sudo chown -R work:work /opt/patent-search-service
sudo chown -R work:work /var/log/patent-search-service
```

## 4. 代码发布

切换到部署用户：

```bash
sudo su - work
```

将代码同步到：

```text
/opt/patent-search-service
```

发布前记录部署提交号：

```bash
cd /opt/patent-search-service
git rev-parse --short HEAD
```

## 5. Python 环境

```bash
cd /opt/patent-search-service
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## 6. 环境变量

在服务器创建：

```text
/opt/patent-search-service/.env
```

模板：

```env
SERVICE_NAME=patent-search-service
SERVICE_HOST=0.0.0.0
SERVICE_PORT=8000

ENABLE_AUTH=true
API_TOKEN=请填写生产 API Key

OPENSEARCH_HOST=opensearch-o-00gcv9almneh.escloud.volces.com
OPENSEARCH_PORT=9200
OPENSEARCH_USE_HTTPS=true
OPENSEARCH_USER=admin
OPENSEARCH_PASS=请填写 OpenSearch 密码
OPENSEARCH_INDEX=patent_index
OPENSEARCH_VERIFY_CERTS=false
OPENSEARCH_TIMEOUT_SECONDS=30

PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=同 API_TOKEN 或按实际接入填写
PATENT_SEARCH_USE_SELF_HOSTED=true
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
PATENT_SEARCH_TIMEOUT_SECONDS=30
```

注意：

- `.env` 不提交 Git。
- `API_TOKEN`、`OPENSEARCH_PASS` 不写入文档、日志或聊天记录。
- `OPENSEARCH_VERIFY_CERTS=false` 仅适合当前自签名证书条件；长期生产建议配置可信 CA 后改为 `true`。

## 7. systemd 配置

项目内已有模板：

```text
deployment/patent-search-service.service
```

安装：

```bash
sudo cp /opt/patent-search-service/deployment/patent-search-service.service /etc/systemd/system/patent-search-service.service
sudo systemctl daemon-reload
sudo systemctl enable patent-search-service
sudo systemctl start patent-search-service
```

查看状态：

```bash
sudo systemctl status patent-search-service
```

重启：

```bash
sudo systemctl restart patent-search-service
```

停止：

```bash
sudo systemctl stop patent-search-service
```

## 8. 日志

标准输出：

```text
/var/log/patent-search-service/service.log
```

错误输出：

```text
/var/log/patent-search-service/error.log
```

查看日志：

```bash
tail -f /var/log/patent-search-service/service.log
tail -f /var/log/patent-search-service/error.log
journalctl -u patent-search-service -f
```

日志检查要求：

- 不输出 `API_TOKEN`。
- 不输出 `OPENSEARCH_PASS`。
- 不输出完整异常堆栈给接口调用方。

## 9. 部署后验证

健康检查：

```bash
/opt/patent-search-service/.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
```

检索 smoke：

```bash
/opt/patent-search-service/.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
```

详情和引证 smoke：

```bash
/opt/patent-search-service/.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
```

SaaS 适配层 smoke：

```bash
/opt/patent-search-service/.venv/bin/python scripts/smoke_saas_adapter.py http://127.0.0.1:8000 "$API_TOKEN"
```

## 10. 对外接入信息

部署验证通过后，向接入方提供：

| 项 | 示例 |
|---|---|
| Base URL | `http://host:8000` 或网关地址 |
| API Key | 通过安全渠道单独提供 |
| 接口契约 | `对外交付文档/专利检索服务接口契约.md` |
| 查询语法 | `对外交付文档/专利检索服务查询语法说明.md` |
| 验收清单 | `对外交付文档/专利检索服务上线验收清单.md` |

## 11. 回滚方案

推荐保留上一版本代码目录或 Git 提交号。

回滚步骤：

```bash
cd /opt/patent-search-service
git fetch --all
git checkout <上一稳定提交号>
.venv/bin/pip install -r requirements.txt
sudo systemctl restart patent-search-service
```

回滚后必须执行：

```bash
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
```

如果接入方已开始调用，需要同步通知接入方当前服务状态。

## 12. 常见问题

### 12.1 服务无法启动

检查：

```bash
sudo systemctl status patent-search-service
journalctl -u patent-search-service -n 100
tail -n 100 /var/log/patent-search-service/error.log
```

重点确认：

- `.env` 是否存在。
- `.venv` 是否安装依赖。
- `uvicorn` 路径是否存在。
- 服务端口是否被占用。

### 12.2 OpenSearch 查询失败

检查：

```bash
curl -k -u "$OPENSEARCH_USER:$OPENSEARCH_PASS" "https://$OPENSEARCH_HOST:$OPENSEARCH_PORT/$OPENSEARCH_INDEX/_count"
```

重点确认：

- OpenSearch Host、Port、Index 是否正确。
- 账号密码是否正确。
- 服务器网络是否可访问 OpenSearch。
- 证书配置是否符合当前环境。

### 12.3 鉴权失败

检查：

- 服务端 `.env` 中 `ENABLE_AUTH=true`。
- 接入方请求头是否传入 `X-API-Key`。
- 请求头值是否与服务器 `API_TOKEN` 一致。
