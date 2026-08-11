# 企业 IT 运维知识助手 MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个可通过 Docker Compose 本地运行的中文企业 IT 运维知识助手，提供可信 RAG 问答、工单查询、经用户确认的工单创建、人工兜底、离线评测与可观测性。

**Architecture:** 浏览器端 React 工作台通过 SSE 接收 FastAPI 的流式 Agent 事件；后端以 LangGraph 显式编排“意图 → 检索 → 回答/工单 → 确认/转人工”状态机。PostgreSQL 保存业务与审计数据，Redis 保存短期状态和缓存，Qdrant 保存向量；应用内 BM25 与 Qdrant 的向量召回合并后经轻量 Cross-Encoder 重排。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、LangGraph、pytest、PostgreSQL 16、Redis 7、Qdrant、`sentence-transformers`、`rank-bm25`、OpenTelemetry；React 18、TypeScript、Vite、Vitest、Playwright、Docker Compose。

---

## 0. 实施前约束与交付顺序

- 默认通过 `MODEL_MODE=mock` 运行，任何人无需 API Key 即可完成三条主路径：带引用解答、查询工单、确认后建单。
- 真实模型仅通过 OpenAI 兼容环境变量启用；API Key 不得写入 Git、数据库、Trace 或测试夹具。
- 任何写操作必须经过后端确认令牌校验；前端按钮、提示词约束或模型输出不能替代该校验。
- 所有示例资料、员工和工单均为虚构、脱敏数据；不得录入真实企业用户、工单或凭据。
- 每个任务只提交该任务明确列出的文件；工作区现有的 `面试总结.md` 不属于本计划，始终不暂存、不提交。

## 1. 目标文件结构

在开始任何实现前，按下列职责创建目录与文件。后续任务只能向此结构中补充内容，避免将业务逻辑堆入路由或页面组件。

```text
.
├── docker-compose.yml
├── .env.example
├── README.md
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/env.py
│   ├── alembic/versions/0001_initial_schema.py
│   ├── app/
│   │   ├── main.py                     # FastAPI 装配与 lifespan
│   │   ├── core/config.py               # Settings 与环境变量校验
│   │   ├── core/telemetry.py            # Trace/结构化日志初始化
│   │   ├── db/session.py                # Async engine 和 session
│   │   ├── db/models.py                 # SQLAlchemy 业务模型
│   │   ├── schemas.py                   # API、工具、SSE 的 Pydantic 类型
│   │   ├── repositories/                # Conversation、Ticket、Audit 仓储
│   │   ├── llm/providers.py             # Mock/OpenAI-compatible Provider
│   │   ├── rag/ingest.py                # 文档校验、分块、向量写入
│   │   ├── rag/retriever.py             # BM25+Qdrant+重排+引用
│   │   ├── tickets/service.py           # 只读查询、草稿、确认后创建
│   │   ├── agent/state.py               # AgentState
│   │   ├── agent/graph.py               # LangGraph 节点、边与安全闸门
│   │   ├── evaluation/runner.py          # 固定用例评测与指标汇总
│   │   ├── services/chat.py             # 运行图并生成 SSE 事件
│   │   └── api/routes/                  # chat、tickets、knowledge、evaluations
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── fixtures/
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/api/client.ts
│   ├── src/api/sse.ts
│   ├── src/types.ts
│   ├── src/App.tsx
│   ├── src/components/ChatPanel.tsx
│   ├── src/components/CitationList.tsx
│   ├── src/components/RunTimeline.tsx
│   ├── src/components/TicketDraftCard.tsx
│   └── src/components/HandoffCard.tsx
├── data/
│   ├── knowledge/                       # 30–50 篇虚构 Markdown/PDF 资料
│   ├── tickets/seed_tickets.json
│   └── eval/cases.jsonl
├── scripts/
│   ├── seed_data.py
│   ├── ingest_knowledge.py
│   ├── run_evaluation.py
│   └── smoke_test.ps1
└── docs/
    ├── architecture.md
    ├── evaluation-report.md
    └── demo-script.md
```

## 2. 任务清单

### Task 1：初始化可复现的本地开发环境

**Files:**

- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `backend/pyproject.toml`
- Create: `backend/app/main.py`
- Create: `backend/tests/unit/test_health.py`
- Create: `frontend/package.json`
- Create: `frontend/src/App.tsx`
- Create: `README.md`

- [ ] **Step 1: 编写后端健康检查的失败测试。**

