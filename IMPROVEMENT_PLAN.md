# pg-mcp 功能差距与改进开发计划

> 基于代码审查（2026-09-23，针对 v0.2.1 源码）。所有结论均有代码位置佐证。
> 优先级定义：P0 = 正确性/安全缺陷；P1 = 设计承诺未兑现；P2 = 质量与可维护性。

## 一、审查结论总表

| # | 问题声明 | 结论 | 关键证据 |
|---|---------|------|---------|
| 1 | 多数据库未启用，始终使用单一执行器，请求可能访问错误数据库 | **确认** | `server.py:197` 只向 orchestrator 传入主库执行器；`orchestrator.py:198` 执行时不使用已解析的库名；`settings.py:198` 配置模型只有单库 |
| 2 | 表/列访问限制与 EXPLAIN 策略无法强制实施，敏感对象不受保护 | **确认** | `server.py:153-158` 硬编码 `blocked_tables=None, blocked_columns=None, allow_explain=False`；`SecurityConfig` 无对应字段（校验逻辑本身已实现于 `sql_validator.py:236-283`，只是不可达） |
| 3 | 弹性模块（限流、退避）未整合进请求流程 | **确认** | `_rate_limiter` 创建于 `server.py:187` 但全代码库无任何调用点；`retry_delay`/`backoff_factor`（`settings.py:154-159`）从未被读取，重试循环无退避（`orchestrator.py:377-435`） |
| 4 | 可观测性模块（指标、追踪）未整合 | **确认** | `MetricsCollector` 定义了完整指标（`metrics.py:46-103`）并启动了 HTTP 端口，但请求链路无任何埋点，`/metrics` 永远输出 0；`tracing.py` 除包导出外无任何 import |
| 5 | 响应/模型缺陷（重复 to_dict、未使用配置字段） | **确认** | `QueryResponse` 定义了两个 `to_dict`（`query.py:160-173` 与 `query.py:214-220`），后者覆盖前者，前者的 tokens_used 兜底逻辑为死代码，迫使 `server.py:359-361` 事后补丁；未使用字段见 §4 |
| 6 | 测试覆盖不足，行为难以验证 | **确认** | 无 observability/server 装配测试；integration/e2e 需真实 DB+API Key 且默认不跳过；覆盖率无基线；README 与代码默认值漂移 |

---

## Phase 1（P0）：多数据库执行路由 + 安全控制配置化

**目标**：`query(database=...)` 真正路由到对应数据库；表/列黑名单与 EXPLAIN 策略可通过环境变量配置。

### 现状差距

- `Settings.database` 是单个 `DatabaseConfig`（`settings.py:198`），不存在多库配置结构。`server.py` 虽然构建了 `sql_executors` 字典（`server.py:160-169`），但只把主库执行器传给 orchestrator（`server.py:197`，注释 "Use primary executor"）。
- `orchestrator.py:138` 的 `_resolve_database()` 校验了库名，但 `orchestrator.py:198` 执行时用的是 `self.sql_executor`（单一实例）。**后果：传入 `database="b"` 时校验通过，查询却打到主库。**
- `SQLValidator` 已实现 `_check_blocked_tables` / `_check_blocked_columns`（`sql_validator.py:236-283`）和 `allow_explain` 分支（`sql_validator.py:152-168`），但 `server.py:153-158` 全部硬编码为 None/False，`SecurityConfig` 中没有对应配置项。

### 任务

| 任务 | 内容 | 涉及文件 |
|------|------|---------|
| 1.1 | `Settings` 增加 `databases: dict[str, DatabaseConfig]`，支持 `DATABASES__<NAME>__HOST` 式嵌套 env；保留 `DATABASE_*` 单库 env 向后兼容（映射为默认条目） | `config/settings.py` |
| 1.2 | orchestrator 接收 `sql_executors: dict[str, SQLExecutor]`（替换单一 `sql_executor`），`execute_query` Step 5 按 `database_name` 取执行器 | `services/orchestrator.py` |
| 1.3 | `SecurityConfig` 新增 `blocked_tables: list[str]`、`blocked_columns: list[str]`、`allow_explain: bool = False`，复用 `blocked_functions` 的逗号分隔解析 validator | `config/settings.py` |
| 1.4 | `server.py` 装配：为每个 database 建 pool + executor；`SQLValidator` 从配置传入 blocked_tables/blocked_columns/allow_explain | `server.py` |
| 1.5 | 决策 `allow_write_operations`（当前为死配置）：**推荐直接删除**——只读是本服务的安全底线，保留一个不生效的开关是假承诺；若确需实现，需在 `SQLValidator` 增加对应分支并补充安全测试 | `config/settings.py`, `services/sql_validator.py` |

