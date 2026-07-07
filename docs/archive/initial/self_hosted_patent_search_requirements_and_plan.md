# 自研专利检索服务需求说明与实施路径

## 1. 项目背景

公司当前已有一套 AI 辅助专利获权 SaaS 平台。该平台中的多个核心能力依赖专利检索服务，包括专利检索、相似专利召回、申请人/权利人检索、IPC 分类检索、法律状态筛选、专利详情读取、引证文献查询、专利分析前的数据获取等。

目前，平台所使用的专利检索能力主要来自外采服务。现有外采服务的接口参数、查询语法、返回字段和字段消费方式，记录在《专利检索 MCP 参数与字段依赖》文档中。该文档明确列出了当前检索服务的核心参数，包括 `q`、`ds`、`sort`、`page`、`page_size`、`highlight`，并说明了 `patent_search`、`patent_get_detail`、`patent_get_citations` 等能力的参数和返回字段消费方式。

与此同时，公司已经开始建设自己的专利数据库。当前核心专利数据存储在 OpenSearch 索引 `patent_index` 中。根据《patent_index 数据库字段表》文档，该索引已经包含专利 ID、公开号、申请号、标题、摘要、申请人、当前权利人、发明人、IPC 分类、申请日、公开日、法律状态、权利要求、说明书、引证文献、相关文献等字段，具备建设自研专利检索服务的数据基础。

因此，本项目的目标是基于公司自有 OpenSearch 专利数据库，建设一套自研专利检索后端服务，逐步替代外采检索服务，为现有 SaaS 平台提供稳定、可控、可扩展的专利检索能力。

---

## 2. 项目建设目标

本项目的总体目标是：

> 基于自有 OpenSearch `patent_index`，建设一套可供 SaaS 平台调用的专利检索后端服务，实现从外采检索服务到自研检索服务的逐步替换。

具体目标包括：

1. 建设独立的专利检索后端服务。
2. 对上提供 HTTP API，供 SaaS 平台或业务后端调用。
3. 对下连接公司自有 OpenSearch `patent_index`。
4. 兼容现有外采检索服务的核心查询参数和返回字段，降低 SaaS 改造成本。
5. 支持专利列表检索、专利详情查询、引证/相关文献查询。
6. 支持标题、摘要、全文、IPC、申请人、当前权利人、申请日、公开日、法律状态、专利类型等核心查询条件。
7. 支持分页、排序、数据范围过滤、基础高亮等检索辅助能力。
8. 输出完整的需求文档、字段映射文档、接口文档、查询语法说明、测试报告、外采服务对比报告、联调报告、部署说明和交付说明。
9. 支持后续持续迭代，包括查询语法增强、检索质量优化、性能优化、日志审计、权限控制、缓存等能力。

---

## 3. 替换对象与兼容原则

### 3.1 信息来源说明

本项目的兼容对象主要来自《专利检索 MCP 参数与字段依赖》文档。该文档记录了现有外采专利检索服务的接口参数、查询语法、返回字段消费方式和相关详情/引证接口参数。

虽然该文档名称中包含 “MCP”，但在本项目中，仅将其作为**现有外采专利检索服务接口与字段依赖说明文档**使用。

### 3.2 替换对象

本项目主要替换现有外采服务中的以下能力：

| 外采服务能力 | 自研服务是否对齐 | 说明 |
|---|---|---|
| `patent_search` | 对齐 | 核心专利检索接口 |
| `patent_get_detail` | 对齐 | 专利详情查询能力 |
| `patent_get_citations` | 对齐 | 引证/相关文献查询能力 |
| 返回字段消费 | 对齐 | 尽量保持字段命名和字段含义一致 |
| 查询参数规范 | 优先兼容 | 降低 SaaS 上层改造成本 |
| 底层实现逻辑 | 不复刻 | 使用自研 OpenSearch 查询逻辑 |

### 3.3 兼容原则

本项目采用：

> 对外兼容，内部自研。

对外兼容是指：

1. 尽量保留外采服务已有参数命名，例如 `q`、`ds`、`sort`、`page`、`page_size`、`highlight`。
2. 尽量保留外采服务返回结果中 SaaS 已经依赖的字段，例如 `patent_id`、`applicationNumber`、`documentNumber`、`title`、`abstract`、`applicant`、`currentAssignee`、`mainIpc`、`ipcMainList`、`applicationDate`、`documentDate`、`legalStatus`、`type`、`total` 等。
3. 尽量减少 SaaS 上层系统改造成本，优先实现“替换接口地址后即可联调”的效果。
4. 对外接口表现尽量接近外采服务，但不要求检索结果、排序结果、高亮结果与外采服务完全一致。

内部自研是指：

1. 查询语法解析由自研服务实现。
2. OpenSearch DSL 构造由自研服务实现。
3. 字段映射由自研服务实现。
4. 结果格式化由自研服务实现。
5. 异常处理、日志记录、分页排序、参数校验等由自研服务统一控制。
6. 底层数据完全来自公司自有 OpenSearch `patent_index`。

---

## 4. 项目整体路径

本项目从 0 到 1 到交付上线，建议分为以下阶段推进：

```text
阶段一：需求边界确认
阶段二：部署环境前置确认
阶段三：字段映射与接口设计
阶段四：项目骨架搭建
阶段五：最小检索链路跑通
阶段六：核心查询能力补齐
阶段七：详情与引证能力补齐
阶段八：接口兼容与异常处理完善
阶段九：测试与外采服务对比
阶段十：SaaS 联调与灰度验证
阶段十一：部署上线与交付沉淀
阶段十二：后续迭代优化
```