```python
# backend/tests/unit/test_health.py
from fastapi.testclient import TestClient
from app.main import app


def test_health_returns_service_name_and_status() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"service": "it-operations-agent-api", "status": "ok"}
```

- [ ] **Step 2: 验证测试在应用尚未实现前失败。**

Run: `cd backend; pytest tests/unit/test_health.py -q`
Expected: `ERROR`，因为 `app.main` 尚不存在。

- [ ] **Step 3: 创建最小 FastAPI 应用与健康端点。**

```python
# backend/app/main.py
from fastapi import FastAPI

app = FastAPI(title="IT Operations Agent API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"service": "it-operations-agent-api", "status": "ok"}
```

- [ ] **Step 4: 创建依赖、Compose 与环境变量模板。**

`backend/pyproject.toml` 必须声明 `fastapi`、`uvicorn[standard]`、`pydantic-settings`、`sqlalchemy[asyncio]`、`asyncpg`、`alembic`、`redis`、`qdrant-client`、`rank-bm25`、`sentence-transformers`、`langgraph`、`openai`、`opentelemetry-api`、`opentelemetry-sdk`、`pytest`、`pytest-asyncio`、`httpx`。`docker-compose.yml` 必须包含 `postgres:16-alpine`、`redis:7-alpine`、`qdrant/qdrant`、`api`、`web` 五个服务，并为 PostgreSQL、Redis 和 Qdrant 添加健康检查。

`.env.example` 固定提供如下无秘密默认值：

```dotenv
MODEL_MODE=mock
OPENAI_BASE_URL=
OPENAI_API_KEY=
OPENAI_MODEL=
POSTGRES_DB=itops
POSTGRES_USER=itops
POSTGRES_PASSWORD=itops-local-only
DATABASE_URL=postgresql+asyncpg://itops:itops-local-only@postgres:5432/itops
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
```

- [ ] **Step 5: 验证健康检查、前端占位页与容器启动。**

Run: `cd backend; pytest tests/unit/test_health.py -q`
Expected: `1 passed`。

Run: `docker compose up --build -d; powershell -File scripts/smoke_test.ps1`
Expected: `/health` 返回 JSON 中的 `status: ok`，前端根页面 HTTP 200。

- [ ] **Step 6: 提交环境基线。**

```powershell
git add docker-compose.yml .env.example README.md backend frontend scripts/smoke_test.ps1
git commit -m "chore: bootstrap local agent workspace"
```

### Task 2：定义配置、领域模型与数据库迁移

**Files:**

- Create: `backend/app/core/config.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/db/models.py`
- Create: `backend/alembic/versions/0001_initial_schema.py`
- Create: `backend/tests/unit/test_config.py`
- Create: `backend/tests/integration/test_initial_schema.py`

- [ ] **Step 1: 为模型模式和数据库 URL 写失败测试。**

```python
# backend/tests/unit/test_config.py
import pytest
from pydantic import ValidationError
from app.core.config import Settings


def test_settings_rejects_real_mode_without_openai_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(model_mode="openai", openai_base_url=None, openai_api_key=None, openai_model=None)


def test_settings_accepts_mock_mode_without_openai_credentials() -> None:
    settings = Settings(model_mode="mock")
    assert settings.model_mode == "mock"
```

- [ ] **Step 2: 运行配置测试确认其失败。**

Run: `cd backend; pytest tests/unit/test_config.py -q`
Expected: `ERROR`，因为 `Settings` 尚未定义。

- [ ] **Step 3: 实现严格配置与初始表。**

`Settings` 使用 `Literal["mock", "openai"]`；当模式为 `openai` 时，模型地址、Key 和模型名均不能为空。初始迁移创建 `users`、`conversations`、`messages`、`agent_runs`、`tickets`、`ticket_events`、`tool_audits`、`knowledge_documents`、`knowledge_chunks` 表。

`tickets` 必含 `ticket_number` 唯一索引；`tool_audits.idempotency_key` 必含唯一索引；`messages` 存储 JSON `citations` 与可空 JSON `user_feedback`；`agent_runs` 存储 `trace_id`、`intent`、`final_state`、`latency_ms`、`input_tokens`、`output_tokens`、`handoff_reason`。

- [ ] **Step 4: 运行迁移和集成测试。**

