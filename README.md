# 企业 IT 运维知识助手

可本地复现的企业 IT 支持 Agent MVP。它用 LangGraph 显式状态机完成带引用的知识问答、本人可见工单查询、确认后建单和安全转人工，并提供 SSE 工作台、审计、固定评测集与 Docker Compose 交付环境。默认 Mock 模式不需要模型 API Key。

- [架构与数据流](docs/architecture.md)
- [逐句演示脚本](docs/demo-script.md)
- [离线评测报告](docs/evaluation-report.md)

## 能力边界

- 知识问答：Qdrant 稠密召回、应用内 BM25、重排和原文引用。
- 工单查询：只查询当前模拟用户可见工单，最终状态为 `ticket_status`。
- 工单创建：Agent 只生成草稿和短期确认令牌；前端显式确认后，服务端才执行幂等写入。
- 安全兜底：证据不足、受限操作、依赖失败或步骤超限均转人工，不自动执行高风险运维。
- 可观测性：Trace ID、节点耗时、模型/Token、引用 ID、脱敏工具审计和 unresolved bad case。

## 最小环境

- Docker Desktop 或 Docker Engine，支持 Docker Compose v2
- 建议 4 CPU、8 GB 内存、至少 5 GB 可用磁盘
- Windows 本地冒烟测试需要 Windows PowerShell 5.1 或 PowerShell 7
- 端口 `5173`、`18000`、`15432`、`16333`、`16334`、`6379` 可用

只有不使用 Docker 的开发流程才需要 Python 3.11-3.14 和 Node.js 22。

## 启动

1. 复制环境变量模板：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 构建并启动。API 会在 Uvicorn 启动前自动执行 `alembic upgrade head`：

   ```powershell
   docker compose up --build -d
   ```

3. 幂等导入模拟用户、会话、100 条 2026 工单和知识库：

   ```powershell
   docker compose exec api python /app/scripts/seed_data.py
   docker compose exec api python /app/scripts/ingest_knowledge.py
   ```

   两条命令可重复执行。种子工单范围为 `IT-2026-0001` 至 `IT-2026-0100`。

4. 访问前端 <http://localhost:5173>。将连接信息设置为用户 `u-001`、会话 `c-001`；API 健康检查为 <http://localhost:18000/health>。

5. 运行完整发布冒烟：

   ```powershell
   powershell -File scripts/smoke_test.ps1
   ```

冒烟会验证迁移、两次种子/知识导入、API/Web、VPN SSE 引用、`IT-2026-0001` 查单、草稿、无令牌拒绝、有效令牌 HTTP 201、评测及报告。

## 测试

容器内执行发布验证：

```powershell
docker compose exec api pytest tests -q
docker compose exec web npm run test -- --run
docker compose exec web npm run build
docker compose exec api python /app/scripts/run_evaluation.py --input /app/data/eval/cases.jsonl --report /app/docs/evaluation-report.md
docker compose config
```

API 镜像包含 Alembic、`tests` 和 Python dev 依赖；前端镜像包含 Vitest/TypeScript 构建依赖。`docs` 以可写方式挂载，因此容器评测会更新宿主机报告。

不使用 Docker 时：

```powershell
cd backend
python -m pip install -e ".[dev]"
python -m pytest tests -q

cd ..\frontend
npm ci
npm run test -- --run
npm run build
```

依赖 PostgreSQL、Redis 或 Qdrant 的后端集成测试仍需相应服务；`test_end_to_end_flows.py` 使用可控进程内 fake，可独立验证四条核心路径。

## 模型模式

`.env` 默认配置：

```dotenv
MODEL_MODE=mock
```

Mock Provider 可预测、无外部调用，适合演示和固定评测。切换 OpenAI 兼容接口时填写：

```dotenv
MODEL_MODE=openai
OPENAI_BASE_URL=https://your-compatible-endpoint/v1
OPENAI_API_KEY=replace-me
OPENAI_MODEL=your-model
```

修改后运行 `docker compose up --build -d`。真实模型模式仍使用同一状态机与确认闸门，但结果、时延和 Token 会受外部服务影响。

## 评测口径

固定集 `data/eval/cases.jsonl` 包含 80-120 条知识、查单/建单和高风险/无答案用例。

- `Recall@5`：知识用例期望来源中，被前 5 个检索结果命中的比例，微平均；发布阈值 `>= 0.80`。
- `Citation precision`：对已输出引用的知识回答，最终引用覆盖逐例必需来源的比例，微平均；缺失证据由 Recall 反映，标注不是全部相关来源的穷举，附加同主题引用不计为错误；发布阈值 `>= 0.85`。
- 最终状态通过率：实际 `final_state` 与逐例期望一致的比例。
- 工具行为通过率：实际工具序列与逐例期望完全一致的比例。
- 未确认工单写入：图执行前后工单增量或写工具痕迹；必须为 `0`。
- P50/P95：单个图调用的本地端到端耗时分位数，不含浏览器渲染。

评测命令只在 Recall、引用或未确认写入门槛失败时返回非零；逐例状态/工具失败仍会写入报告并列出样本 ID，发布时应同时检查报告总分。

## 数据与隐私

仓库内用户、工单、知识和评测数据均为 2026 年虚构脱敏数据，不应替换为真实员工资料。日志不记录完整用户输入、完整检索上下文或 API Key；工具审计会将手机号、邮箱和连续 16 位数字替换为 `[REDACTED]`，并仅保留结果类别、引用/工单标识和安全摘要。`.env` 不应提交。

## 已知限制

- 本地模拟身份不是认证机制，不提供真实 SSO、完整 RBAC 或多租户隔离。
- 工单系统是 PostgreSQL 模拟实现，不连接 Jira 或 ServiceNow。
- Mock 回答用于流程复现，不代表真实模型质量；固定评测指标只适用于仓库数据版本。
- BM25 索引在应用进程内构建，适用于当前小型知识集，不是大规模搜索架构。
- 不自动重置密码、修改权限、重启服务或执行其他高风险操作。
- Compose 面向单机演示，不包含生产级 TLS、密钥管理、备份、高可用或容量规划。

停止并删除本地数据卷：`docker compose down -v`。
