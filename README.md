# pg-mcp — PostgreSQL MCP Server

一个 FastMCP 服务器：将自然语言问题转换为 SQL（OpenAI），用 sqlglot 做安全校验，以只读事务对 PostgreSQL（asyncpg）执行，并可选地用 LLM 校验结果、给出置信度评分。通过 MCP 协议暴露唯一的 `query` 工具。

## 功能特性

- **自然语言 → SQL**：LLM 生成 SQL，携带 schema 上下文；校验失败时带错误反馈重试（指数退避）。
- **纵深安全**：仅允许单条 SELECT（含 CTE / 集合操作）；内置危险函数黑名单（`pg_sleep`、文件操作、`dblink` 等）；表/列黑名单、EXPLAIN 策略可配置；执行期使用只读事务 + `statement_timeout` + 安全 `search_path`。
- **多数据库**：`DATABASES` JSON 或嵌套 env 配置多库，按库名路由执行器（fail-closed）。
- **弹性**：查询/LLM 双层并发限流、重试退避、LLM 调用熔断器。
- **可观测**：Prometheus 指标（请求数/耗时/LLM tokens/安全拒绝）、request_id 全链路追踪、日志敏感信息过滤。

## 安装

```bash
uv sync --all-extras    # 安装运行与开发依赖（Python >= 3.11）
```

## 配置

复制 `.env.example` 为 `.env` 并填写。最低要求：

```dotenv
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=postgres
DATABASE_USER=postgres
DATABASE_PASSWORD=secret
OPENAI_API_KEY=sk-...
```

常用配置组（均有默认值，见 `.env.example`）：`DATABASE_*`、`OPENAI_*`、`SECURITY_*`、`VALIDATION_*`、`CACHE_*`、`RESILIENCE_*`、`OBSERVABILITY_*`。多库示例：

```dotenv
DATABASES={"blog": {"host": "...", "name": "blog_small"}, "shop": {"host": "...", "name": "ecommerce_medium"}}
```

## 运行

```bash
uv run python -m pg_mcp    # 或 uv run pg-mcp，或 uv run python main.py（三者等价，stdio transport）
```

## 测试与质量检查

```bash
uv run pytest                                  # 单元+安全测试（integration 默认排除）
uv run pytest -m integration                   # 需要 live PostgreSQL + OPENAI_API_KEY
uv run pytest --cov=src --cov-report=html      # 覆盖率（分支覆盖，fail_under=80）
uv run mypy src                                # 严格类型检查
uv run ruff check --fix . && uv run ruff format .
cd fixtures && make create-all                 # 建三个测试库（psql）
```

## query 工具

参数：

- `question`（str）：自然语言问题。
- `database`（str | None）：目标库；仅配置一个库时可省略并自动选择。
- `return_type`（`"sql" | "result"`）：只返回生成的 SQL，或执行并返回结果（默认）。

返回（始终为 dict，不抛异常）：

- `success`、`generated_sql`、`data`（columns/rows/row_count/execution_time_ms）、`confidence`（0-100）、`tokens_used`，失败时为 `error: {code, message, details}`。

## 架构

```
MCP client ⇄ stdio ⇄ FastMCP (server.py)
                         │  tool: query(question, database?, return_type)
                         ▼
              QueryOrchestrator (services/orchestrator.py)
    ┌──────────────┬───────────────┬──────────────┬──────────────────┐
    ▼              ▼               ▼              ▼                  ▼
SchemaCache    SQLGenerator    SQLValidator   SQLExecutor      ResultValidator
(cache/)       (OpenAI)        (sqlglot)      (asyncpg,        (LLM, 置信度;
TTL 缓存                       只读校验       只读事务/限行/超时)  非阻塞)
```

详细模块说明见 `CLAUDE.md`；历史改进计划与验收记录见 `IMPROVEMENT_PLAN.md`。