每个阶段都需要形成明确交付物，避免只写代码、不留文档、不便交接。

---

# 阶段一：需求边界确认

## 1. 阶段目标

明确本项目要做什么、不做什么、优先做什么、后续再做什么，防止开发过程中需求发散。

本阶段重点不是技术实现，而是明确项目边界。

## 2. 主要工作

1. 梳理现有 SaaS 平台依赖哪些专利检索能力。
2. 梳理外采服务当前提供哪些核心接口。
3. 明确自研检索服务第一版必须支持哪些能力。
4. 明确哪些能力暂不进入第一版。
5. 明确自研服务与外采服务的替换关系。
6. 明确对外兼容原则。
7. 明确最终交付物类型。

## 3. 当前确认范围

本项目必须建设以下接口：

```text
POST /api/patent/search
GET  /api/patent/detail/{patent_id}
GET  /api/patent/citations/{patent_id}
```

其中，第一优先级是：

```text
POST /api/patent/search
```

## 4. 当前不做范围

本项目第一轮建设不做以下内容：

1. 不做前端页面。
2. 不做 SaaS 业务流程改造。
3. 不做专利数据入库、清洗、同步任务。
4. 不做报告生成。
5. 不做 AI 分析能力。
6. 不做专利图片/PDF 文件服务。
7. 不做复杂权限计费体系。
8. 不要求完全复刻外采服务的召回结果和排序结果。
9. 不要求一次性支持所有复杂查询语法。

## 5. 阶段交付物

```text
docs/requirement.md
《自研专利检索服务需求说明与实施路径》
```

## 6. 阶段验收标准

1. 项目背景说明清晰。
2. 替换对象说明清晰。
3. 接口范围说明清晰。
4. 不做事项说明清晰。
5. 后续实施路径说明清晰。
6. 团队可以基于该文档判断项目边界。

---

# 阶段二：部署环境前置确认

## 1. 阶段目标

在正式开发前，提前确认服务器基础环境、运行条件、网络连通性和部署约束，避免后续服务开发完成后才发现服务器无法部署、无法访问 OpenSearch、端口无法开放、缺少运行环境或权限不足等问题。

本阶段不要求立即部署专利检索服务，只要求完成部署环境摸底，并形成部署环境确认文档。

## 2. 主要工作

1. 确认服务器基础信息。
2. 确认登录账号和权限。
3. 确认操作系统版本。
4. 确认 CPU、内存、磁盘资源。
5. 确认 Python、pip、venv 等运行环境。
6. 确认是否支持 Docker。
7. 确认是否支持 systemd。
8. 确认是否已有 Nginx。
9. 确认服务器是否能够访问 OpenSearch。
10. 确认 OpenSearch 端口和鉴权是否可用。
11. 确认 SaaS 后端是否能够访问该服务器。
12. 确认服务端口是否可以开放。
13. 确认是否需要内网访问、白名单、安全组或反向代理。
14. 确认代码目录、配置目录、日志目录规划。
15. 记录部署风险和待确认事项。

## 3. 重点确认项

### 3.1 服务器基础信息

需要确认：

```text
服务器 IP
操作系统版本
CPU 核数
内存大小
磁盘容量
磁盘剩余空间
服务器用途：测试环境 / 灰度环境 / 生产环境
```

建议执行命令：

```bash
uname -a
cat /etc/os-release
lscpu
free -h
df -h
ip addr
```

### 3.2 登录与权限

需要确认：

```text
登录账号
是否有 sudo 权限
是否允许安装软件
是否允许使用 Docker
是否允许配置 systemd
是否允许修改防火墙
是否允许开放端口
```

建议执行命令：

```bash
whoami
id
sudo -l
```

### 3.3 运行环境

需要确认：

```text
Python 版本
pip 是否可用
venv 是否可用
Docker 是否可用
Docker Compose 是否可用
Nginx 是否存在
```

建议执行命令：

```bash
python3 --version
pip3 --version
python3 -m venv --help
docker --version
docker compose version
nginx -v
```

### 3.4 OpenSearch 连通性

这是本阶段最重要的确认项之一。

需要确认：

```text
服务器是否能访问 OpenSearch Host
OpenSearch 端口是否可达
OpenSearch 鉴权是否可用
是否需要加入白名单
是否需要内网访问
是否需要代理
```

建议执行命令：

```bash
curl -I https://<OPENSEARCH_HOST>:9200
```

如果需要账号密码：

```bash
curl -u <OPENSEARCH_USER>:<OPENSEARCH_PASS> https://<OPENSEARCH_HOST>:9200
```

判断方式：

```text
连接超时：网络不通或端口不通
401/403：网络通，但鉴权失败或权限不足
返回 OpenSearch 信息：连接基本可用
```

### 3.5 服务访问与端口

需要确认：

```text
后端服务计划监听哪个端口
该端口是否允许开放
SaaS 后端是否能访问该服务器
是否需要 Nginx 反向代理
是否需要 HTTPS
是否需要域名
是否只能内网访问
```

建议执行命令：

```bash
ss -tulnp
```

### 3.6 目录与日志

需要确认：

```text
代码部署目录
配置文件目录
日志目录
当前用户是否有写权限
是否需要日志轮转
磁盘空间是否足够
```

建议目录规划：

```text
/opt/patent-search-service
/etc/patent-search-service/.env
/var/log/patent-search-service
```

如果没有 sudo 权限，可以先采用用户目录：

```text
~/apps/patent-search-service
~/logs/patent-search-service
```

