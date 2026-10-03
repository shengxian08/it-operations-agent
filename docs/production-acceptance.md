# 正式环境验收与发布证据

2026-10-02 较早执行的测试数量与全栈结果保留在下表。共享对话后的新增 PDF/人工交接修复与最新本机证据见 [project-audit.md](project-audit.md)：287 后端、27 前端、18 demo/production-mock 浏览器用例通过。新增结果没有重跑本文件的 production-live、Linux、真实模型、容量或完整恢复门禁，因此不能继承为新版的完整生产验收。

范围为单企业、100 名员工、10 个并发对话、500 篇知识文档；RPO ≤ 24 小时、RTO ≤ 4 小时。功能测试、模型质量、容量、恢复、回滚和告警分别留证。测试账号、模拟数据和 MODEL_MODE=mock 的结果不能替代真实员工部署或真实模型验收。

## 当前已验证与未验证

2026-10-02 的本机独立容器验证：

| 项目 | 已取得的证据 | 适用范围 |
|---|---|---|
| 后端 | 全量 263 tests passed，42.94 秒；PostgreSQL/Redis/Qdrant 隔离服务 | 本机功能、安全边界、事务、租约与版本恢复；最终数量以最新测试报告为准 |
| 前端 | 23 单测、严格 TypeScript/正式构建通过 | 默认正式入口、API 错误、会话并发、未知结果幂等、断流恢复、账户版本冲突与索引轮询 |
| 浏览器 | 4 demo + 9 production-mock E2E 通过 | 旧 demo 保留与正式接口情景；不验证外部模型 |
| 真实身份/全栈 | production-live 2 tests passed，真实 Keycloak、生产静态 Web、API/worker/PG/Redis/Qdrant | 三角色登录、知识发布/停用/回滚、202 运行刷新恢复、确认建单、支持闭环、账户禁用/启用/logout；聊天模型仍为 mock |
| 语义 embedding | CPU worker 镜像构建、固定 BGE revision 下载、断网 512 维归一化编码与 VPN 相关文档首位检索 probe | 固定模型可在 CPU 离线执行；不是 500 文档质量或容量结果 |
| 数据库恢复 | 隔离测试数据库备份 3.02 秒，恢复至空 `itops_restore_test` 0.87 秒；restore_probe 完成两个 durable worker 任务、知识回答带引用、确认草稿与同 key 两次确认仅新增一张工单 | PostgreSQL 恢复后业务探针，复用现有 test Qdrant；尚非完整 Linux 空机/files/身份/向量/外部恢复和 RPO/RTO |
| TLS 网关 | 独立 TLS proxy 探针验证静态200、CSP/HSTS/nosniff、me401/no-store、metrics/ready404、Keycloak admin/master 有无末尾斜线均404、SSE代理配置 | 本机临时自签证书/隔离网关；正式证书、DNS及企业 receiver 仍由目标环境验收 |
| 最小权限 | 独立 PostgreSQL 初始化实测 migrator建表、应用DML成功/建表拒绝；Qdrant 无key401、只读key查询200/创建403、写key创建200 | 本机真实 PG/Qdrant 权限边界 probe，临时 secret/容器/网络已清理 |
| 镜像应用一致性 | API/worker从 `/tmp` 导入 installed package，48个Python文件逐bytes与源码一致，两个应用hash相同 | 实际已构建镜像；CI每次构建继续保存同探针证据 |
| CI | workflow 已实现 | 需由目标 Git 平台实际运行；本机检查不能声称 CI job 已成功 |

目标 Linux 主机的完整空环境 secrets/数据库/文件/Qdrant 恢复与 RTO、同 schema 旧镜像回滚、真实模型 Recall/引用质量与 30 分钟 P95、外部备份副本校验和实际告警接收仍须独立验收。本机单库备份/恢复冒烟只证明相应步骤可执行。

## 可重复的隔离工程测试

在新的 CI runner 或独立测试主机运行，端口 15932、16379、17333、18517 和 8080 必须空闲。不要在已有共享测试或生产服务所在主机执行 down/recreate PostgreSQL。`compose.test.yml` 的数据库和 Qdrant 使用 tmpfs，容器重启后数据丢失；初始化脚本创建 `itops_e2e`、`itops_schema_test` 和 `itops_restore_test`。

```bash
docker compose --project-name itops-acceptance-test -f compose.test.yml up -d --wait
export ENVIRONMENT=test MODEL_MODE=mock DEMO_ENABLED=true COOKIE_SECURE=false
export TEST_DATABASE_URL=postgresql+asyncpg://itops_test:isolated-test-only@127.0.0.1:15932/itops_schema_test
export TEST_REDIS_URL=redis://127.0.0.1:16379/0
export TEST_QDRANT_URL=http://127.0.0.1:17333
export DATABASE_URL="$TEST_DATABASE_URL" REDIS_URL="$TEST_REDIS_URL" QDRANT_URL="$TEST_QDRANT_URL"
export PYTHONPATH="$PWD:$PWD/backend"
cd backend
uv sync --frozen --extra dev --python 3.11
uv run --frozen --extra dev ruff check --config pyproject.toml app tests ../scripts
uv run --frozen --extra dev alembic upgrade head
uv run --frozen --extra dev alembic downgrade base
uv run --frozen --extra dev alembic upgrade head
uv run --frozen --extra dev alembic check
uv run --frozen --extra dev pytest tests -q
cd ../frontend
npm ci
npm test
VITE_DEMO_ENABLED=false npm run build
npx playwright install --with-deps chromium
npx playwright test --project=demo --project=production-mock
cd ..
```