### 验收标准

- [ ] 集成测试：对 `database="ecommerce_medium"` 的提问，结果来自该库（可用 `fixtures/` 已有的三个库做多库验证，无需新建环境）。**（未完成：需要真实 PostgreSQL 环境）**
- [x] 传入不存在的库名返回 `DATABASE_ERROR`；传入正确库名时，执行器池统计对应该库。（`tests/security/test_multi_db_isolation.py`、`tests/unit/test_orchestrator.py::TestExecutorRouting`）
- [x] 配置 `SECURITY_BLOCKED_TABLES=secrets,audit_log` 后，生成/执行涉及这些表的 SQL 返回 `SECURITY_VIOLATION`；列级同理。（`tests/security/test_blocked_objects.py`，并加固了 schema 限定表名、别名/裸列名绕过缺口）
- [x] `SECURITY_ALLOW_EXPLAIN=true/false` 分别放行/拒绝 EXPLAIN。（`tests/security/test_explain_policy.py`）
- [x] 现有单库 `DATABASE_*` 配置方式行为不变（回归测试）。

---

## Phase 2（P1）：弹性组件整合

**目标**：限流器、重试退避真正进入请求路径；消除双熔断器实例。

### 现状差距

- `MultiRateLimiter`（`rate_limiter.py:197-310`）提供 `for_queries()` / `for_llm()` 上下文管理器，`server.py:187-190` 创建了实例（query_limit=10、llm_limit=5，还是硬编码），但 `query` 工具与 orchestrator 从未调用。
- orchestrator 的重试循环（`orchestrator.py:377-435`）验证失败后直接 `continue`，无任何间隔；`ResilienceConfig.retry_delay` / `backoff_factor` 从未被读取。
- 双熔断器：orchestrator 自建并在用（`orchestrator.py:99-102`），`server.py:181-184` 的全局 `_circuit_breaker` 是从未使用的死实例。
- 错误码已备好但没用上：`ErrorCode.RATE_LIMIT_EXCEEDED` 与 `RateLimitExceededError`（`errors.py:35, 272`）已定义，无触发路径。

### 任务

| 任务 | 内容 | 涉及文件 |
|------|------|---------|
| 2.1 | `query` 工具入口用 `async with _rate_limiter.for_queries(timeout=...)` 包裹 orchestrator 调用；获取失败返回 `RATE_LIMIT_EXCEEDED` 错误响应 | `server.py` |
| 2.2 | 限流参数配置化：`RESILIENCE_QUERY_RATE_LIMIT`、`RESILIENCE_LLM_RATE_LIMIT`（替换硬编码 10/5） | `config/settings.py`, `server.py` |
| 2.3 | 重试退避：在 `_generate_sql_with_retry` 的失败分支加 `await asyncio.sleep(retry_delay * backoff_factor ** attempt)` | `services/orchestrator.py` |
| 2.4 | 删除 `server.py` 的 `_circuit_breaker` 全局死实例（orchestrator 内部实例是唯一真实状态） | `server.py` |

### 验收标准

- [x] 单测：并发超过限值时请求返回 `rate_limit_exceeded`（query 层 `tests/unit/test_server_tool.py`、LLM 层 `tests/unit/test_orchestrator.py::TestResilienceIntegration`）；限流器统计数（`get_all_stats()`）反映拒绝次数（**未断言，遗留**）。
- [x] 单测：monkeypatch `asyncio.sleep` 验证重试间隔按 `retry_delay * backoff_factor^n` 递增。（`test_retry_uses_exponential_backoff`）
- [x] 全代码库无 `_circuit_breaker` 全局残留；`mypy src` 通过。