```python
# backend/tests/integration/test_initial_schema.py
async def test_initial_schema_contains_ticket_idempotency_index(async_engine) -> None:
    async with async_engine.connect() as connection:
        result = await connection.exec_driver_sql(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'tool_audits'"
        )
    assert "uq_tool_audits_idempotency_key" in {row[0] for row in result}
```

Run: `docker compose exec api alembic upgrade head; cd backend; pytest tests/unit/test_config.py tests/integration/test_initial_schema.py -q`
Expected: 全部通过。

- [ ] **Step 5: 提交持久化基础。**

```powershell
git add backend/app/core backend/app/db backend/alembic backend/tests
git commit -m "feat: add agent domain schema"
```

### Task 3：实现模型 Provider 与稳定的 Mock 模式

**Files:**

- Create: `backend/app/llm/providers.py`
- Create: `backend/tests/unit/test_providers.py`

- [ ] **Step 1: 写出 Provider 不泄露 Key 且 Mock 可预测的失败测试。**

```python
# backend/tests/unit/test_providers.py
from app.llm.providers import MockChatProvider


async def test_mock_provider_returns_deterministic_answer() -> None:
    provider = MockChatProvider()
    result = await provider.complete("VPN 连不上")
    assert result.text == "请先确认网络连接，然后重新连接 VPN。"
    assert result.input_tokens == 3
    assert result.output_tokens > 0
```

- [ ] **Step 2: 运行 Provider 测试。**

Run: `cd backend; pytest tests/unit/test_providers.py -q`
Expected: `ERROR`，因为 `MockChatProvider` 尚不存在。

- [ ] **Step 3: 实现统一 Provider 接口。**

实现 `ChatResult(text, input_tokens, output_tokens, model)` 数据类、`ChatProvider` 协议、`MockChatProvider` 与 `OpenAICompatibleProvider`。Mock 映射至少覆盖 VPN、工单状态、建单信息缺失和未知问题四类输入。真实 Provider 只能从 `Settings` 读取 Key，日志字段仅允许记录模型名、耗时与 Token。

- [ ] **Step 4: 验证 Mock 和真实模式的构造行为。**

Run: `cd backend; pytest tests/unit/test_providers.py -q`
Expected: 全部通过；测试输出中不得出现 `OPENAI_API_KEY` 内容。

- [ ] **Step 5: 提交 Provider。**

```powershell
git add backend/app/llm backend/tests/unit/test_providers.py
git commit -m "feat: add mock and openai model providers"
```

### Task 4：实现模拟工单数据、受控工具与确认闸门

**Files:**

- Create: `data/tickets/seed_tickets.json`
- Create: `backend/app/schemas.py`
- Create: `backend/app/repositories/tickets.py`
- Create: `backend/app/tickets/service.py`
- Create: `backend/tests/integration/test_ticket_service.py`
- Create: `scripts/seed_data.py`

- [ ] **Step 1: 先写“未确认不得建单”和“幂等不得重复建单”的失败测试。**

```python
# backend/tests/integration/test_ticket_service.py
import pytest
from app.tickets.service import TicketService
from app.schemas import TicketDraft


@pytest.mark.asyncio
async def test_create_ticket_rejects_unconfirmed_draft(ticket_service: TicketService) -> None:
    draft = TicketDraft(title="VPN 无法连接", category="network", priority="medium", description="客户端报错", attempted_steps=[])
    with pytest.raises(PermissionError, match="confirmation token is required"):
        await ticket_service.create_confirmed(user_id="u-001", draft=draft, confirmation_token=None, idempotency_key="key-1")


@pytest.mark.asyncio
async def test_create_ticket_is_idempotent(ticket_service: TicketService, confirmed_token: str) -> None:
    draft = TicketDraft(title="VPN 无法连接", category="network", priority="medium", description="客户端报错", attempted_steps=[])
    first = await ticket_service.create_confirmed("u-001", draft, confirmed_token, "key-2")
    second = await ticket_service.create_confirmed("u-001", draft, confirmed_token, "key-2")
    assert first.ticket_number == second.ticket_number
```

- [ ] **Step 2: 运行工单测试确认其失败。**

Run: `cd backend; pytest tests/integration/test_ticket_service.py -q`
Expected: `ERROR`，因为服务与 Schema 尚不存在。

- [ ] **Step 3: 实现工单服务边界。**

实现不可变 `TicketDraft` Schema、`TicketStatusResult`、`TicketCreateResult` 和 `TicketService`：

