# 企业 IT 助手生产工程实施记录

2026-10-02。用户批准：单企业、100 员工、10 并发、500 文档，单 Linux 主机；OIDC/Keycloak、自建工单、批准的外部聊天 API。

## 共享实现契约

- 正式业务前缀 `/api/v1`；旧 `/api` 仅 `DEMO_ENABLED=true` 注册。生产必须关闭 demo。
- Settings 统一管理：environment, demo_enabled, public_base_url, oidc_issuer/client_id/client_secret, session_secret/cookie_secure/ttl_seconds, csrf origin；worker_concurrency=10, run_timeout_seconds=120, lease_seconds=30, user_rate_limit=5, model_timeout_seconds=45, model_max_retries=1, model_max_output_tokens=1500；model_monthly_budget/input_price/output_price；embedding_mode/model/revision, qdrant_collection/api_key/read_api_key, knowledge_storage_path。
- 身份模块 `app.production.identity` 导出 Principal(user_id, display_name, role)、`require_principal`、`require_support`、`require_admin`。认证从 HttpOnly Cookie + Redis session 获取；写接口验证 CSRF。`GET /api/v1/me` 返回 id/display_name/role/csrf_token，未登录 401。登录/callback/logout 在 `/api/v1/auth/*`。
- 会话：POST/GET `/conversations`；GET `/conversations/{id}/messages` -> {messages,next_cursor}；POST archive。仅本人可见。
- 运行：POST `/conversations/{id}/runs` body {content,client_message_id}、Idempotency-Key -> 202 {id,status,trace_id}；GET `/runs/{id}`；GET `/runs/{id}/events?after=sequence`；POST cancel。事件为现有事件名、SSE id 为单调序号，终态附 final；状态 queued/running/completed/failed/cancelled。
- 前端恢复：GET `/conversations/{id}/runs` 返回近期运行，包含待确认草稿。GET run 返回 {id,status,trace_id,result,events}；result 为 final payload。Worker 通过 `app.runtime.build_chat_service(settings, user_access_level, index_revision)` 获取服务。
- 业务模块 `app.production.business` 导出 `router`、`ProductionTicketService(session_factory, settings)`，支持旧图需要的 get_ticket_status / issue_confirmation_token 接口，确认记录落 PostgreSQL。草稿事件在 worker 通过 token 查出 draft_id/version。
- 草稿：GET `/ticket-drafts?conversation_id=...`；PATCH `/ticket-drafts/{id}` body {version,draft}；POST `/ticket-drafts/{id}/confirm` body {version,confirmation_token} + Idempotency-Key；成功 {ticket_number,status}，冲突409，过期410。凭证每次修改重签，确认和工单创建单事务，trace不参与幂等。
- 工单：GET `/tickets` -> {tickets,next_cursor}；GET `/tickets/{number}`；PATCH body {version,status?,assignee_id?}；POST comments body {version,content}。员工仅本人，support/admin 企业全局；重新处理 resolved/closed -> in_progress。
- 升级：GET `/escalations`；PATCH `/escalations/{id}` body {version,status,assignee_id?}；Worker handoff调用业务服务持久化独立升级记录，不能自动写正式工单。
- 知识：GET `/knowledge/documents` -> {documents,next_cursor}；GET `/knowledge/documents/{id}` -> article sections；POST `/knowledge/uploads` multipart file/access_level/title；GET `/knowledge/jobs`；GET job；POST `/knowledge/jobs/{id}/publish`；POST document deactivate；POST `/knowledge/revisions/{id}/activate`。管理员权限，上传只是准备任务，不自动发布。
- RAG 工厂 `app.production.knowledge.build_retriever(session_factory,qdrant,settings,index_revision=None)`；共享 `build_embedder(settings)`；production source/snapshot/chunk 表按 index_revision 隔离。生产索引只从单一 PG active pointer 获取并固定每个run版本。关键词缓存按版本与允许访问等级构建，当前文档权限再次校验。
- 新表全部使用现有 Base，放在 `production/*_models.py`；根代理统一编写 migration0003，避免共享模型文件冲突。
- app.state 提供 settings, session_factory, redis, qdrant, identity_service, run_service, ticket_service, knowledge_service。各router通过request.app.state读取。

## 分工与验收

1. 身份、会话、持久化任务、worker（代理A）；真实数据库测试验证幂等/租约/隔离。
2. 工单确认事务、支持闭环、知识版本/检索（代理B）；真实数据库与Qdrant验证发布/回滚/确认。
3. 正式前端（代理C）；单测、类型构建与生产端到端。
4. 根代理：配置、runtime/main集成、迁移、生产/测试Compose、依赖锁、日志指标、评测门禁、CI、运维/备份恢复/压测脚本。

## 决策记录

- 在当前工作目录创建新分支实现，保留全部未提交修改；不创建会遗漏当前新功能的干净 worktree，不提交用户既有改动。
- 外部部署、企业真实凭据、真实模型费用和30分钟容量/RPO/RTO演练无法凭本机代码替代；提供可执行脚本并在本机独立容器验证可验证部分。
- 登录采用标准OIDC客户端实现，模型供应商/价格/预算为运维必填参数，不编造供应商价格。