## 4. 阶段交付物

```text
docs/deploy_env_check.md
《部署环境确认清单》
```

## 5. 文档内容建议

《部署环境确认清单》建议包含：

```text
# 部署环境确认清单

## 1. 服务器基础信息
- 服务器 IP：
- 操作系统：
- CPU：
- 内存：
- 磁盘：
- 环境类型：

## 2. 登录与权限
- 登录账号：
- 是否有 sudo：
- 是否可安装软件：
- 是否可使用 Docker：
- 是否可开放端口：

## 3. 运行环境
- Python 版本：
- pip：
- venv：
- Docker：
- Docker Compose：
- Nginx：

## 4. OpenSearch 连通性
- OpenSearch Host：
- OpenSearch Port：
- 是否可访问：
- 鉴权是否通过：
- 是否需要白名单：
- 是否需要代理：

## 5. 服务访问与端口
- 计划服务端口：
- 服务器本机是否可访问：
- SaaS 后端是否可访问：
- 是否需要 Nginx：
- 是否需要 HTTPS：
- 是否需要域名：

## 6. 目录与日志
- 代码目录：
- 配置目录：
- 日志目录：
- 是否有写权限：
- 是否需要日志轮转：

## 7. 风险与待确认事项
- 
```

## 6. 阶段验收标准

1. 已确认服务器基础配置。
2. 已确认登录账号和权限。
3. 已确认运行环境是否满足 Python/FastAPI 服务部署要求。
4. 已确认是否支持 Docker 或 systemd。
5. 已确认服务器是否能够访问 OpenSearch。
6. 已确认服务端口和访问方式。
7. 已确认代码、配置、日志目录规划。
8. 已记录部署风险和待确认事项。
9. 已形成《部署环境确认清单》。

## 7. 本阶段结论要求

本阶段结束后，需要明确给出一个结论：

```text
当前服务器是否满足后续部署 patent-search-service 的基本条件？
```

结论可以分为三类：

```text
满足：可以作为后续部署环境。
基本满足：存在少量待处理问题，但不影响开发推进。
暂不满足：存在阻塞问题，需要先处理服务器、网络或权限问题。
```

---

# 阶段三：字段映射与接口设计

## 1. 阶段目标

明确外采服务字段与自有 OpenSearch 字段之间的对应关系，并固定自研 API 的请求和响应结构。

这是项目最重要的设计阶段之一。

如果字段映射不清楚，后续代码会混乱；如果返回结构不固定，SaaS 联调会反复修改。

## 2. 主要工作

1. 梳理外采服务查询字段。
2. 梳理 OpenSearch 可用字段。
3. 建立查询字段映射表。
4. 建立返回字段映射表。
5. 设计统一 API 返回结构。
6. 设计错误返回结构。
7. 设计分页规则。
8. 设计排序规则。
9. 设计空值处理规则。
10. 设计数组字段处理规则。

## 3. 查询字段映射

初步建议如下：

| 外部查询字段 | 含义 | OpenSearch 字段 |
|---|---|---|
| `title` | 标题 | `Title`, `TitleCN`, `TitleEN`, `TitleOriginal` |
| `ab` | 摘要 | `Abstract`, `AbstractCN`, `AbstractEN`, `AbstractOriginal` |
| `tscd` | 标题+摘要+权利要求+说明书 | `Title`, `Abstract`, `MainClaim`, `Requirement`, `Instructions` |
| `ipc` | IPC 分类 | `IPC`, `IPCList`, `IPCSmallCategory`, `IPCLargeGroup`, `IPCSmallGroup` |
| `applicant` | 申请人 | `Applicant`, `ApplicantNormalized`, `FirstApplicant` |
| `currentAssignee` | 当前权利人 | `Assignee`, `AssigneeNormalized` |
| `ad` | 申请日 | `ApplicationDate` |
| `documentYear` | 公开年 | `PublicationDate` |
| `legalStatus` | 法律状态 | `LatestLegalStatus`, `LegalStatus`, `LegalStatusCode` |
| `type` | 专利类型 | `Type`, `PatentTypeCode`, `Kind` |

## 4. 返回字段映射

初步建议如下：

| 对外返回字段 | OpenSearch 字段 | 说明 |
|---|---|---|
| `id` | `patent_id` | 专利内部 ID |
| `patent_id` | `patent_id` | 专利内部 ID |
| `applicationNumber` | `ApplicationNumber` | 申请号 |
| `documentNumber` | `PublicationNumber` | 公开号/公告号 |
| `title` | `Title` | 标题 |
| `ti` | `Title` | 标题别名 |
| `abstract` | `Abstract` | 摘要 |
| `ab` | `Abstract` | 摘要别名 |
| `applicant` | `Applicant` | 申请人 |
| `pa` | `Applicant` | 申请人别名 |
| `currentAssignee` | `Assignee` | 当前权利人 |
| `inventor` | `Inventor` | 发明人 |
| `mainIpc` | `IPC` | 主 IPC |
| `ipcMainList` | `IPCList` | IPC 列表 |
| `applicationDate` | `ApplicationDate` | 申请日 |
| `ad` | `ApplicationDate` | 申请日别名 |
| `documentDate` | `PublicationDate` | 公开日/公告日 |
| `legalStatus` | `LatestLegalStatus` / `LegalStatus` | 法律状态 |
| `currentStatus` | `LatestLegalStatus` | 当前状态 |
| `type` | `Type` | 专利类型 |
| `total` | OpenSearch `hits.total` | 总命中数 |

## 5. API 设计