- `get_ticket_status(user_id, ticket_number)` 只返回当前用户的工单；不存在与无权访问返回同一通用结果。
- `issue_confirmation_token(conversation_id, draft)` 把草稿摘要写入 Redis，TTL 10 分钟。
- `create_confirmed(...)` 验证令牌、草稿摘要、用户、TTL 和幂等键；任一不匹配抛出 `PermissionError`，且不写入 `tickets`。
- 成功时生成格式为 `IT-YYYY-0001` 的工单号，写入 `tickets`、`ticket_events`、`tool_audits`。

- [ ] **Step 4: 导入种子数据并验证三个工单分支。**

Run: `docker compose exec api python /app/scripts/seed_data.py; cd backend; pytest tests/integration/test_ticket_service.py -q`
Expected: 未确认用例无写入；重复请求只创建一张工单；查询仅返回归属工单。

- [ ] **Step 5: 提交工单能力。**

```powershell
git add data/tickets backend/app/schemas.py backend/app/repositories/tickets.py backend/app/tickets backend/tests/integration/test_ticket_service.py scripts/seed_data.py
git commit -m "feat: add confirmed ticket workflow"
```

### Task 5：实现知识库导入、混合检索、重排与引用

**Files:**

- Create: `data/knowledge/vpn-connection.md`
- Create: `data/knowledge/account-access.md`
- Create: `data/knowledge/software-installation.md`
- Create: `backend/tests/fixtures/vpn-error-codes.pdf`
- Create: `backend/app/rag/ingest.py`
- Create: `backend/app/rag/retriever.py`
- Create: `backend/tests/unit/test_chunking.py`
- Create: `backend/tests/integration/test_retriever.py`
- Create: `scripts/ingest_knowledge.py`

- [ ] **Step 1: 编写分块与来源引用的失败测试。**

```python
# backend/tests/unit/test_chunking.py
from app.rag.ingest import chunk_markdown


def test_chunk_markdown_keeps_source_heading_and_chunk_order() -> None:
    chunks = chunk_markdown("# VPN 连接\n\n第一步。\n\n第二步。", source_path="vpn-connection.md", max_chars=12, overlap_chars=0)
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert all(chunk.source_title == "VPN 连接" for chunk in chunks)
    assert all(chunk.source_path == "vpn-connection.md" for chunk in chunks)
```

- [ ] **Step 2: 运行分块测试。**

Run: `cd backend; pytest tests/unit/test_chunking.py -q`
Expected: `ERROR`，因为 `chunk_markdown` 尚不存在。

- [ ] **Step 3: 实现导入和检索接口。**

实现 `KnowledgeChunk`、`Citation`、`RetrievalHit` 和 `extract_text(path)`；后者仅接受 `.md` 与 `.pdf`，Markdown 用 UTF-8 读取，PDF 用 PyMuPDF 提取逐页文本，空文本或解码失败均拒绝。`chunk_markdown` 以 Markdown 标题作为来源标题，保存路径、文档版本、chunk 序号和字符范围。`ingest_knowledge.py` 读取 `data/knowledge`，拒绝空文件与不支持格式，写 PostgreSQL 元数据、Qdrant 向量和内存 BM25 索引。以 `backend/tests/fixtures/vpn-error-codes.pdf` 验证 PDF 文本可被解析并进入相同分块流程。

`HybridRetriever.retrieve(query, user_access_level)` 执行：访问级别过滤 → Qdrant top-20 → BM25 top-20 → min-max 归一化的 `0.65 * vector + 0.35 * bm25` 合并 → Cross-Encoder top-5。每个最终结果都返回 `Citation(document_id, source_title, source_path, chunk_index, excerpt)`。

- [ ] **Step 4: 用固定资料验证混合检索结果。**

```python
# backend/tests/integration/test_retriever.py
async def test_vpn_query_returns_vpn_citation(retriever) -> None:
    hits = await retriever.retrieve("VPN 连不上如何处理", user_access_level="employee")
    assert hits[0].citation.source_path == "vpn-connection.md"
    assert len(hits) <= 5
```

Run: `docker compose exec api python /app/scripts/ingest_knowledge.py; cd backend; pytest tests/unit/test_chunking.py tests/integration/test_retriever.py -q`
Expected: 全部通过，首条引用来自 `vpn-connection.md`。

- [ ] **Step 5: 补齐 30–50 篇模拟资料并提交。**

为 VPN、账号、权限、设备、网络、办公软件、安全 FAQ 各写至少 4 篇短资料；每篇包含标题、适用对象、前置条件、操作步骤、何时转人工。所有文档须在导入测试中通过。