upgrade/downgrade/up/check 仅在上述可丢弃 schema 测试库执行，生产不 downgrade。全部 TEST URL 必须设置，否则真实依赖测试被跳过。检查报告中的 passed/skipped、测试命令与镜像 commit，避免把仅单元测试当全量测试。

真实身份 E2E 仍用独立 test realm 和可控模型，在同一隔离项目叠加 `compose.e2e.yml`：

```bash
docker compose --project-name itops-acceptance-test -f compose.test.yml -f compose.e2e.yml up -d --build --wait --wait-timeout 180
cd frontend
export E2E_BASE_URL=http://127.0.0.1:18517
export E2E_EMPLOYEE_USERNAME=e2e-employee E2E_EMPLOYEE_PASSWORD=E2e-only-employee-123
export E2E_SUPPORT_USERNAME=e2e-support E2E_SUPPORT_PASSWORD=E2e-only-support-123
export E2E_ADMIN_USERNAME=e2e-admin E2E_ADMIN_PASSWORD=E2e-only-admin-123
npx playwright test --project=production-live
cd ..
```

Keycloak ready 后再执行浏览器测试。上述凭据仅来自仓库的独立测试 realm，绝不能复制到正式环境。production-live 项目仅在测试时将浏览器 hostname `keycloak` 映射为 loopback；正式环境使用真实 HTTPS issuer。测试完在独立主机执行该项目的 `down --volumes`，不要误操作其他项目。

## Mock 逻辑门禁与真实模型质量

CI 在独立测试服务中检查 mock 图执行、评测 CLI 和 release gate 的成功/失败行为。可单独复现逻辑门禁：

```bash
cd backend
uv run --frozen --extra dev pytest tests/integration/test_evaluation_runner.py \
  tests/unit/test_evaluation_gate.py tests/unit/test_evaluation_cli.py -q
```

`scripts/run_evaluation.py` 使用统一正式配置、`build_retriever` 和 structured citations，并固定一次已发布 index revision。评测图使用只读工单工具适配器，验证工具选择；确认写入事务另由真实 PG 集成测试验证。仓库旧 84 例缺少完整 `relevant_sources` 标注，报告应显示 citation precision 未测量，release gate 失败，不能把 `expected_citations` 直接复制为完整相关集合而制造 precision 分数。带完整人工相关性标注的独立固定集才能用于真实质量门禁。

真实模型验收必须在批准的 HTTPS staging 上使用相同 production API/worker、固定 BGE512、正式知识版本权限与经批准的外部模型 endpoint。采用有知识预期来源、无答案、受限操作、工单查询/确认场景的脱敏标注集，逐例记录 query ID、principal 角色、run/trace、active revision、引用 document/chunk ID、最终状态、工具行为、Token/成本和时延；不将真实敏感输入提交到仓库。使用正式评测 CLI 获取可复核的 JSON 报告，或实际 `/api/v1/conversations/{id}/runs` 202 与 SSE/GET run 取得浏览器链路结果，独立计算 Recall@5、引用 precision 和安全/未确认写入，保存失败样本供人工复核。门禁要求 Recall≥0.80、precision≥0.85、完整相关性标注、关键最终状态/工具行为100%、无越权知识泄露及未确认工单写入=0；签署真实模型报告后才能设置 `real_model_evaluation_passed=true`。

使用已发布的知识 revision，所有 `expected_citations` / `relevant_sources` 为知识**文件名**（例如 `vpn-handbook.md`），相关集合应由独立人工完整标注。评测用户必须是 enabled 正式账号并拥有给定会话，工单用例使用其真实拥有的测试工单号。Docker worker 镜像只带 backend，运行仓库 scripts 时显式挂载只读脚本和私有数据，报告目录需对容器 UID10001 可写：

```bash
# dc 指向批准的 staging 配置；数据与报告目录由运维事先创建并设置权限。
dc run --rm --no-deps -v "$PWD/scripts:/app/scripts:ro" \
  -v /secure-acceptance/dataset:/evaluation:ro -v /srv/itops/evidence:/reports worker \
  python /app/scripts/run_evaluation.py --input /evaluation/annotated-cases.jsonl \
  --report /reports/real-model-quality.md --json-report /reports/real-model-quality.json \
  --index-revision <published-revision-id> \
  --user-id <enabled-evaluation-user-id> --conversation-id <owned-conversation-id>
dc run --rm --no-deps -v "$PWD/scripts:/app/scripts:ro" \
  -v /secure-acceptance/dataset:/evaluation:ro -v /srv/itops/evidence:/reports:ro worker \
  python /app/scripts/evaluate_release.py --report /reports/real-model-quality.json \
  --dataset /evaluation/annotated-cases.jsonl --index-revision <published-revision-id> \
  --require-production-model
```