### 5.1 专利检索接口

```http
POST /api/patent/search
```

请求示例：

```json
{
  "q": "ipc:H02M AND tscd:(\"均衡\" OR \"平衡\")",
  "ds": "cn",
  "sort": "relation",
  "page": 1,
  "page_size": 50,
  "highlight": 0
}
```

返回示例：

```json
{
  "success": true,
  "code": 0,
  "message": "ok",
  "data": {
    "total": 128,
    "page": 1,
    "page_size": 50,
    "records": [
      {
        "id": "cn-xxx",
        "patent_id": "cn-xxx",
        "applicationNumber": "CN202411108082.1",
        "documentNumber": "CN119188170B",
        "title": "一种轴承座壳体的加工工艺",
        "ti": "一种轴承座壳体的加工工艺",
        "abstract": "本发明公开了一种...",
        "ab": "本发明公开了一种...",
        "applicant": "某某公司",
        "pa": "某某公司",
        "currentAssignee": "某某公司",
        "inventor": "张三;李四",
        "mainIpc": "B23P15/00",
        "ipcMainList": ["B23P15/00", "B23Q3/00"],
        "applicationDate": "2024-08-13",
        "ad": "2024-08-13",
        "documentDate": "2026-06-12",
        "legalStatus": "授权",
        "currentStatus": "授权",
        "type": "发明专利",
        "score": 12.45
      }
    ]
  }
}
```

### 5.2 专利详情接口

```http
GET /api/patent/detail/{patent_id}
```

请求示例：

```http
GET /api/patent/detail/cn-xxx?include_description=true
```

返回内容包括：

1. 基础著录信息。
2. 标题。
3. 摘要。
4. 申请人。
5. 当前权利人。
6. 发明人。
7. IPC 分类。
8. 法律状态。
9. 权利要求。
10. 说明书。
11. 附图信息。
12. 同族信息。
13. 引证/相关文献信息。

是否返回长文本字段由 `include_description` 控制。

### 5.3 引证/相关文献接口

```http
GET /api/patent/citations/{patent_id}
```

数据来源优先使用：

```text
ReferencesCited
ReferencesCitedRaw
ReferencesCitedText
RelatedDocuments
```

## 6. 阶段交付物

```text
docs/field_mapping.md
《字段映射说明》

docs/api_spec.md
《API 接口设计说明》
```

## 7. 阶段验收标准

1. 查询字段映射表完整。
2. 返回字段映射表完整。
3. search/detail/citations 三个接口路径明确。
4. 请求参数明确。
5. 返回结构明确。
6. 错误结构明确。
7. OpenSearch 字段来源明确。

---

# 阶段四：项目骨架搭建

## 1. 阶段目标

搭建一个可运行的后端服务基础框架，为后续检索逻辑开发提供稳定结构。

## 2. 推荐项目形态

项目名称建议为：

```text
patent-search-service
```

推荐技术栈：

```text
Python
FastAPI
OpenSearch Python Client
Pydantic
Uvicorn
Docker
```

## 3. 推荐目录结构

```text
patent-search-service/
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── search.py
│   │   ├── detail.py
│   │   └── citations.py
│   ├── core/
│   │   ├── config.py
│   │   ├── exceptions.py
│   │   └── logging.py
│   ├── schemas/
│   │   ├── search.py
│   │   ├── detail.py
│   │   └── response.py
│   ├── services/
│   │   ├── search_service.py
│   │   ├── detail_service.py
│   │   └── citation_service.py
│   ├── query/
│   │   ├── parser.py
│   │   └── dsl_builder.py
│   ├── mappings/
│   │   ├── field_mapping.py
│   │   ├── query_field_mapping.py
│   │   ├── result_mapper.py
│   │   └── legal_status_mapping.py
│   ├── repositories/
│   │   └── opensearch_repo.py
│   └── utils/
│       └── pagination.py
├── docs/
├── scripts/
├── tests/
├── .env.example
├── requirements.txt
├── README.md
└── Dockerfile
```

## 4. 模块职责

| 模块 | 职责 |
|---|---|
| `api/` | 接收 HTTP 请求，返回 HTTP 响应 |
| `schemas/` | 定义请求体、响应体、字段类型 |
| `services/` | 编排业务流程 |
| `query/` | 解析查询语法，构造查询对象 |
| `mappings/` | 字段映射、状态映射、结果映射 |
| `repositories/` | 访问 OpenSearch |
| `core/` | 配置、日志、异常 |
| `tests/` | 单元测试和接口测试 |
| `scripts/` | 调试脚本、冒烟测试脚本 |
| `docs/` | 项目文档 |

## 5. 阶段交付物

```text
可启动的后端项目
README.md
.env.example
Dockerfile
GET /health 健康检查接口
OpenSearch 配置读取能力
```

## 6. 阶段验收标准

1. 项目可以本地启动。
2. `/health` 接口正常返回。
3. `.env` 配置可以被读取。
4. OpenSearch 连接配置已封装。
5. 项目目录结构清晰。
6. 后续模块可以按结构继续开发。

---

# 阶段五：最小检索链路跑通

## 1. 阶段目标

先跑通从 API 到 OpenSearch 再到统一返回的完整链路。

该阶段不追求复杂查询语法，重点是证明：

```text
HTTP 请求 → 参数校验 → OpenSearch 查询 → 字段映射 → 返回结果
```

整条链路可用。

## 2. 主要工作