```powershell
git add data/knowledge backend/app/rag backend/tests/unit/test_chunking.py backend/tests/integration/test_retriever.py scripts/ingest_knowledge.py
git commit -m "feat: add hybrid knowledge retrieval"
```

### Task 6：构建 LangGraph 状态机与安全路由

**Files:**

- Create: `backend/app/agent/state.py`
- Create: `backend/app/agent/graph.py`
- Create: `backend/tests/unit/test_agent_graph.py`

- [ ] **Step 1: 为无证据转人工、建单确认和最大步骤写失败测试。**

```python
# backend/tests/unit/test_agent_graph.py
import pytest
from app.agent.graph import build_graph


@pytest.mark.asyncio
async def test_graph_handoffs_when_retrieval_has_no_evidence(fake_dependencies) -> None:
    graph = build_graph(fake_dependencies)
    result = await graph.ainvoke({"user_id": "u-001", "conversation_id": "c-001", "message": "未知资产编号", "step_count": 0})
    assert result["final_state"] == "handoff"
    assert result["handoff_reason"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_graph_never_calls_create_tool_before_confirmation(fake_dependencies) -> None:
    graph = build_graph(fake_dependencies)
    result = await graph.ainvoke({"user_id": "u-001", "conversation_id": "c-002", "message": "VPN 一直失败，请建工单", "step_count": 0})
    assert result["final_state"] == "awaiting_confirmation"
    assert fake_dependencies.ticket_service.created_ticket_count == 0
```

- [ ] **Step 2: 运行状态机测试。**

Run: `cd backend; pytest tests/unit/test_agent_graph.py -q`
Expected: `ERROR`，因为图和依赖夹具不存在。

- [ ] **Step 3: 定义状态和节点。**

`AgentState` 使用 `TypedDict`，字段固定为：`user_id`、`conversation_id`、`message`、`intent`、`retrieval_hits`、`citations`、`ticket_number`、`ticket_draft`、`confirmation_token`、`tool_history`、`step_count`、`answer`、`final_state`、`handoff_reason`、`trace_id`、`error`。

节点固定为 `classify_intent`、`retrieve_evidence`、`decide_next_action`、`answer_with_citations`、`lookup_ticket`、`collect_ticket_draft`、`issue_confirmation`、`handoff`。图编译时通过条件边确保：

```python
if state["step_count"] >= 8:
    return "handoff"
if state["intent"] == "ticket_create":
    return "collect_ticket_draft"
if not state["retrieval_hits"]:
    return "handoff"
return "answer_with_citations"
```

`create_ticket` 不属于此图的自动节点；只可由后续确认 API 调用 `TicketService.create_confirmed`。

- [ ] **Step 4: 验证所有主要分支。**

Run: `cd backend; pytest tests/unit/test_agent_graph.py -q`
Expected: 覆盖带引用回答、查单、待确认草稿、无证据转人工、超过 8 步转人工五条路径，全部通过。

- [ ] **Step 5: 提交 Agent 编排。**

```powershell
git add backend/app/agent backend/tests/unit/test_agent_graph.py
git commit -m "feat: add controlled langgraph workflow"
```

### Task 7：提供会话 API、SSE 事件与确认接口

**Files:**

- Create: `backend/app/services/chat.py`
- Create: `backend/app/api/routes/chat.py`
- Create: `backend/app/api/routes/tickets.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/integration/test_chat_api.py`

- [ ] **Step 1: 编写 SSE 事件顺序和确认 API 的失败测试。**

```python
# backend/tests/integration/test_chat_api.py
def test_chat_stream_ends_with_final_event(client) -> None:
    response = client.post("/api/conversations/c-001/messages:stream", json={"user_id": "u-001", "content": "VPN 连不上"})
    events = [line for line in response.text.splitlines() if line.startswith("event:")]
    assert events[0] == "event: run_started"
    assert "event: citations" in events
    assert events[-1] == "event: final"


def test_confirm_ticket_requires_confirmation_token(client) -> None:
    response = client.post("/api/conversations/c-001/ticket-confirmations", json={"user_id": "u-001", "draft": {}})
    assert response.status_code == 422
```

- [ ] **Step 2: 运行 API 测试。**

Run: `cd backend; pytest tests/integration/test_chat_api.py -q`
Expected: `ERROR`，因为路由尚未注册。

