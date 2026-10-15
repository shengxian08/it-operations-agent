# 本地运行与复现

以下命令面向 Windows PowerShell，使用本仓库独立依赖环境、公开合成数据和固定隔离端口。执行前核对端口和 compose 项目所有权。演示不会连接付费模型、企业身份或外部工单渠道。

```powershell
Push-Location backend
uv sync --locked
Pop-Location
Push-Location frontend
npm ci
npm run build
Pop-Location
docker compose -p itops-agent-deepening-test -f compose.test.yml up -d --wait postgres redis qdrant
$env:ENVIRONMENT='test'
$env:MODEL_MODE='mock'
$env:DEMO_ENABLED='true'
$env:TEST_DATABASE_URL='postgresql+asyncpg://itops_test:isolated-test-only@127.0.0.1:15932/itops_testing'
$env:DATABASE_URL=$env:TEST_DATABASE_URL
$env:TEST_REDIS_URL='redis://127.0.0.1:16379/15'
$env:TEST_QDRANT_URL='http://127.0.0.1:17333'
$env:PYTHONPATH="$PWD/backend;$PWD"
Push-Location backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m pytest -q --tb=short
.venv/Scripts/python.exe -m ruff check --config pyproject.toml app tests ../scripts
Pop-Location
```

在一个终端运行 `backend/.venv/Scripts/python.exe scripts/agent_demo_server.py`，另一个运行 `backend/.venv/Scripts/python.exe scripts/agent_protocol_demo.py`。后者用独立 MCP 客户端验证 stdio 与 HTTP，上传公开 Markdown，等待实际解析/索引完成，预览、发布候选关系，再检索知识。演示地址为 `http://127.0.0.1:18201`。

演示服务器写入 `artifacts/agent-deepening/demo/session.json`，其中只有明确标记的公开测试身份；它没有企业登录意义，不应部署到公开网络。浏览器脚本读取该测试会话。可选工具环境安装 `playwright==1.62.0` 并配置 Chromium 后，使用该环境运行 `scripts/agent_browser_demo.py`。演示依次核对新会话、模式、实际提交请求、报告、快照时间和页面重连，输出截图与 JSON。

正式应用开关：`AGENTIC_RAG_ENABLED`、`RELATION_RETRIEVAL_ENABLED`、`INVESTIGATION_STRATEGY=single|multi`、`MCP_ENABLED`。复杂策略默认不启用；MCP HTTP 入口为 `/agent-tools/mcp`，沿用应用会话、Origin 与 `X-CSRF-Token`，工具参数不传用户身份。

独立 stdio 公开快照入口：

```powershell
$env:PYTHONPATH="$PWD/backend"
backend/.venv/Scripts/python.exe -m app.agent.mcp_server --snapshot backend/app/data/operations-public-snapshot.json
```

与电商隔离 kind 环境互通时，运行 `backend/.venv/Scripts/python.exe scripts/agent_kubernetes_demo.py --kubeconfig <任务专用kubeconfig绝对路径>`。它固定读取 `kind-agent-deepening-20261003`、namespace `agent-deepening` 的 API Deployment。生产接入需单独配置受限身份、目标资源与指标提供器。

```powershell
Push-Location frontend
npm test -- --maxWorkers=2
npm run build
npm run test:e2e -- --project=demo --project=production-mock --workers=1
Pop-Location
backend/.venv/Scripts/python.exe scripts/agent_evaluation.py
backend/.venv/Scripts/python.exe scripts/agent_evaluation.py --export-jobs artifacts/agent-deepening/evaluation/jobs.jsonl
```

报告与原始执行记录位于 `artifacts/agent-deepening/`；精选证据位于 `docs/agent-deepening/evidence/`。测试库使用 tmpfs，重启会丢失合成数据。不要把本说明中的 downgrade、恢复或种子流程应用于共享数据库。