## 进度

- 基线差异保存 `.superpowers/production/baseline.patch`；未操作既有其他项目容器。
- 工程实现已完成并在本机独立依赖中验证；尚未向企业 Linux 主机部署。

## 最终实现与验证记录

- 身份：Keycloak OIDC 授权码、PKCE/state/nonce/JWKS 校验，Redis 服务端会话、HttpOnly/Secure Cookie 与 CSRF；所有正式业务取服务端身份。管理员账户角色覆盖、启停和 optimistic version，最后管理员保护；禁用/降权与活动任务停止同事务，Worker 各持久化边界再次核对权限。
- 可靠执行：PostgreSQL run/message 幂等、单会话活动任务约束、租约与 fencing、序号事件和终态原子提交；取消、超时与失去执行者均收敛，独立 Worker 不依赖浏览器连接。预算按重试上限预留，已知用量结算、未知用量保守结算。
- 建单与支持：PostgreSQL 草稿版本/凭证/确认/工单同事务，重复确认只创建一次，修改重签；工单分派、状态、评论与员工重开、审计和独立人工升级记录。
- 知识：不可变上传与有界 PDF 子进程解析、预览后异步索引队列、租约与当前管理员复核、原子版本切换/停用/回滚。发布同文件名更新文章版本，旧 snapshot 保持；批量导入默认追加/更新并保留 frontmatter 权限，不进行正式目录删除。
- RAG：统一 factory 与模型/分块/pipeline 元数据；固定 BGE commit `7999e1d3359715c523056ef9478215996d62a620`，实际中文模型是 **512 维**。查询使用中文检索指令，文档编码不加该指令；运行固定知识版本，BM25 按版本和角色缓存，并复查当前原文权限。严格结构化引用与证据不足人工兜底。
- 部署：正式 API、CPU Worker、静态 Nginx Web 镜像；独立 production/test/e2e Compose。私有依赖、数据库应用/迁移权限分离、Qdrant 读写凭据分离、受控 secret 文件、TLS/SSE 网关、健康检查与资源限额。生产 API 不挂载或引用 Qdrant 写密钥文件。
- 发布与运维：冻结 uv/npm 依赖，源码参与本地 wheel 缓存键；镜像从 `/tmp` 导入的安装包与源码逐文件一致验证。CI 输出精确 commit 镜像 artifact；release 先检查证据、停入口、迁移并等待后台健康，最后等待网关健康，失败保持维护窗口。JSON 安全日志、私有指标/Prometheus/Alertmanager、留存、加密外部备份、空目标恢复和容量脚本已交付。
- 运维独立审查的问题已修复：所有目标数据库、知识卷和 Qdrant 在写入恢复前预检；备份 snapshot 下载后 finally 删除临时服务器副本；容量计时排除身份/文档前检并要求实际负载 ≥1800 秒，持续 SSE heartbeat 不能绕过总 deadline。

2026-10-02 最终本机证据：

| 验证 | 实际结果 |
|---|---|
| 后端完整测试（真实隔离 PostgreSQL/Redis/Qdrant） | **263 passed，42.94 秒，零 skip**；报告 `artifacts/backend-final.xml` |
| Ruff 与 Python 编译、uv lock check | 通过；冻结 124 个依赖 |
| 前端 | 23 单测、13 demo/production-mock E2E、严格 TypeScript 与正式构建通过 |
| 最终镜像 production-live | **2 passed，12.7 秒**；真实 Keycloak 三角色与完整业务，模型 mock，保留历史数据重跑 |
| 镜像工件 | API、CPU Worker、静态 Web 构建通过；API/Worker 安装包 48 个 Python 文件与源码相同 |
| 语义模型 | 最终 CPU Worker 在 `--network none` 加载固定权重，512 维、归一化与 VPN 文档首位排名通过 |
| Alembic | 新隔离空数据库 upgrade head → downgrade base → upgrade head → check 通过，无待生成迁移 |
| PostgreSQL/Qdrant 生产权限 | 实际独立容器：应用 DML 成功/建表拒绝；向量 read GET200/write403、writer PUT200、无密钥401 |
| TLS 网关 | 校验证书的自签测试：静态与安全头、未登录401/no-store、私有入口404、master/admin 四种路径404、SSE代理配置通过 |
| PostgreSQL 备份恢复 | 独立测试库备份3.02秒、恢复至空库0.87秒；恢复后两次持久运行、引用、同 key 确认仅新增一张工单通过 |

本机恢复探针复用现有 test Qdrant，不能证明完整空 Linux 主机、原文件、身份库、向量快照和外部副本的恢复目标。真实外部模型质量、完整相关性标签、500 篇知识/10 并发持续30分钟容量、实际 Git 平台 CI、外部 receiver 送达、目标主机整机恢复和兼容旧镜像回滚仍须按验收手册执行。RPO ≤24小时、RTO ≤4小时保留为正式验收目标，未宣称已达到。

完整操作入口：[部署](production-deployment.md)、[运维](production-operations.md)、[验收](production-acceptance.md)。当前分支为 `codex/production-engineering`，保留原未提交工作，没有自动提交或外部推送。