- [ ] **Step 3: 实现 API 契约。**

实现以下端点：

| Method | Path | 行为 |
| --- | --- | --- |
| `POST` | `/api/conversations/{id}/messages:stream` | 运行 Agent，依次发送 `run_started`、`node_completed`、`citations`、`ticket_draft`（如有）、`handoff`（如有）、`final` SSE 事件。 |
| `POST` | `/api/conversations/{id}/ticket-confirmations` | 只接受 `user_id`、`confirmation_token`、完整 `TicketDraft`、`idempotency_key`；创建成功返回 201。 |
| `GET` | `/api/tickets/{ticket_number}` | 在当前模拟用户范围内返回工单状态。 |
| `POST` | `/api/messages/{message_id}/feedback` | 仅接受 `resolved` 或 `unresolved`，写入消息反馈。 |

所有请求生成或接收 `X-Trace-Id`；所有错误使用 `{ "code": str, "message": str, "trace_id": str }`，不得向客户端返回堆栈、数据库 URL 或 API Key。

- [ ] **Step 4: 验证 Mock 模式的端到端 API 主路径。**

Run: `cd backend; pytest tests/integration/test_chat_api.py -q`
Expected: SSE 事件顺序正确；建单草稿没有确认令牌时不会落库；确认一次后返回 201 和工单号。

- [ ] **Step 5: 提交 API。**

```powershell
git add backend/app/services backend/app/api backend/app/main.py backend/tests/integration/test_chat_api.py
git commit -m "feat: expose streaming chat and ticket APIs"
```

### Task 8：实现前端工作台与安全确认交互

**Files:**

- Create: `frontend/src/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/sse.ts`
- Create: `frontend/src/components/ChatPanel.tsx`
- Create: `frontend/src/components/CitationList.tsx`
- Create: `frontend/src/components/RunTimeline.tsx`
- Create: `frontend/src/components/TicketDraftCard.tsx`
- Create: `frontend/src/components/HandoffCard.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/components/TicketDraftCard.test.tsx`

- [ ] **Step 1: 写出票据按钮在无确认状态下禁用的失败测试。**

```tsx
// frontend/src/components/TicketDraftCard.test.tsx
import { render, screen } from "@testing-library/react";
import { TicketDraftCard } from "./TicketDraftCard";

it("disables submit until the user checks confirmation", () => {
  render(<TicketDraftCard draft={{ title: "VPN 无法连接", category: "network", priority: "medium", description: "错误 809", attempted_steps: [] }} confirmationToken="token-1" onSubmit={vi.fn()} />);
  expect(screen.getByRole("button", { name: "确认并创建工单" })).toBeDisabled();
});
```

- [ ] **Step 2: 运行组件测试。**

Run: `cd frontend; npm test -- TicketDraftCard.test.tsx`
Expected: `FAIL`，因为组件尚不存在。

- [ ] **Step 3: 实现界面与 SSE 状态映射。**

`ChatPanel` 显示输入、流式文本与“已解决/仍未解决”。`CitationList` 只显示 API 返回的标题、片段和路径。`RunTimeline` 将 `node_completed` 显示为“正在检索”“正在查询工单”等非敏感状态。`TicketDraftCard` 展示可编辑草稿、确认复选框、确认后才启用的按钮，并在点击时发送确认令牌与 UUID 幂等键。`HandoffCard` 展示转人工原因与已收集上下文摘要。

`sse.ts` 必须只接受 Task 7 定义的事件名；未知事件记录开发期控制台警告，不改变提交按钮状态。

- [ ] **Step 4: 验证组件与浏览器交互。**

Run: `cd frontend; npm test -- TicketDraftCard.test.tsx`
Expected: 复选框勾选前按钮禁用，勾选后按钮启用，`onSubmit` 仅被调用一次。

Run: `cd frontend; npx playwright test`
Expected: Mock 模式下完成“VPN 问答显示引用”“查单显示状态”“勾选确认后创建工单”三条用例。

- [ ] **Step 5: 提交前端。**

```powershell
git add frontend
git commit -m "feat: add agent support workspace"
```

### Task 9：接入 Trace、结构化审计与用户反馈回流

**Files:**

- Create: `backend/app/core/telemetry.py`
- Create: `backend/app/repositories/agent_runs.py`
- Modify: `backend/app/services/chat.py`
- Modify: `backend/app/tickets/service.py`
- Create: `backend/tests/integration/test_observability.py`