1. 实现 `POST /api/patent/search`。
2. 实现 OpenSearch 基础查询。
3. 实现简单关键词查询。
4. 实现 `title:(xxx)` 查询。
5. 实现 `ab:(xxx)` 查询。
6. 实现 `ipc:xxx` 查询。
7. 实现 `ad:[x TO y]` 查询。
8. 实现分页。
9. 实现基础排序。
10. 实现第一版字段映射。
11. 实现冒烟测试脚本。

## 3. 最小支持语法

| 语法 | 示例 | 查询字段 |
|---|---|---|
| 普通关键词 | `阀门` | `Title`, `Abstract` |
| 标题检索 | `title:(阀门)` | `Title`, `TitleCN`, `TitleEN` |
| 摘要检索 | `ab:(缓冲)` | `Abstract`, `AbstractCN`, `AbstractEN` |
| IPC 检索 | `ipc:H02M` | `IPC`, `IPCList`, `IPCSmallCategory` |
| 申请日范围 | `ad:[2020-01-01 TO 2020-12-31]` | `ApplicationDate` |

## 4. 阶段交付物

```text
POST /api/patent/search 初版
基础查询 DSL Builder
基础字段映射 ResultMapper
scripts/smoke_test.py
docs/query_syntax.md 初版
```

## 5. 阶段验收标准

1. `POST /api/patent/search` 可以调用。
2. 可以查到真实 OpenSearch 数据。
3. 返回结果包含 `total` 和 `records`。
4. 返回字段符合初版字段映射规则。
5. 支持分页。
6. 支持基础排序。
7. 冒烟测试脚本可以跑通。

---

# 阶段六：核心查询能力补齐

## 1. 阶段目标

补齐 SaaS 常用的核心检索能力，使自研服务具备替代外采服务的基础条件。

## 2. 主要工作

1. 支持 `tscd:(...)` 全文检索。
2. 支持 `applicant:(...)` 申请人检索。
3. 支持 `currentAssignee:(...)` 当前权利人检索。
4. 支持 `legalStatus:(...)` 法律状态检索。
5. 支持 `type:(...)` 专利类型检索。
6. 支持 `documentYear:[x TO y]` 公开年范围检索。
7. 支持简单 `AND`。
8. 支持简单 `OR`。
9. 支持基础括号分组。
10. 支持 `ds=cn/all`。
11. 支持 `sort=relation/!applicationDate`。
12. 支持 `highlight=0/1` 参数。
13. 完善查询语法错误提示。

## 3. 核心查询语法

| 查询能力 | 示例 |
|---|---|
| 全文检索 | `tscd:(均衡)` |
| 全文 OR 检索 | `tscd:("均衡" OR "平衡")` |
| IPC + 全文 | `ipc:H02M AND tscd:(均衡)` |
| 申请人 | `applicant:(华为技术有限公司)` |
| 当前权利人 | `currentAssignee:(华为技术有限公司)` |
| 法律状态 | `legalStatus:(有效专利)` |
| 专利类型 | `type:(发明专利)` |
| 公开年 | `documentYear:[2020 TO 2024]` |

## 4. 法律状态处理原则

法律状态初版先采用基础映射，不追求完全精确。

示例：

| 外部查询值 | OpenSearch 字段判断 |
|---|---|
| 有效专利 | `LatestLegalStatus` 包含授权、有效等状态 |
| 在审 | `LatestLegalStatus` 包含公开、实质审查等状态 |
| 失效 | `LatestLegalStatus` 包含终止、届满、撤回、驳回等状态 |

后续需要根据业务方口径单独维护法律状态规则表。

## 5. 阶段交付物

```text
增强版 query parser
增强版 dsl_builder
legal_status_mapping.py
query_syntax.md 完整版
compatibility.md 初版
```

## 6. 阶段验收标准

1. 核心查询语法可用。
2. `tscd` 可以检索标题、摘要、权利要求、说明书。
3. `applicant` 和 `currentAssignee` 可以检索。
4. `AND` / `OR` 简单组合可用。
5. `ds` 数据范围过滤可用。
6. `sort` 排序可用。
7. 查询语法错误有明确提示。
8. 查询语法支持范围有文档记录。

---

# 阶段七：详情与引证能力补齐

## 1. 阶段目标

补齐检索之后的详情读取和引证/相关文献读取能力。

检索列表只能解决“找到哪些专利”，详情和引证接口解决“进一步读取专利内容”和“继续分析相关文献”的问题。

## 2. 专利详情接口工作内容

实现：

```http
GET /api/patent/detail/{patent_id}
```

主要工作：

1. 根据 `patent_id` 查询 OpenSearch。
2. 返回基础著录信息。
3. 返回标题、摘要、申请人、权利人、发明人。
4. 返回 IPC、CPC 等分类信息。
5. 返回法律状态。
6. 返回权利要求。
7. 返回说明书。
8. 返回附图信息。
9. 返回同族信息。
10. 支持 `include_description` 控制长文本是否返回。

## 3. 引证/相关文献接口工作内容

实现：

```http
GET /api/patent/citations/{patent_id}
```

主要工作：

1. 根据 `patent_id` 查询目标专利。
2. 读取 `ReferencesCited`。
3. 读取 `ReferencesCitedRaw`。
4. 读取 `ReferencesCitedText`。
5. 读取 `RelatedDocuments`。
6. 统一格式化返回。

## 4. 阶段交付物

```text
GET /api/patent/detail/{patent_id}
GET /api/patent/citations/{patent_id}
detail_service.py
citation_service.py
detail/citations 接口文档
```

## 5. 阶段验收标准