---

## Phase 3（P1）：可观测性整合

**目标**：`/metrics` 反映真实流量；request_id 贯穿全链路；自动刷新可配置。

### 现状差距

- `MetricsCollector`（单例）已定义 `query_requests`、`query_duration`、`llm_calls`、`llm_latency`、`llm_tokens_used`、`sql_rejected`、`db_query_duration` 等指标（`metrics.py:46-103`），`server.py:137-144` 创建实例并启动 HTTP 端口，**但整个请求链路没有一处 `.inc()` / `.time()`** —— orchestrator 只用了 plain logging。
- 入口重复：`metrics.py:105` 有 `start_metrics_server()`，`server.py:141` 却直接调 `prometheus_client.start_http_server`。
- `tracing.py` 完整实现了 `TracingContext` / `TracingLogger`（`tracing.py:96, 240, 292`）并从包导出，但零调用；orchestrator 只是把 request_id 塞进 logging extra。
- Schema 自动刷新 `start_auto_refresh`（`schema_cache.py:131`）在 `server.py:125-133` 被整段注释禁用，且无配置开关。

### 任务

| 任务 | 内容 | 涉及文件 |
|------|------|---------|
| 3.1 | 埋点：`query` 工具层记录 `query_requests{status,database}` 与 `query_duration`；orchestrator 记录 `db_query_duration`、`sql_rejected{reason}`、`llm_calls`/`llm_latency`；`tokens_used` 有值时累加 `llm_tokens_used`（需要先让 generator 返回 usage 元数据，与任务 4.4 联动） | `server.py`, `services/orchestrator.py`, `services/sql_generator.py` |
| 3.2 | 统一指标端口启动入口为 `MetricsCollector.start_metrics_server()`，删除直调 | `observability/metrics.py`, `server.py` |
| 3.3 | 追踪二选一：**推荐整合** —— orchestrator 用 `TracingContext` 管理 request_id 并替换手工 extra 传参；若决定不做，则删除 `tracing.py` 避免维护假接口 | `services/orchestrator.py`, `observability/tracing.py` |
| 3.4 | Schema 自动刷新配置化：`CACHE_AUTO_REFRESH_MINUTES`（默认 0 = 关闭），替换注释块 | `config/settings.py`, `cache/schema_cache.py`, `server.py` |

### 验收标准

- [ ] 集成环境跑若干查询后 `curl :9090/metrics` 出现非零 `pg_mcp_query_requests_total`、`pg_mcp_query_duration_seconds`。**（未完成：需要真实 PostgreSQL + OpenAI 环境；指标埋点代码已合入并有单测覆盖）**
- [x] 被 SECURITY_VIOLATION 拒绝的请求使 `pg_mcp_sql_rejected_total` 按原因递增。（`test_security_violation_increments_sql_rejected_metric`）
- [x] 同一请求的所有日志行携带相同 request_id（整合方案下由 TracingContext 保证）。（orchestrator 全链路运行在 `request_context()` 内，`tests/unit/test_tracing.py`）

---

## Phase 4（P1）：模型与响应清理

### 现状差距

- **重复 `to_dict`**：`query.py:160-173`（含 tokens_used 兜底）与 `query.py:214-220`（`exclude_none=True`）。Python 类体中后定义覆盖前定义 → 实际生效的是后者，前者的兜底逻辑是死代码；`server.py:359-361` 被迫在调用后补丁 `tokens_used`。
- **未使用配置字段**：
  - `SecurityConfig.allow_write_operations`（`settings.py:78`）— validator 从未读取；
  - `ValidationConfig.min_confidence_score`（`settings.py:119`）— 与 `confidence_threshold`（`settings.py:131`）语义重复，`result_validator.py:172` 只用后者；
  - `ResilienceConfig.retry_delay` / `backoff_factor` — Phase 2 任务 2.3 落地后转为已使用。
