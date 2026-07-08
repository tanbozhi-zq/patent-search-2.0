# 阶段五测试前端设计文档

## 1. 背景与目标

阶段五已完成最小检索链路 `POST /api/patent/search`，为了方便非开发人员（测试人员、产品、业务方）手动验证检索效果，需要一个简单的 Web 测试页面。

本前端**不是产品功能**，仅作为阶段五及后续阶段的手动测试工具。

## 2. 设计决策

- **形态**：单个 HTML 文件，使用原生 HTML + JavaScript，无构建步骤。
- **挂载方式**：由 FastAPI 通过 `StaticFiles` 在 `/test/` 路径下挂载，确保同源、无 CORS 问题。
- **访问地址**：启动后端后访问 `http://127.0.0.1:8000/test/`。

## 3. 页面功能

页面提供一个表单，包含以下输入：

| 字段 | 默认值 | 说明 |
|---|---|---|
| API Token | 空 | 对应 `X-API-Key`，测试鉴权 |
| 查询式 q | `阀门` | 支持阶段五最小语法 |
| 数据范围 ds | `cn` | 下拉选择 `cn` / `all` |
| 排序 sort | `relation` | 下拉选择 `relation` / `!applicationDate` |
| 页码 page | `1` | 数字输入 |
| 每页条数 page_size | `10` | 数字输入，范围 1-100 |
| 高亮 highlight | `0` | 下拉选择 `0` / `1` |

页面行为：

1. 点击“搜索”按钮后，使用 `fetch` 调用 `POST /api/patent/search`。
2. 在页面下方展示返回的 JSON 结果，并对 `total`、`records` 做可读化展示。
3. 出错时展示 HTTP 状态码和错误详情。
4. 支持清空结果。

## 4. 文件变更

- 新增 `frontend/index.html`（作为 `/test/` 目录的默认页面）
- 修改 `app/main.py`，在路由注册后添加：
  ```python
  from fastapi.staticfiles import StaticFiles
  app.mount("/test", StaticFiles(directory="frontend", html=True), name="frontend")
  ```

## 5. 非目标

本页面不做以下功能：

- 专利详情查询
- 引证文献查询
- 复杂查询语法可视化构建
- 用户登录/权限管理
- 生产环境部署
- 与 SaaS 平台的集成

## 6. 验证方式

1. 启动后端：`uvicorn app.main:app --host 127.0.0.1 --port 8000`
2. 浏览器打开 `http://127.0.0.1:8000/test/`
3. 输入 API Token 和查询条件，点击搜索
4. 确认能正常返回结果、分页、排序、错误提示均正确