1. 可以根据 `patent_id` 查询专利详情。
2. 可以控制是否返回长文本字段。
3. 可以返回权利要求和说明书。
4. 可以返回法律状态。
5. 可以返回引证文献或相关文献信息。
6. 查询不到专利时有明确错误返回。

---

# 阶段八：接口兼容与异常处理完善

## 1. 阶段目标

使服务从“能跑”变成“可联调、可排错、可交付”。

## 2. 主要工作

1. 统一成功返回结构。
2. 统一失败返回结构。
3. 增加参数校验。
4. 增加 `page_size` 最大值限制。
5. 增加 `q` 长度限制。
6. 增加查询超时处理。
7. 增加 OpenSearch 异常处理。
8. 增加查询语法错误处理。
9. 增加日志记录。
10. 增加请求耗时记录。
11. 增加慢查询记录。
12. 增加接口兼容说明文档。
13. 增加已知差异说明。

## 3. 错误返回示例

```json
{
  "success": false,
  "code": 40001,
  "message": "q 查询语法错误：缺少右括号",
  "data": null
}
```

## 4. 常见错误码建议

| 错误码 | 含义 |
|---|---|
| `0` | 成功 |
| `40001` | 查询语法错误 |
| `40002` | 参数非法 |
| `40003` | page 或 page_size 非法 |
| `40401` | 专利不存在 |
| `50001` | OpenSearch 查询异常 |
| `50002` | 服务内部异常 |

## 5. 阶段交付物

```text
统一异常处理
统一错误码
日志记录
接口兼容说明 compatibility.md
已知差异说明 known_issues.md
```

## 6. 阶段验收标准

1. 参数错误能返回明确提示。
2. 查询语法错误能返回明确提示。
3. OpenSearch 异常不会导致服务崩溃。
4. 查询耗时可记录。
5. 慢查询可追踪。
6. 接口兼容范围有文档说明。
7. 已知差异有文档说明。

---

# 阶段九：测试与外采服务对比

## 1. 阶段目标

验证自研检索服务是否满足替换外采服务的基础条件。

## 2. 测试类型

### 2.1 接口测试

测试内容包括：

1. 正常查询。
2. 无结果查询。
3. 参数缺失。
4. 参数非法。
5. page 非法。
6. page_size 超限。
7. sort 非法。
8. ds 非法。
9. q 语法错误。
10. OpenSearch 异常。

### 2.2 查询语法测试

测试样例包括：

```text
title:(阀门)
ab:(缓冲)
tscd:(均衡)
tscd:("均衡" OR "平衡")
ipc:H02M
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
ad:[2020-01-01 TO 2020-12-31]
documentYear:[2020 TO 2024]
ipc:H02M AND tscd:(均衡)
```

### 2.3 字段映射测试

检查内容包括：

1. `documentNumber` 是否来自 `PublicationNumber`。
2. `applicationNumber` 是否来自 `ApplicationNumber`。
3. `title` 是否来自 `Title`。
4. `abstract` 是否来自 `Abstract`。
5. `applicant` 是否来自 `Applicant`。
6. `currentAssignee` 是否来自 `Assignee`。
7. `mainIpc` 是否来自 `IPC`。
8. `ipcMainList` 是否来自 `IPCList`。
9. `applicationDate` 是否来自 `ApplicationDate`。
10. `documentDate` 是否来自 `PublicationDate`。
11. `legalStatus` 是否来自 `LatestLegalStatus` 或 `LegalStatus`。
12. `type` 是否来自 `Type`。

### 2.4 外采服务对比测试

选取典型查询，分别调用外采服务和自研服务，对比：

1. `total` 差异。
2. Top 结果差异。
3. 标题摘要字段完整度。
4. 申请人/权利人字段完整度。
5. IPC 字段完整度。
6. 法律状态差异。
7. 响应时间差异。
8. 排序差异。
9. 高亮差异。

注意：

```text
不要求自研服务与外采服务结果完全一致。
对比目的不是证明结果完全相同，而是识别差异、解释差异、判断是否满足 SaaS 核心业务使用。
```

## 3. 阶段交付物

```text
tests/
scripts/smoke_test.py
docs/test_report.md
docs/vendor_compare_report.md
```

## 4. 阶段验收标准

1. 核心接口测试通过。
2. 核心查询语法测试通过。
3. 字段映射测试通过。
4. 外采服务对比报告完成。
5. 主要差异可解释。
6. 明确是否具备 SaaS 联调条件。

---

# 阶段十：SaaS 联调与灰度验证

## 1. 阶段目标

让 SaaS 平台或业务后端接入自研检索服务，验证实际业务流程是否可用。

## 2. 主要工作

1. 提供测试环境接口地址。
2. 提供接口文档。
3. 提供调用示例。
4. 协助 SaaS 调用方替换接口地址。
5. 验证 search 接口调用。
6. 验证 detail 接口调用。
7. 验证 citations 接口调用。
8. 验证字段读取是否正常。
9. 验证分页、排序、筛选是否正常。
10. 验证异常场景处理。
11. 收集联调问题。
12. 修复兼容问题。
13. 形成联调问题清单。

## 3. 联调重点

| 联调项 | 说明 |
|---|---|
| 参数兼容 | SaaS 原有参数是否可直接传入 |
| 返回字段兼容 | SaaS 原有字段读取是否正常 |
| 分页兼容 | 页码和每页数量是否符合预期 |
| 排序兼容 | 相关性排序和申请日倒序是否可用 |
| 错误兼容 | 异常时 SaaS 是否能正常处理 |
| 性能可接受 | 典型查询响应时间是否可接受 |
| 结果可解释 | 查询结果与业务预期是否接近 |

