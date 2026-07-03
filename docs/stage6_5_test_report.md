# 阶段 6.5 测试报告

## 测试范围

- `index_analyzer_mode` 参数默认 `compat`
- `normal` 模式保留阶段六普通 `multi_match`
- `compat` 模式对问题字段生成 phrase 查询
- 阶段六合法/非法查询不回退
- 真实 OpenSearch `normal` 与 `compat` 命中量对比

## 代码审查

阶段 6.5 代码审查报告：

```text
docs/stage6_5_code_review.md
```

审查结论：**通过**，未发现阻塞性问题。

## 自动化测试

执行命令：

```bash
source .venv/bin/activate
python3 -m pytest -q
```

结果：

```text
79 passed in 0.05s
```

结论：自动化测试全部通过，阶段六测试无回退。

## 健康检查

```bash
curl http://127.0.0.1:8000/health
```

结果：

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

## 真实 OpenSearch 对比（analyzer-compat smoke）

执行命令：

```bash
python3 scripts/smoke_analyzer_compat.py http://127.0.0.1:8000 test-token
```

结果：

| q | normal total | compat total | 结论 |
|---|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` | 44,286,246 | 19,934,751 | compat 显著降低误召回 |
| `tscd:(图像采集方法)` | 35,700,030 | 18,873,147 | compat 显著降低误召回 |
| `title:(口腔数字印模仪)` | 863,158 | 783,955 | compat 降低误召回 |
| `ab:(药物组合物)` | 6,558,178 | 6,285,148 | compat 降低误召回 |
| `type:(发明专利)` | 30,769,770 | 30,769,770 | 持平 |

所有查询均返回 HTTP 200。

## 阶段六回归抽查

| q | index_analyzer_mode | HTTP | total | 结果 |
|---|---|---:|---:|---|
| `ipc:H02M AND tscd:(均衡)` | compat | 200 | 68,810 | 通过 |
| `NOT title:(外观)` | compat | 200 | 65,642,722 | 通过 |
| `type:(发明专利)` | normal | 200 | 30,769,770 | 通过 |
| `foo:(均衡)` | compat | 400 | code=40001 | 通过 |

阶段六合法/非法查询在 `compat` 默认模式下行为正常。

## 结论

阶段 6.5 测试结论：**通过**

通过依据：

1. 自动化测试全部通过：`79 passed in 0.05s`。
2. `index_analyzer_mode` 默认 `compat`，参数校验正确。
3. `compat` 模式对问题字段生成 phrase 查询，真实 OpenSearch 对比显示误召回显著降低。
4. `normal` 模式保留阶段六普通 DSL。
5. 未修改 OpenSearch 索引或 mapping。
6. 阶段六合法/非法查询不回退。

是否建议进入阶段七：**是**。
