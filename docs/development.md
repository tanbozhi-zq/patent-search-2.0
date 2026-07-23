# 开发与发布规范

## 本地开始

项目使用 Python 3.11。首次运行：

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    cp .env.example .env
    make check

.env 只用于本机或服务器，不能提交。真实 API、MCP 或 OpenSearch 凭据只放在安全环境中。

## 日常检查

| 命令 | 用途 |
|---|---|
| make test | 运行全部单元与契约测试 |
| make check | 编译检查加全部测试 |
| make run | 本地启动 FastAPI |

新增或改变行为时，必须同时新增或更新 tests/ 下的测试；测试源码是仓库的一部分，不能放入 .gitignore。

## 分支与提交

1. 从最新 main 创建短期分支，推荐前缀 feature/、fix/、chore/ 或 codex/。
2. 一个分支只解决一个可说明的问题；不混入无关格式化或历史文件清理。
3. 合入 main 前运行 make check，并更新 README 或相关运维文档。
4. 发布记录必须包含 Git 提交号、服务版本、验证结果和回滚点。

## 发布与索引

代码发布遵循 docs/ops/deployment_runbook.md。OpenSearch v2 不是普通配置替换：

- 写入目标、读目标和物理索引必须分别确认；
- 服务生产读路径使用稳定 alias，而不是新物理索引名；
- 切换前必须完成数据对齐、IPC DSL 兼容、serving 设置与固定样本验收；
- 任何生产切换都必须保留旧索引和明确回滚路径。

完整步骤见 docs/ops/opensearch_v2_cutover.md。

## 完成定义

一个改动只有同时满足以下条件才算完成：

1. 代码和测试均提交到 Git。
2. make check 通过。
3. 文档反映当前行为，而不是计划或历史状态。
4. 涉及配置、部署或索引时，已写明验证与回滚方式。