## 4. 阶段交付物

```text
docs/integration_report.md
《SaaS 联调报告》

docs/integration_issues.md
《联调问题清单》
```

## 5. 阶段验收标准

1. SaaS 能够成功调用自研 search 接口。
2. SaaS 能够成功读取返回字段。
3. SaaS 能够调用详情接口。
4. SaaS 能够调用引证接口。
5. 主要业务流程可跑通。
6. 联调问题已有记录。
7. 阻塞性问题已解决或有明确处理方案。

---

# 阶段十一：部署上线与交付沉淀

## 1. 阶段目标

将自研检索服务部署到目标环境，并形成可维护、可交接、可追踪的正式交付物。

## 2. 主要工作

1. 编写部署说明。
2. 完善 Dockerfile。
3. 整理环境变量说明。
4. 配置 OpenSearch 连接参数。
5. 配置日志目录。
6. 配置服务端口。
7. 配置健康检查。
8. 配置基础访问控制。
9. 部署到测试环境或灰度环境。
10. 验证服务启动。
11. 验证健康检查。
12. 验证核心接口。
13. 整理交付说明。
14. 整理已知问题。
15. 整理后续优化计划。

## 3. 部署配置示例

`.env.example` 建议包含：

```env
OPENSEARCH_HOST=
OPENSEARCH_PORT=9200
OPENSEARCH_USE_HTTPS=true
OPENSEARCH_USER=
OPENSEARCH_PASS=
OPENSEARCH_INDEX=patent_index

API_HOST=0.0.0.0
API_PORT=8000

DEFAULT_PAGE_SIZE=50
MAX_PAGE_SIZE=100
QUERY_TIMEOUT_SECONDS=10
```

## 4. 阶段交付物

```text
README.md
.env.example
Dockerfile
docs/deploy.md
docs/delivery.md
docs/known_issues.md
docs/next_plan.md
```

## 5. 阶段验收标准

1. 服务可以在目标环境启动。
2. `/health` 接口正常。
3. search/detail/citations 核心接口可用。
4. 环境变量配置清晰。
5. 部署步骤清晰。
6. 交付文档完整。
7. 已知问题有记录。
8. 后续优化方向明确。

---

# 阶段十二：后续迭代优化

## 1. 阶段目标

在完成基础替换后，继续提升检索能力、稳定性、性能和业务适配程度。

## 2. 后续优化方向

### 2.1 查询语法增强

1. 支持更复杂的括号嵌套。
2. 支持更完整的 `NOT` 逻辑。
3. 支持更多字段查询。
4. 支持更灵活的日期范围。
5. 支持更复杂的申请人/权利人组合查询。
6. 支持更完整的法律状态查询。

### 2.2 检索质量优化

1. 优化中文分词。
2. 优化标题、摘要、权利要求、说明书的字段权重。
3. 优化 IPC 查询策略。
4. 优化申请人/权利人规范化匹配。
5. 优化排序规则。
6. 优化高亮效果。
7. 建立典型查询评估集。

### 2.3 性能优化

1. 增加查询超时控制。
2. 增加慢查询分析。
3. 限制深分页。
4. 增加缓存。
5. 优化 OpenSearch 查询 DSL。
6. 优化返回字段裁剪。
7. 对超长字段进行按需返回。

### 2.4 稳定性与可运维性

1. 增加日志分级。
2. 增加请求追踪 ID。
3. 增加接口调用统计。
4. 增加错误统计。
5. 增加健康检查。
6. 增加服务监控指标。
7. 增加告警机制。

### 2.5 安全与权限

1. 增加 API Token。
2. 增加调用方识别。
3. 增加访问频率限制。
4. 增加单次查询数量限制。
5. 增加敏感字段控制。
6. 增加导出限制。

### 2.6 新接口形态

在兼容接口稳定后，可以设计结构化查询接口：

```http
POST /api/v2/patent/search
```

示例：

```json
{
  "query": {
    "text": "均衡 平衡",
    "operator": "OR",
    "fields": ["title", "abstract", "claims", "description"]
  },
  "filters": {
    "ipc": ["H02M"],
    "applicant": ["华为技术有限公司"],
    "application_date": {
      "from": "2020-01-01",
      "to": "2020-12-31"
    },
    "legal_status": ["有效专利"],
    "patent_type": ["发明专利"]
  },
  "sort": [
    {
      "field": "applicationDate",
      "order": "desc"
    }
  ],
  "page": 1,
  "page_size": 50
}
```

该接口不用于初始替换外采服务，而是用于后续新业务、新前端或更复杂检索场景。

---

# 5. 项目完整交付物清单

项目从建设到上线，最终应沉淀以下内容：

| 类型 | 交付物 |
|---|---|
| 代码 | `patent-search-service` 后端服务代码 |
| 配置 | `.env.example` |
| 部署 | `Dockerfile` |
| 文档 | `README.md` |
| 文档 | `docs/requirement.md` 需求说明与实施路径 |
| 文档 | `docs/deploy_env_check.md` 部署环境确认清单 |
| 文档 | `docs/field_mapping.md` 字段映射说明 |
| 文档 | `docs/api_spec.md` API 接口设计说明 |
| 文档 | `docs/query_syntax.md` 查询语法支持说明 |
| 文档 | `docs/compatibility.md` 接口兼容说明 |
| 文档 | `docs/test_report.md` 测试报告 |
| 文档 | `docs/vendor_compare_report.md` 外采服务对比报告 |
| 文档 | `docs/integration_report.md` SaaS 联调报告 |
| 文档 | `docs/deploy.md` 部署说明 |
| 文档 | `docs/delivery.md` 交付说明 |
| 文档 | `docs/known_issues.md` 已知问题 |
| 文档 | `docs/next_plan.md` 后续优化计划 |
| 测试 | `tests/` 单元测试与接口测试 |
| 脚本 | `scripts/smoke_test.py` 冒烟测试脚本 |
| 脚本 | 典型查询样例集 |