- [ ] **Step 1: 编写 Trace ID 贯通和审计脱敏的失败测试。**

```python
# backend/tests/integration/test_observability.py
def test_ticket_audit_redacts_description_and_keeps_trace_id(client, audit_repository) -> None:
    response = client.post("/api/conversations/c-001/messages:stream", headers={"X-Trace-Id": "trace-test-1"}, json={"user_id": "u-001", "content": "请建单，我的手机号是 13800138000"})
    audit = audit_repository.latest()
    assert audit.trace_id == "trace-test-1"
    assert "13800138000" not in audit.request_summary
```

- [ ] **Step 2: 运行观测测试。**

Run: `cd backend; pytest tests/integration/test_observability.py -q`
Expected: `ERROR`，因为 telemetry 与审计仓储尚未实现。

- [ ] **Step 3: 实现最小可观测性。**

实现 `get_or_create_trace_id`、JSON 日志上下文和 `AgentRunRepository`。每个工作流节点创建 span，属性限于节点名、耗时、结果类别、文档/工单 ID、模型名与 Token；不得写入完整用户输入、完整检索上下文、API Key 或未脱敏工单描述。工具审计用正则替换手机号、邮箱和连续 16 位数字为 `[REDACTED]`。用户 `unresolved` 反馈创建一条 `bad_case` 记录，包含消息 ID、Trace ID、最终状态和引用 ID。

- [ ] **Step 4: 验证 Trace 和反馈回流。**

Run: `cd backend; pytest tests/integration/test_observability.py -q`
Expected: Trace ID 在 Agent Run 与 Tool Audit 一致；PII 不在审计摘要出现；`unresolved` 反馈产生 bad case 记录。

- [ ] **Step 5: 提交可观测性。**

```powershell
git add backend/app/core/telemetry.py backend/app/repositories/agent_runs.py backend/app/services/chat.py backend/app/tickets/service.py backend/tests/integration/test_observability.py
git commit -m "feat: add agent tracing and safe audit logs"
```

### Task 10：构建固定评测集、评测脚本与质量报告

**Files:**

- Create: `data/eval/cases.jsonl`
- Create: `backend/app/evaluation/runner.py`
- Create: `scripts/run_evaluation.py`
- Create: `backend/tests/integration/test_evaluation_runner.py`
- Create: `docs/evaluation-report.md`

- [ ] **Step 1: 写出评测结果含 Recall、引用正确率和安全断言的失败测试。**

```python
# backend/tests/integration/test_evaluation_runner.py
from app.evaluation.runner import evaluate_case


async def test_create_ticket_case_fails_when_graph_writes_without_confirmation(fake_runtime) -> None:
    case = {"id": "ticket-001", "query": "VPN 无法连接，请建单", "expected_final_state": "awaiting_confirmation", "expected_tools": [], "expected_citations": ["vpn-connection.md"]}
    result = await evaluate_case(fake_runtime, case)
    assert result.passed is True
    assert result.created_ticket_count == 0
```

- [ ] **Step 2: 运行评测测试。**

Run: `cd backend; pytest tests/integration/test_evaluation_runner.py -q`
Expected: `ERROR`，因为评测运行器尚不存在。

- [ ] **Step 3: 实现评测输入、判定与报告。**

`cases.jsonl` 每行包含 `id`、`query`、`expected_final_state`、`expected_citations`、`expected_tools`、`expected_handoff_reason`。资料至少 80 条、最多 120 条，其中至少 15 条无答案/高风险用例、15 条查单/建单用例、其余为知识库问答。

`app.evaluation.runner` 运行 Mock 模式图并导出 `evaluate_case`；`scripts/run_evaluation.py` 只负责读取参数和调用该模块。输出：Recall@5、citation precision、最终状态通过率、工具行为通过率、未确认写入次数、P50/P95 时延；任何未确认写入使进程退出码为 1。报告以 Markdown 覆盖总分、失败样本 ID、基线时间、运行命令和数据版本。

- [ ] **Step 4: 运行评测并建立基线。**

Run: `docker compose exec api python /app/scripts/run_evaluation.py --input /app/data/eval/cases.jsonl --report /app/docs/evaluation-report.md`
Expected: 生成报告；`unconfirmed_ticket_writes=0`；若 Recall@5、小于 0.80 或引用正确率小于 0.85，命令以 1 退出并列出失败样本。

- [ ] **Step 5: 提交评测资产与报告模板。**