- **文档漂移**：README 宣称 `OPENAI_MODEL` 默认 `gpt-5.2-mini`（代码 `gpt-4o-mini`，`settings.py:52`）、`OPENAI_MAX_TOKENS=32000`（代码 2000，`settings.py:53`）、`log_format` 默认 json（代码 text，`settings.py:180`）；README/CLAUDE_DESKTOP_SETUP 的启动命令 `python main.py` 指向的是无关 stub。
- **依赖不一致**：`server.py` 从 `mcp.server.fastmcp` 导入 FastMCP，而 pyproject 声明的是 `fastmcp` 包（根目录 `main.py` stub 用的才是它）——两个不同的 FastMCP 实现。

### 任务

| 任务 | 内容 | 涉及文件 |
|------|------|---------|
| 4.1 | 合并为单一 `QueryResponse.to_dict()`（`exclude_none=True` + tokens_used 兜底），删除 `server.py` 的事后补丁 | `models/query.py`, `server.py` |
| 4.2 | 删除 `min_confidence_score`（统一用 `confidence_threshold`）；删除 `allow_write_operations`（Phase 1 任务 1.5 决策） | `config/settings.py` |
| 4.3 | 让 `SQLGenerator` 返回 token usage（OpenAI response.usage），orchestrator 透传到 `tokens_used`，删除 orchestrator 中 "tokens_used would come from..." 的 TODO（`orchestrator.py:396-397`） | `services/sql_generator.py`, `services/orchestrator.py` |
| 4.4 | 修正 `main.py`：要么改为真正的入口（`from pg_mcp.__main__ import main; main()`），要么删除并更新 README/CLAUDE_DESKTOP_SETUP 指向 `python -m pg_mcp`；同步修正 README 配置默认值 | `main.py`, `README.md`, `CLAUDE_DESKTOP_SETUP.md` |
| 4.5 | 统一 FastMCP 导入来源：确认目标实现后固定 import 与 pyproject 依赖 | `server.py`, `pyproject.toml` |

### 验收标准

- [x] `QueryResponse` 只有一个 `to_dict`；全库搜索无 `tokens_used` 事后补丁。
- [x] `query` 工具返回中 `tokens_used` 为真实 LLM usage 而非恒 0。（`SQLGenerationResult` 携带 usage；`test_generate_returns_token_usage`）
- [x] README 中的默认值表与 `settings.py` 一一对应（人工核对：model=gpt-4o-mini、max_tokens=2000、log_format=text、新增 security/resilience/cache 字段均已同步）。
- [x] `uv run python main.py`（若保留）与 `uv run python -m pg_mcp` 行为一致。（main.py 已改为委托 `pg_mcp.__main__:main`）

---

## Phase 5（P2）：测试补强与验证基线

### 现状差距

- 无 `observability/`（logging 脱敏、metrics、tracing）与 `server.py` lifespan 装配的测试；`tests/unit/test_resilience.py` 只测组件本身，不测整合路径。
- integration/e2e 需要真实 PostgreSQL + `OPENAI_API_KEY`，且 pyproject 无默认 deselect —— 无数据库环境直接 `uv run pytest` 会失败，无法作为日常检查。
- 覆盖率配置了 `fail_under=80` 但从未记录实际基线。
- CLAUDE.md 规划的 `tests/security/` 目录不存在（安全测试混在 `tests/unit/test_sql_validator.py`）。

### 任务

| 任务 | 内容 | 涉及文件 |
|------|------|---------|
| 5.1 | 建立覆盖率基线：`uv run pytest -m "not integration" --cov=src --cov-report=term`，把结果与本计划的缺口写入本文档附录 | `pyproject.toml` |
| 5.2 | 默认跳过重环境测试：e2e 补 `integration` marker（或新增 `e2e` marker），addopts 默认加 `-m "not integration"`；CI 任务显式全量跑 | `pyproject.toml`, `tests/e2e/` |
| 5.3 | 新增单测：logging 脱敏过滤器、`MetricsCollector.reset_all_metrics`、限流整合路径（配合 2.1）、重试退避时序（配合 2.3） | `tests/unit/` |
| 5.4 | 新增安全回归测试（建议建 `tests/security/`）：blocked tables/columns 配置生效、EXPLAIN 开关、多库越权（请求 A 库不得触达 B 库——配合 Phase 1） | `tests/security/` |
| 5.5 | lifespan 装配测试：mock pool/OpenAI，断言组件按序创建、shutdown 带超时关闭（`test_shutdown.py` 迁入测试体系） | `tests/unit/` 或 `tests/integration/` |