---

# 6. 项目最终验收标准

项目达到以下条件，可认为完成从 0 到 1 的交付：

## 6.1 功能验收

1. `POST /api/patent/search` 可用。
2. `GET /api/patent/detail/{patent_id}` 可用。
3. `GET /api/patent/citations/{patent_id}` 可用。
4. 支持核心查询参数：`q`、`ds`、`sort`、`page`、`page_size`、`highlight`。
5. 支持标题检索。
6. 支持摘要检索。
7. 支持全文检索。
8. 支持 IPC 检索。
9. 支持申请人检索。
10. 支持当前权利人检索。
11. 支持申请日范围检索。
12. 支持基础法律状态检索。
13. 支持专利类型检索。
14. 支持分页。
15. 支持排序。
16. 支持基础高亮或保留高亮参数。

## 6.2 字段验收

检索结果至少包含：

```text
total
records
patent_id
applicationNumber
documentNumber
title
abstract
applicant
currentAssignee
inventor
mainIpc
ipcMainList
applicationDate
documentDate
legalStatus
currentStatus
type
```

## 6.3 兼容验收

1. 兼容《专利检索 MCP 参数与字段依赖》文档中的核心参数。
2. 兼容现有 SaaS 可能消费的核心返回字段。
3. 与外采服务的差异有文档记录。
4. SaaS 联调中发现的问题有明确记录和处理方案。

## 6.4 稳定性验收

1. 服务可正常启动。
2. 健康检查正常。
3. OpenSearch 异常时服务不崩溃。
4. 查询语法错误有明确提示。
5. 参数错误有明确提示。
6. 查询耗时可记录。
7. 慢查询可追踪。
8. 日志可查看。

## 6.5 交付验收

1. 代码仓库完整。
2. README 完整。
3. 环境变量说明完整。
4. 部署说明完整。
5. 接口文档完整。
6. 字段映射说明完整。
7. 查询语法说明完整。
8. 测试报告完整。
9. 外采服务对比报告完整。
10. 已知问题和后续计划完整。

---

# 7. 风险与应对

## 7.1 查询结果与外采服务不一致

风险说明：

由于底层数据库、字段结构、分词器、排序策略和法律状态口径不同，自研服务查询结果不可能与外采服务完全一致。

应对方式：

1. 不以“结果完全一致”作为验收标准。
2. 通过典型查询进行对比。
3. 记录差异。
4. 分析差异原因。
5. 对 SaaS 核心场景优先优化。

## 7.2 查询语法复杂度过高

风险说明：

如果一次性完整支持所有查询语法，开发复杂度会迅速上升。

应对方式：

1. 优先支持高频查询语法。
2. 复杂语法进入后续迭代。
3. 在查询语法文档中明确支持范围。
4. 对不支持语法返回清晰错误提示。

## 7.3 字段数据质量不稳定

风险说明：

OpenSearch 中部分字段可能存在空值、数组、格式不一致、样例不完整等情况。

应对方式：

1. 字段映射时定义空值处理规则。
2. 对数组字段统一格式化。
3. 对长文本字段按需返回。
4. 对关键字段进行字段完整度测试。

## 7.4 法律状态口径不一致

风险说明：

外采服务可能使用归类后的法律状态，而自有库中存在原始法律状态字段。二者需要映射。

应对方式：

1. 初版采用基础映射。
2. 后续维护法律状态规则表。
3. 与业务方确认有效、在审、失效等口径。
4. 对法律状态差异单独记录。

## 7.5 SaaS 仍需少量改造

风险说明：

即使自研服务尽量兼容外采服务参数和返回字段，如果接口路径、鉴权、错误码、字段层级存在差异，SaaS 仍可能需要少量调整。

应对方式：

1. 优先保持核心参数一致。
2. 优先保持核心返回字段一致。
3. 联调前输出接口兼容说明。
4. 联调期间记录并修复兼容问题。
5. 必要时增加适配层。

## 7.6 部署环境不满足要求

风险说明：

服务器可能存在权限不足、无法访问 OpenSearch、端口无法开放、运行环境不完整、无法使用 Docker 或磁盘空间不足等问题。

应对方式：

1. 将部署环境确认前置。
2. 形成《部署环境确认清单》。
3. 提前验证 OpenSearch 连通性。
4. 提前确认服务访问端口。
5. 提前记录阻塞项并推动解决。

---

# 8. 结论

本项目不是简单开发一个搜索接口，而是建设公司自有专利检索能力的基础后端。

项目的核心路径是：

```text
先明确需求边界
再确认部署环境
再完成字段映射
再设计 API 契约
再搭建服务骨架
再跑通最小检索链路
再补齐核心查询能力
再实现详情和引证接口
再完善异常与兼容
再进行测试和外采服务对比
再完成 SaaS 联调
最后部署上线并沉淀交付文档
```

项目的核心原则是：

```text
对外尽量兼容外采服务，降低 SaaS 改造成本；
对内完全基于自有 OpenSearch 数据库实现，形成公司可控的检索能力；
先完成可用闭环，再持续优化检索质量、性能和稳定性。
```