JSON schema-2 报告保存数据 SHA256、实际模型/embedding版本、index revision和逐例输出；第二条命令从数据与输出重新计算门禁，不信任报告里预填的 PASS 或汇总分数，并以 `--require-production-model` 拒绝 demo/mock/未固定 embedding 证据。结果还必须核对 runtime.model_mode 为 openai、embedding 与批准版本一致，并确认供应商实际调用证据；mock runtime 即使逻辑门禁成功，也不得作为真实模型证据。演示需要显式 `run_evaluation.py --demo`，仅限 development/test，退出0表示逻辑回归，report中的未测量 quality/release gate 仍保留失败状态。

## 30 分钟容量测试

先由管理员按正常上传→解析预览→发布流程准备至少 500 篇员工可访问的代表性知识文档；应包含典型 Markdown/PDF、长度与分块数量、访问级别、已知/无答案案例，报告记录总数和数据版本。测试 10 名**不同**已登录用户，避免单账号限流扭曲容量；另验证总体 100 名账号的可用性。使用 staging 真实模型、生产资源限额和批准预算，保持 worker_concurrency=10，不提高上限来通过验收。

私有 sessions 文件只保存在 0700 目录/0600 文件，格式为 `{"accounts":[{"cookie":"it_assistant_session=<session-cookie>","csrf_token":"<csrf-token>"},...]}`；至少 10 项。查询文件是 JSON 字符串数组，选用具有知识答案的代表性问题。会话 Cookie 和 CSRF token 来自正常登录，测试后删除/失效，不上传为 CI artifact。

```bash
cd backend
uv run --frozen --extra dev python ../scripts/production/load_test.py \
  --base-url https://<approved-staging-host> --environment staging \
  --sessions /secure-acceptance/sessions.json \
  --queries /secure-acceptance/queries.json \
  --duration 1800 --concurrency 10 --documents 500 --real-model \
  --output ../output/acceptance/capacity.json
```

script 检查 HTTPS、账户数、10名不同的实际 `/me` 身份、可访问文档数；预检完成后才开始 `actual_load_seconds` 计时，每个并发对话执行只读工单请求、durable run 与 SSE/GET reconciliation。每次提交对话默认总deadline150秒（`--run-timeout`），包括SSE心跳，断流不会无限拖延测试。门禁要求：实际负载至少 1800 秒、10 并发、≥500 文档、无失败操作、至少 10 个回答成功、读接口 P95<0.5 秒、检索 P95<2 秒、回答端到端 P95<30 秒，并启用真实模型验收标记。`--real-model` 是运维声明，仍必须以 staging 配置和供应商用量证据确认模型确实是真实 API。短时间/Mock 测试即使零错误也始终 `capacity_passed=false`。

同时保存 CPU/RSS、数据库连接、Redis内存、队列长度/运行时间、worker heartbeat、索引任务、磁盘空间、向量集合数量及供应商用量，确保无 OOM/泄漏/不断增长积压。单次回答耗时统计包含读工单和最终 GET，对比图节点检索统计解释瓶颈。失败后先分析，再调整资源/检索/预算；不得删除慢请求、失败样本或更改阈值后声称原配置通过。

## 恢复、回滚与告警验收

按 [运维手册](production-operations.md) 从外部下载加密备份并在真正空环境完成 secrets/config、PG两个库、原始文件、Qdrant、固定模型缓存、身份和业务探针。记录最后恢复数据时间与故障时间，RPO ≤24h；从宣布故障到可用业务入口的总时间（含下载/解密/模型准备）RTO ≤4h。验证知识引用、历史、草稿/确认幂等、工单评论/审计和身份权限，并比较计数/active pointer，才算 `restore_passed`。

在 staging 升级同 schema 兼容的新镜像后，按旧 commit 回滚 API/worker/Web，验证任务恢复和同一确认请求没有重复工单，记录升级前/后/回滚后 revision 和 image digest，才算 `rollback_passed`。如果迁移不兼容旧镜像，必须记录空环境备份恢复路径及数据差额，不能拿 test 数据库 downgrade 代替正式回滚验证。

故障演练须实际送达企业批准的 receiver，并由值班人员确认；保存告警规则、触发/接收/确认时间和恢复通知。仅校验 Alertmanager YAML 或 Prometheus firing 不算 `alerts_passed`。

## 发布 evidence

保存精确 commit 对应的结构化 evidence 与上述报告、image digest、数据/模型版本和批准记录。未执行的项目保持 false，模板不是可发布结果：

```json
{
  "image_tag": "<full-commit-sha>",
  "tests_passed": false,
  "real_model_evaluation_passed": false,
  "capacity_passed": false,
  "restore_passed": false,
  "rollback_passed": false,
  "alerts_passed": false
}
```

所有真实验收完成后，运维填写证据并执行 `ops.py release --evidence ... --image-tag ...`。release 接受门禁布尔值而不替代报告审查，批准人员应核对来源、环境、commit、阈值和实际时间。本机测试及 CI 产物本身不授权外部部署。