### 验收标准

- [x] 无外部依赖时 `uv run pytest` 全绿（330 passed, 46 deselected）；`make check`（lint + mypy + pytest）一条命令通过。
- [x] 覆盖率 ≥ 80%（基线 **84.8%**，branch coverage）；security 模块 ≥ 95%（**部分达成**：`sql_executor` 99%、`rate_limiter` 98%、`circuit_breaker` 100%，但 `sql_validator` 90%、`tracing` 79% 仍未达标，遗留）。

---

## 完成情况总结（2026-10-09 追加：工程基线重建 + 安全加固）

背景：本次会话发现仓库丢失了全部构建配置（`pyproject.toml`、`README.md`、`Makefile`），并确认了一个校验器安全漏洞。已修复并重建：

1. **重建 `pyproject.toml`**（含 `mcp>=1.2.0,<2` 钉版——mcp 2.x 将 FastMCP 更名为 MCPServer，与代码不兼容；ruff 启用 ASYNC 规则组匹配既有 `# noqa: ASYNC109`；mypy 对 asyncpg 的 ignore_missing_imports override；覆盖率门禁 80% 分支覆盖）。
2. **重建 `README.md`**（与代码默认值核对一致）与 `Makefile`（`make check` 一键门禁）。
3. **安全漏洞修复（P0）**：`SQLValidator` 新增 `_check_nested_statements()`——PostgreSQL 数据修改型 CTE（`WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d`）顶层解析为 `Select`，原校验器完全放行。现对整棵 AST 扫描禁止语句类型。回归测试见 `tests/security/test_nested_statements.py`（7 例，含深层嵌套与无误报验证）。
4. **遗留测试补齐**：限流器 `get_all_stats()` 拒绝计数断言（`test_get_all_stats_reflects_rejections`）；lifespan 装配测试（计划任务 5.5，`tests/unit/test_server_lifespan.py`——组件按序装配、shutdown 带超时关池、auto-refresh 开关路径）。
5. **格式基线统一**：`ruff format`（line-length 100）一次性归一；sqlglot 30.x 将 `Expression` 更名 `Expr`，校验器注解已同步。

**验证状态（2026-10-09）**：ruff check/format 全绿；`mypy src` strict 通过（30 文件）；354 passed + 46 deselected；覆盖率 **88.24%**（分支，门禁 80%）。真实环境验证：`.env` 指向的 PostgreSQL 13.3 连通、schema 内省成功（`test` 库）、lifespan 完整启动/关闭正常。

**新增遗留**：完整 NL→SQL 集成验收（`uv run pytest -m integration`）仍需有效 `OPENAI_API_KEY`（当前 `.env` 未配置）。

**✅ 2026-10-09 集成验收补全**：`.env` 已配置 OpenAI 兼容端点（`OPENAI_BASE_URL=http://10.11.203.232:3030/v1`，`OPENAI_MODEL=glm-5.3-flash`）。`uv run pytest -m integration` **46 passed**（25 分钟，真实 LLM + 真实 PostgreSQL）；另有手动端到端验证：中文提问 → 生成 `SELECT COUNT(*) ... WHERE is_removed = 0`（tokens_used=1183）→ 校验通过 → 真实执行返回 10 行。两条历史遗留的集成验收项（多库验证除外——当前仅配置单库）至此全部完成。

---

## 完成情况总结（2026-09-23）