```powershell
git add data/eval backend/app/evaluation scripts/run_evaluation.py backend/tests/integration/test_evaluation_runner.py docs/evaluation-report.md
git commit -m "test: add reproducible agent evaluation suite"
```

### Task 11：完成端到端验证、文档和演示材料

**Files:**

- Create: `backend/tests/integration/test_end_to_end_flows.py`
- Create: `docs/architecture.md`
- Create: `docs/demo-script.md`
- Modify: `README.md`
- Modify: `scripts/smoke_test.ps1`

- [ ] **Step 1: 为三条主路径编写端到端失败测试。**

```python
# backend/tests/integration/test_end_to_end_flows.py
def test_three_demo_paths(api_client) -> None:
    answer = api_client.ask("VPN 连不上")
    assert answer.citations[0].source_path == "vpn-connection.md"

    status = api_client.ask("查询工单 IT-2026-0001")
    assert status.final_state == "ticket_status"

    draft = api_client.ask("VPN 无法连接，请创建工单")
    assert draft.final_state == "awaiting_confirmation"
    created = api_client.confirm(draft)
    assert created.ticket_number.startswith("IT-")
```

- [ ] **Step 2: 运行端到端测试。**

Run: `cd backend; pytest tests/integration/test_end_to_end_flows.py -q`
Expected: 在完整 API、资料和种子数据尚未就绪时失败。

- [ ] **Step 3: 补全演示与运行文档。**

`README.md` 必须包含：架构图链接、最小环境要求、复制 `.env.example`、`docker compose up --build`、资料导入、评测命令、Mock 与真实模型切换、数据脱敏说明、指标口径和已知限制。

`docs/architecture.md` 固化组件与数据流图。`docs/demo-script.md` 以逐句输入/预期 UI 输出的方式写明三条演示路径，并包含“未知问题转人工”和“未确认无法建单”的安全演示。`smoke_test.ps1` 依次验证服务健康、种子数据、三条 API 主路径和评测报告存在。

- [ ] **Step 4: 执行发布前完整验证。**

Run:

```powershell
docker compose down -v
docker compose up --build -d
powershell -File scripts/smoke_test.ps1
docker compose exec api pytest tests -q
docker compose exec web npm run test -- --run
docker compose exec api python /app/scripts/run_evaluation.py --input /app/data/eval/cases.jsonl --report /app/docs/evaluation-report.md
```

Expected: 所有测试通过；三条主路径通过；评测达到 Recall@5 ≥ 0.80、引用正确率 ≥ 0.85、未确认写入为 0；任何一项未达标都不得发布。

- [ ] **Step 5: 提交可交付版本。**

```powershell
git add README.md docs backend/tests/integration/test_end_to_end_flows.py scripts/smoke_test.ps1
git commit -m "docs: complete agent MVP delivery guide"
git push origin main
```

## 3. 设计需求到任务的覆盖核对

| 已确认需求 | 覆盖任务 |
| --- | --- |
| 本地全栈、Docker Compose、Mock 模式 | Task 1、Task 3、Task 11 |
| 模拟用户、会话、消息、审计与工单持久化 | Task 2、Task 4、Task 9 |
| 30–50 篇模拟知识库、增量导入与权限预留 | Task 5 |
| Qdrant + BM25 混合检索、重排和引用 | Task 5 |
| LangGraph 显式状态机、最大步骤、无证据转人工 | Task 6 |
| 查单、草稿、显式确认、幂等写入 | Task 4、Task 6、Task 7、Task 8 |
| SSE、引用、过程透明、反馈与人工兜底 UI | Task 7、Task 8、Task 9 |
| Trace、PII 脱敏、bad case 回流 | Task 9 |
| 80–120 条评测、Recall/引用/安全/性能指标 | Task 10、Task 11 |
| README、演示脚本和可复现交付 | Task 1、Task 11 |

## 4. 计划自检

- 每项 MVP 需求均映射到至少一个可测试任务，未包含真实 SSO、外部工单、多租户或自动高风险运维操作。
- 任务中未使用未决占位语、模糊的“适当处理”或“后续实现”等表述；每个实现任务均给出确切文件、测试和验证命令。
- 类型在任务间统一：`TicketDraft`、`confirmation_token`、`idempotency_key`、`AgentState`、`Citation`、`final_state` 和 `trace_id` 只使用上述定义。
- 提交命令均为显式路径；执行时继续排除工作区中未提交的 `面试总结.md`。