**已落地**：Phase 1-5 全部代码任务完成。多库配置（`DATABASES` JSON / 嵌套 env / 单库自动兜底）+ 执行器按库名路由（fail-closed）；安全策略配置化（blocked_tables/blocked_columns/allow_explain，含别名、schema 限定、裸列名三种绕过缺口的加固）；限流器双层接入请求路径 + 重试指数退避 + 死熔断器清理；Prometheus 指标（请求/耗时/LLM/拒绝/连接）与 request_id 追踪进入请求链路；`MetricsCollector` 改用私有 registry 使 `reset_all_metrics` 可用；`CACHE_AUTO_REFRESH_MINUTES` 驱动 schema 自动刷新；模型/响应清理（单一 `to_dict`、token usage 透传、`main.py` 修复、`fastmcp`→`mcp` 依赖修正）；新增 `tests/security/` 与 observability/server-tool/result-validator/tracing 单测，pytest 默认排除 integration，新增根 `Makefile`（`make check`）。

**验证状态**：`make check` 全绿（ruff + mypy strict + 330 单测通过），覆盖率 84.8%（分支覆盖，门禁 80% 生效）。

**遗留（需真实环境或后续迭代）**：
1. 多库/指标的两条集成验收需 `fixtures/` 三库 + 有效 `OPENAI_API_KEY` 的真实环境（`uv run pytest -m integration`）。
2. `MultiRateLimiter.get_all_stats()` 拒绝计数断言未写。
3. 安全模块覆盖率未达 95% 目标（`sql_validator` 90%、`tracing` 79%）。
4. `server.py` lifespan 装配测试（计划任务 5.5，mock pool/OpenAI 断言装配顺序）未实现。

---

## 里程碑与工作量估算

| 里程碑 | 内容 | 预估 | 前置 |
|--------|------|------|------|
| M1 | Phase 1（多库路由 + 安全配置化） | 3–4 人日 | 无 |
| M2 | Phase 2（限流/退避整合） | 2 人日 | 无（可与 M1 并行） |
| M3 | Phase 3（指标/追踪埋点） | 2–3 人日 | M2（限流位置定案）、4.3（token 统计） |
| M4 | Phase 4（模型/文档清理） | 1 人日 | 无（4.3 影响_M3） |
| M5 | Phase 5（测试补强 + 基线） | 2–3 人日 | 跟随各 Phase，最后收口 |

总计约 **10–13 人日**。建议顺序：M1 → M2 → M4(4.1–4.2) → M3 → M5，每个里程碑结束跑一次全量验收清单。

## 风险与兼容性说明

1. **响应结构变化**：4.1 统一 `to_dict` 后，`exclude_none` 行为与现网一致（当前实际生效的就是它），但需在 CHANGELOG 注明 `tokens_used` 语义从"恒 0"变为真实值。
2. **配置兼容**：1.1 必须保证只设置 `DATABASE_*` 的现有部署零改动；多库 env 命名在 `.env.example` 给出完整示例。
3. **限流默认值**：2.2 配置化后默认值保持 10/5，避免现网行为突变；拒绝响应使用已有 `rate_limit_exceeded` 错误码，客户端可识别。
4. **双 FastMCP**（4.5）：`mcp` 与 `fastmcp` 的 lifespan/tool API 有差异，切换前先跑 e2e 确认 `run_stdio_async` 行为一致。

## 附录：证据速查

```
多库断链      server.py:99-109(单库pool) → :160-169(执行器dict) → :197(只传主库) → orchestrator.py:138(校验库名) vs :198(固定执行器)
安全不可配置  server.py:153-158(硬编码None/False)；settings.py:73-109(SecurityConfig无字段)；sql_validator.py:236-283(逻辑已有)
限流未接      server.py:187-190(创建)；rate_limiter.py:246-286(for_queries/for_llm无人调用)
退避未实现    settings.py:154-159(字段)；orchestrator.py:377-435(循环无sleep)
熔断器重复    orchestrator.py:99-102(在用) vs server.py:181-184(死实例)
指标未埋点    metrics.py:46-103(定义) / metrics.py:105(start_metrics_server) vs server.py:137-144(仅创建+起端口)
追踪未使用    tracing.py:96,240,292(实现+导出)，包外零import
双to_dict     query.py:160-173(死代码) 覆盖于 query.py:214-220；server.py:359-361(补偿补丁)
死配置字段    settings.py:78(allow_write_operations)、:119(min_confidence_score)
默认值漂移    README.md:82,308,346 vs settings.py:52,53,180
```
