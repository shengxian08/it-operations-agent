# 共享对话驱动的项目自查与实施

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](verification/README.md)。

审查日期：2026-10-02。依据[用户指定的完整共享对话](https://chatgpt.com/share/6abf375e-b318-83ea-bfbf-2556d572e59b)。审查对象是当前工作区，包括已有未提交正式环境实现；基线分支 `codex/production-engineering`，HEAD `979ed7f`。本轮没有提交、推送或部署。

最初审查完成四条业务链核对、PDF 证据闭环和人工交接提示修复。后续已完成来源型 Markdown、T02 查单、T03 信息收集、T04 显式人工请求与上下文及 T05 最新可见进度的本机软件合同；真实质量、目标环境、剩余增量独立最终审查与GitHub交付仍是明确待办，见 [improvement-backlog.md](improvement-backlog.md)。这份报告不把当前项目判定为已具备真实企业生产验收条件。

2026-10-02 后续实施更新：用户批准技术复审方案后，已实施[来源型 Markdown](architecture/markdown-structure.md)：实际结构与出处、原子代码/配置、真实token预算、严格新旧manifest、同hash逐篇重解析、历史引用/权限/回滚及预览。本阶段基于当前工作区，保留前阶段PDF与技术实验历史证据；下面原表中的计数属于它们各自记录时间。当前验收、独立审查及源码hash见 Markdown实施证据（本地证据：`artifacts/markdown-structure/implementation-evidence.json`）。该阶段未覆盖T02/T03/T04及真实样本/答案/目标部署；T02的后续状态见下方2026-10-03更新。

## 分类与本轮验证

2026-10-03 T02 实施更新：已完成[多轮查单与澄清](architecture/ticket-lookup.md)的本机软件合同，修复唯一独立审查的4项Important发现后，当前全量419后端、36前端、24浏览器通过，failures/errors/skipped均为0，构建、Ruff与锁文件检查通过。真实隔离PG/Redis、图、工单工具、HTTP/SSE与Mock HTTP浏览器分别留证；当前源码hash、独立原始审查（本地证据：`artifacts/ticket-lookup/final-review.md`）、作者一次修复验收（本地证据：`artifacts/ticket-lookup/review-fixes.md`）及逐项完成审计（本地证据：`artifacts/ticket-lookup/completion-audit.md`）见实施证据（本地证据：`artifacts/ticket-lookup/implementation-evidence.json`）。审查者没有独立重跑修复后的全量依赖与浏览器，不把作者验收冒认独立批准。下表较早计数仍表示2026-10-02记录时间。T03/T04/T05、私有质量和目标环境仍为独立待办。

2026-10-03 T04 实施更新：T03 已完成必填信息收集后，现已完成[显式人工请求与持久上下文](architecture/handoff-context.md)的作者本机软件合同。最终 **513 后端、43 前端、28 浏览器 passed，failures/errors/skipped 均为 0**，构建、Ruff 与锁文件检查通过。真实隔离依赖、受控模型/资料及 Mock HTTP 浏览器分别留证；18 份修改前文件、冻结源码 hash、RED/逆向用例与原 HANDOFF-002 探针见实施证据（本地证据：`artifacts/remaining-tasks/handoff-context/implementation-evidence.json`）。整个剩余软件增量的唯一独立最终审查尚未执行，T05、T06/T07 与 GitHub 交付保持待办。上方 T02 及下表计数属于各自记录时间。

2026-10-03 T05 实施更新：已完成[最新可见工单进度](architecture/ticket-progress.md)，最终 **537后端、46前端、30浏览器passed，failures/errors/skipped均0**；build/Ruff/lock和迁移一致性检查通过。24项真实隔离PG/Redis/ASGI/worker进度用例验证实际记录时间、公开/内部/未分类、来源权限和全回放入口、并发/回滚/错误及锁等待后降级。20份before、当前冻结源码与全部RED/中间失败见本地实施证据（本地证据：`artifacts/remaining-tasks/ticket-progress/implementation-evidence.json`）。1440/390 Mock HTTP截图已分阶段人工核对；production-live新增用例本阶段未执行，T06/T07、唯一最终独立审查与GitHub交付仍未完成。以上各阶段记录保留原时间和原计数。

分类：**已实现并验证**指本轮执行过相应测试；**实现但未验证**指代码存在而当前目标环境未验；**Mock**指受控依赖/模型；**仅文档**不等于已落地；**缺失**指有代码/探针证明行为未实现；**环境阻塞**指真实样本、模型或目标环境缺少。

| 范围 | 本轮证据 | 能证明的边界 |
|---|---|---|
| 修改前基线 | audit-baseline-ready.xml（本地证据：`artifacts/audit-baseline-ready.xml`）：263 passed，0 skipped，40.29 秒 | 隔离依赖迁移后当前已有功能的基线 |
| 最终后端 | audit-final.xml（本地证据：`artifacts/audit-final.xml`）、可读日志（本地证据：`artifacts/audit-final.log`）：287 passed，0 failures/errors/skipped，44.35 秒 | 本机 Python、真实隔离 PG/Redis/Qdrant；部分模型/worker 边界仍用受控替身 |
| PDF 解析 | pdf-review-green.xml（本地证据：`artifacts/pdf-review-green.xml`）：19 passed；初始与逆向审查失败见 pdf-evidence-red / pdf-review-red / pdf-legacy-red | 实际本地 PyMuPDF 文件解析；样本为合成 PDF |
| 知识快照 | pdf-chain.xml（本地证据：`artifacts/pdf-chain.xml`）：21 passed，17.19 秒；最终全量再次包含 | 实际上传、子进程、预览、PG/Qdrant、版本、权限与停用；模型受控、embedding 确定性 |
| 前端 | audit-frontend.log（本地证据：`artifacts/audit-frontend.log`）：27 passed；audit-build.log（本地证据：`artifacts/audit-build.log`） 构建成功 | Vitest、严格 TypeScript、Vite 正式构建 |
| 浏览器 | audit-e2e.xml（本地证据：`artifacts/audit-e2e.xml`）：18 passed，0 skipped，7.38 秒 | 4 demo + 14 production-mock，真实 Chromium 渲染，HTTP 响应由测试拦截；不是实时生产 API 验收 |
| 代码与锁文件 | Ruff `All checks passed`、`uv lock --check` 通过 | 本轮静态检查；没有新增 OCR/模型/解析依赖 |
| 业务补全探针 | business-audit-probes.json（本地证据：`artifacts/business-audit-probes.json`）、[可重复脚本](../scripts/audit_business_paths.py) | 无数据库写入、无模型 API 的图路径诊断；不能替代正式业务事务验收 |

首次测试有缺少 schema 的 4 failures/33 errors；只对本轮可丢弃测试数据库执行 `alembic upgrade head` 后取得有效基线。实施中全量曾有 8 errors，原因是旧检索测试使用 400 字正文窗口与完整表格行冲突；修复为独立的 800 字表格行预算，没有删测试或降低门槛。上述失败日志保留，最终结果独立保存。

## 四条实际业务链

| 员工路径 | 实际实现入口 | 分类与审查结论 |
|---|---|---|
| 知识问答 | [graph.py](../backend/app/agent/graph.py)、[knowledge.py](../backend/app/production/knowledge.py)、rag/ingest.py 与 retriever.py | 已实现并验证：真实 PG/Qdrant 召回、访问过滤、当前权限复核、模型 JSON 引用编号校验、引用版本。PDF 结构修复见下文。真实 PDF/真实模型的相关性与答案正确性仍未验证，合法引用编号不保证语义被支持。 |
| 工单查询 | graph.lookup_ticket → [ProductionTicketService.get_ticket_status](../backend/app/production/business.py) → 工单库 | T02/T05已实现并本机验证：受控对象与澄清、每次当前账号/工单鉴权、最新可见评论/白名单审计及实际时间。公开/内部/未分类明确，当前与原快照权限约束历史回放，锁等待后再次鉴权；空记录、查无与数据库故障不同。真实企业身份与目标部署未验。 |
| 创建工单 | collect_ticket_draft → issue_confirmation → draft 编辑/重签 → confirm_draft 事务 | T03已实现并本机验证：逐轮收集问题/影响/明确尝试事实，完整才签名；旧草稿需补全重签。原版本/凭证、过期、10并发一单、回滚和未知响应重试仍通过，见下方 CREATE-001。 |
| 人工交接 | graph 的 manual_handoff 意图 → [worker.py](../backend/app/production/worker.py) → record_handoff / runs._finish → Escalation/BusinessAudit → [SupportPage](../frontend/src/production/SupportPage.tsx) | T04 已实现并本机验证：明确动作直接路由、服务器事实与有限员工原文、可追溯引用每次复核、支持明细与处理审计、有效处理人、员工隔离、并发/回滚/恢复。无编号不报成功，pending不等于接单。真实企业身份、外部通知及实际人员接单未验证。 |

真实 Keycloak / 生产全栈、BGE 离线、TLS/恢复探针的已有记录见 [production-acceptance.md](production-acceptance.md)，本轮未重复执行，属于历史证据，不计入本轮新验收。

## 已确认并修复的问题

### HANDOFF-001 · P0 · 无记录时宣称转人工成功

- 场景与修复前：worker 在人工记录落库失败时仍可产生 `handoff` 最终结果，前端只检查状态，显示“已转交人工支持”。用户可能停止求助而后台没有相应记录。
- 预期：有持久化编号显示“人工支持请求已记录”及编号、提交时待处理状态；无编号显示“人工交接未确认”和现有企业支持渠道指引，不能声称已经接单。
- 位置与根因：[ConversationWorkspace.tsx](../frontend/src/production/ConversationWorkspace.tsx) 固定 outcome/提示；[runState.ts](../frontend/src/production/runState.ts) 没有保留 escalation_id。
- 最小修复：新增 [HandoffNotice.tsx](../frontend/src/production/HandoffNotice.tsx)，串联 SSE final/handoff 与 durable result 恢复的编号；graph 的 invalid_citations 答案也改为“需要人工支持”，避免记录前先说“已转人工”。没有代替人员接单确认，也没有自动建正式工单。
- 复现/验收：ConversationWorkspace 的无记录失败测试先失败（handoff-ui-red.log），后通过；runState 恢复记录编号；浏览器有/无编号两例均通过。无记录截图（本地证据：`artifacts/pdf-evidence/handoff-unconfirmed.png`）、有记录截图（本地证据：`artifacts/pdf-evidence/handoff-recorded.png`）。

### PDF-001 · P1 · 表格压平，行列关系丢失

- 场景与修复前：服务等级/分钟/适用范围表被 `page.get_text()` 平铺，关键词仍在，但“紧急→15分钟→业务中断”关系没有结构保证。现有测试只检查关键词。
- 预期：原页单元格→带表头的完整数据行→召回证据→模型上下文→答案/页表行引用逐级一致。
- 位置/根因：[knowledge_parser.py](../backend/app/production/knowledge_parser.py)、[ingest.py](../backend/app/rag/ingest.py) 两个纯文本入口。
- 修复：共享 [pdf.py](../backend/app/rag/pdf.py)，用真实 Table.extract / cells / bbox 读取规则网格，保留页内上下文、表头单位与行值。既有 sections JSONB 保存原单元格预览，不增加表或服务。
- 复现/验收：`test_real_pdf_cells_become_labelled_rows_in_both_ingestion_paths`、完整链 `test_pdf_table_upload_index_answer_and_citation_keep_cell_relationships`；原始、解析、chunk、向量、召回、模型上下文与答案见 chain-evidence.json（本地证据：`artifacts/pdf-evidence/chain-evidence.json`）。

### PDF-002 · P1 · 字符窗口与 240 字摘录切断条件

- 场景：长表跨字符窗口失去表头；完整 chunk 召回后又被 `_relevant_excerpt` 截为 240 字，答案可能看不到后面的数值、单位或条件。
- 位置/根因：chunk_markdown / retriever._to_hit 把表格当普通字符流。
- 修复：每个数据行连同页面上下文独立分块，表格预算 800 字独立于正文窗口；超限明确拒绝而不截断。带 PDF 来源标记的证据完整传给模型；长普通段落重复页码。
- 验收：28 行真实合成表每行保留表头/条件/数值；400 字正文设置下表格行仍完整；超 800 字行拒绝；长于 240 字的引用保持全文与 metadata。对应 unit/test_pdf_structure.py 的四类测试。

### PDF-003 · P1 · 不可靠布局被当作成功解析

- 场景：合并格、扫描页带微小页脚、页边连续结构可能只得到残缺文本，原实现仍可入库。
- 预期与修复：保守支持文字层、单页规则线框与单层唯一表头；不规则 cells、外部表头、表边界文字、页边连续表格等明确失败；加页数、字符、子进程超时保护。无法自动判定的复杂表头/跨页仍需人工核对。
- 位置：[pdf.py](../backend/app/rag/pdf.py)、knowledge._process_claim、knowledge_parser.main。
- 验收：merged/borderless/scan/mixed_scan 真文件均拒绝；扫描上传任务为 failed，发布 409，活动指针不变；既有页数、超时、篡改上传测试仍通过。

### PDF-004 · P1 · 引用打开了当前资料而非引用版本

- 场景：知识更新后，旧答案引用 15 分钟，查看原文却打开 60 分钟的当前来源；无法反查当时证据。
- 根因/位置：document_detail 只返回可变 KnowledgeSource；前端只传 document_id。
- 修复：引用增加 page_number/table_id/row_index/index_revision；前端显示页表行并传 revision；API 返回该不可变 KnowledgeSnapshot，同时检查当前权限/停用与快照原权限。
- 验收：`test_cited_source_opens_its_snapshot_and_still_checks_current_permissions` 实际旧/新正文不同、错 revision 404、提升来源权限后员工 404；浏览器断言请求 revision-cited 并显示引用版本。历史引用与无 metadata 的 Markdown 保持兼容。

### PDF-005 · P1 · 旧平铺 PDF 混入新管线身份

- 场景：新发布任意 Markdown 时，完整索引直接复用其他 active source.content；旧 PDF 没有页码，却继承了新版 extraction/chunking 配置。CLI 相同文件 hash 还会报告 unchanged，不触发重解析。
- 修复：新索引排除缺少逐段 PDF 页出处的来源，返回 excluded_documents；来源正文、记录和旧索引均保留。列表与管理 UI 标注 requires_reparse；相同 PDF hash 可以重新准备解析。旧 ready 平铺任务拒绝发布，重新上传后管理员核对再发布。
- 兼容影响：切换新版后，这些旧 PDF 暂时不能提供回答证据；可逐篇恢复，不需要先删除全部来源。新版 pipeline 增加版本与 legacy policy 身份，旧 revision 不冒认兼容。
- 验收：两份旧 PDF + 新 Markdown 只索引 Markdown，旧内容/status 保留；逐篇更新后只剩一份 excluded；相同 bytes 的 dry-run 显示 would_enqueue，重解析后再次 unchanged。真实 PG/Qdrant 两个新增测试先失败再通过；浏览器显示迁移提示。

### PDF-006 · P1 · 解析器诊断污染 JSON 协议

- 场景：当前 PyMuPDF find_tables 会向 stdout 输出建议，正式子进程 `json.loads(stdout)` 因而失败，上传不能 ready。
- 位置/修复：knowledge_parser.main 在提取期间将诊断 stdout 导向 stderr，只在最终 stdout 输出一个 JSON 对象。
- 验收：实际正式子进程上传规则表成功 ready；保留 pdf-parser-protocol.log 的复现。不是只调用进程内 parser 的假验收。

### PDF-007～010 · 独立逆向审查发现并修复

| ID/优先级 | 场景、根因与影响 | 最小修复/预期 | 可执行验收 |
|---|---|---|---|
| PDF-007 / P1 | 90/180 度页被接受却把数据当表头，270 度误报边界；find_tables 与文本坐标不同 | 仅在内存统一 page.rotation=0，保留原 rotation metadata；表头/行语义不反转 | `test_rotated_page_keeps_original_cell_relationships` 的 90/180/270 三例 |
| PDF-008 / P1 | 同页有规则表时跳过无框检测，无框表中的 999 分钟混入每个规则表上下文 | 在已识别表格之外继续检查连续三行的稳定宽列间空隙；疑似多栏/无框明确拒绝 | `test_borderless_table_beside_a_ruled_table_is_not_trusted_as_context` |
| PDF-009 / P2 | 四幅小图共覆盖约72%，各自小于25%；页脚让扫描页被当作全文 | 按页内图像矩形覆盖并集判断，排除扫描页；限制过多分片 | `test_tiled_images_with_a_footer_do_not_pass_as_text_layer_evidence` |
| PDF-010 / P2 | 正常重复句首段落被 text-table 推断为3列；新列间检查也会误拒常见编号步骤 | 不以猜测列数单独判定；检查实际稳定宽空隙，排除明确编号/项目符号的缩进，后续正文空隙继续核对 | `test_repeated_plain_prose_is_not_mistaken_for_a_table`、`test_plain_numbered_steps_are_not_mistaken_for_borderless_columns` |

首轮六个测试在修复前全部失败（pdf-review-red.xml（本地证据：`artifacts/pdf-review-red.xml`）），编号列表回归另在 pdf-list-red.xml（本地证据：`artifacts/pdf-list-red.xml`） 先失败，修复后全部通过。独立审查另外确认引用快照的两层权限复核，没有确认越权发现；本地桩复核两份legacy+一份重解析PDF+一份Markdown时，只得到2份snapshot、3个chunk/point，召回不包含legacy且原来源保留。审查结论不是“所有 PDF 都可靠”。

## 确认缺口与环境限制

### QUERY-001 · P1 · 多轮工单对象未解析（2026-10-03 已实施并本机验证）

- 场景：历史已查询 IT-2026-0001，用户说“查询我上次那个工单的进度”。
- 修复前：handoff / missing_ticket_number，lookups=[]；只从当前消息提取第一个编号。原始 business-before.json 与 graph-red.xml 保留。
- 预期：唯一明确当前会话对象可重新鉴权查询；多个或无法确定候选先问清。每轮用真实工具查询，不把历史状态当当前状态。
- 位置/根因：graph.lookup_ticket / _extract_ticket_number；原 history 只给知识回答 prompt 使用，没有受控对象记录，旧单词边界还漏掉中文相邻编号。
- 影响/修复建议：打断连续查单并产生无必要升级；先增加受控会话对象解析与澄清状态，禁止从其他会话、其他员工或模型编造对象中取授权。
- 最小修复：纯ticket_lookup解析、owned记录加载；现有run result/final/node_history保存元数据，正式已确认草稿提供编号；每轮工具重新读取User/IdentityAccount/工单。缺号、多候选、畸形编号返回正常澄清；取消限定明确指令；候选按钮发完整编号，无建单/无必要人工副作用。无schema或依赖变化。
- 复现/可执行验收：显式TEST URL下，从backend运行 `.venv/Scripts/python.exe -m pytest -q tests/unit/test_ticket_lookup_context.py tests/production/test_ticket_lookup_persistence.py`，再执行根AGENTS全量检查。证据含59解析/图场景、14实际PG/Redis/worker持久化用例与真实ASGI API，权限降级、停用账号、对象删除、长历史/溢出、超时/DB故障、唯一final与重放；1440/390 Mock HTTP浏览器含缺号、多候选、选择、刷新、换会话。逆向与独立审查的错路由、错对象、畸形编号和“刚才”指代反例已保留RED并修复，相关138项测试通过。详见[实施报告](architecture/ticket-lookup.md)与证据（本地证据：`artifacts/ticket-lookup/implementation-evidence.json`）。企业身份/目标部署不能由本机替代；表内T05仍未实现。

### CREATE-001 · P1 · 未收集必填故障信息（T03 已完成本机软件合同）

- 修复前场景：用户仅输入“创建工单”，直接得到“用户请求 IT 支持”/other/medium/“用户请求 IT 支持。”草稿及确认令牌。原始实际图 RED 和修改前文件保留。
- 预期：先补问问题、影响与已尝试步骤；不了解优先级的依据时询问影响或采用明确的规则，不能伪造描述。
- 位置/根因：graph.collect_ticket_draft 原先直接填默认值。现由 agent/ticket_intake.py、repositories/ticket_intake_context.py、worker/ChatService 和草稿服务共同实现受控字段收集与完整性校验；前端 TicketIntakeNotice/DraftEditor 呈现当前事实和旧草稿补全。
- 影响/最小修复：原缺陷可创建无可处理内容的正式工单；现缺字段不发 token、不写草稿/工单/人工请求，完整后复用原版本确认。旧 pending 不补猜事实，补全后重新签名；控制/纯标点不能冒充故障或尝试记录。
- 复现/验收：原只读 CREATE-001 输入不变，当前 ticket_collection 且 drafts=[]。实际图、真实隔离 PG/Redis worker、ASGI HTTP/SSE、确认编辑/过期/10并发/回滚/未知响应回归通过；1440/390 Mock HTTP 浏览器验证恢复及会话隔离。当前 466 后端、40 前端、26 浏览器通过，0 failures/errors/skipped；见 [T03 报告](architecture/ticket-intake.md) 与实施证据（本地证据：`artifacts/remaining-tasks/ticket-intake/implementation-evidence.json`）。真实身份、模型及目标环境不计已验。

### HANDOFF-002 · P1 · 显式人工意图与必要上下文缺失（T04 已完成本机软件合同）

- 修复前场景：“请转人工”走 knowledge / insufficient_evidence，而非显式人工请求；有资料时可能继续回答。record_handoff 只保存 reason/IDs；支持卡不展示问题、已尝试步骤、证据摘要。实际图与持久化 RED 保留。
- 预期：直接记录明确的人工意图，携带当前员工授权范围内必要问题摘要、已尝试动作与可追溯引用，支持人员能读取并处理；待处理/接单/关闭区分明确。
- 位置/根因：原 graph._classify_intent、business.record_handoff/ESCALATION_FIELDS、business_models.Escalation、SupportPage 缺少明确动作、必要上下文合同和授权读取入口，导致重复问诊和原因不准确。
- 最小修复：agent/handoff.py 直接动作识别；新增 production/handoff_context.py 与可空 JSONB 迁移0005。普通/恢复路径共用有限快照，只取服务器当前事实、最多5条员工原文及5个引用身份；当前来源和原snapshot权限每次重查。支持页可展开明细、读取白名单审计与引用版本；旧行不补猜，仍只有持久编号才显示已记录。
- 复现/验收：原只读 HANDOFF-002 输入不变，现为 explicit_manual_request 且无工具调用。36项实际图、11项真实隔离PG/Redis/worker/HTTP上下文、3项前端及1440/390 Mock HTTP浏览器新增用例验证正反意图、员工范围、当前角色、引用停用/原权限、10并发一次快照/审计、回滚/恢复、分页与处理后重读。最终513后端/43前端/28浏览器通过，0 failures/errors/skipped；详见[T04报告](architecture/handoff-context.md)和证据（本地证据：`artifacts/remaining-tasks/handoff-context/implementation-evidence.json`）。本轮未运行production-live或真实企业身份/模型。

### HANDOFF-003 · P1 · 停用处理人仍可被分派（T04 已修复并本机验证）

- 场景/实际：停用IdentityAccount但保留support角色后，原`_assignee`仍接受该用户，可能把待处理请求交给已不能登录的人员；预期列表与保存都拒绝失效处理人。
- 位置/根因：business._assignee及`/support/assignees`只按User角色检查，未联合当前账号enabled；停用反例在persistence-red.xml先失败。
- 最小修复：分派事务联查并锁定当前User/IdentityAccount，要求enabled且support/admin；列表同样过滤。交接列表、明细和处理重新读取当前操作者角色/账号，保留原状态与版本校验。
- 可执行验收：`tests/production/test_handoff_context.py::test_disabled_support_is_neither_listed_nor_assignable` 与 `test_detail_and_processing_audit_are_owned_and_currently_authorized`；显式全部TEST URL下真实隔离依赖通过并被最终513项回归包含。处理成功仍不宣称外部联系或人员实际接单。

### QUERY-002 · P2 · 最新进展只有状态（T05 已完成本机软件合同）

- 修复前场景/根因：ProductionTicketService.get_ticket_status 的 latest_update 固定为“工单当前状态：status。”，未查询评论/处理审计；员工无法从聊天得知实际进展。legacy仓库还会直接采用无可见性约束的事件summary。
- 预期/最小修复：production/ticket_progress.py从当前可见非空评论与白名单审计选择真实最新记录，保留记录时间和来源；graph显示实际UTC时间，摘要600字并明确省略。无记录时间null，数据库故障仍unavailable。legacy仅本人、明确公开事件或固定创建语义。
- 可执行验收：显式全部TEST URL下运行`tests/production/test_ticket_progress.py`，最终24项用例含公开/内部/未分类/空记录、稳定排序、51条空记录后有效来源、时间、控制字符/长度、并发新查、真实DB故障、ASGI/worker与全部回放入口。原6个只读探针输入不变，未用固定Mock伪造进度。最终537/46/30全量通过，详见[T05报告](architecture/ticket-progress.md)与证据（本地证据：`artifacts/remaining-tasks/ticket-progress/implementation-evidence.json`）。

### QUERY-003 · P1 · 评论权限与历史进度回放缺少来源约束（T05 已修复并本机验证）

- 场景/影响：原评论无公开/内部分类，详情返回全部评论与任意审计；将其加入聊天会让员工读到内部内容及活动时间。支持人员降级、来源改权限/删除/编辑后，旧run/final/消息仍可能按会话所有权回放旧答案。
- 位置/根因：business_models.ProductionTicketComment、business.ticket_detail、production.runs各历史读取入口缺少评论可见性及来源当前/原权限校验。
- 最小修复：0006新增public/internal/unclassified和写入时角色，旧记录保持未知；服务端版本事务限制员工公开补充、支持分类；当前可见审计白名单及可见时间。ticket_lookup保存真实来源和指纹，每次run、events/SSE、会话runs/messages、幂等enqueue/cancel重新校验；不可核验时整段隐藏，原持久结果不改，模型history只保留查询占位语。
- 复现/验收：persistence-red14个失败与legacy-red1个失败保留；`test_original_internal_permission_and_current_visibility_both_restrict_replay`、`test_current_source_restriction_deletion_or_edit_hides_old_answer`、`test_actual_http_replay_and_write_visibility_follow_current_principal`等最终通过，含降级后同key202/同run/redacted与数据库原answer保留。前端/Mock浏览器和真实后端证据分开。

### QUERY-004 · P1 · 等待工单锁后仍沿用旧角色（T05 已修复并本机验证）

- 场景/实际：支持查询在等待工单FOR UPDATE期间降为员工，原先获得锁后仍按旧role读取内部进度；预期获得锁后使用当前角色及工单范围，禁止旧授权继续生效。
- 位置/根因：business.get_ticket_status只在加锁前鉴权。最小修复为锁后重新查询当前enabled/role并复核归属；详情、修改、写评论与分类同步核查当前操作权限。
- 复现/验收：`permission-race-red.xml`1个实际失败后修复；`test_role_demotion_while_waiting_for_ticket_lock_cannot_return_internal_progress`通过。另两项本人工单的内部写入/分类锁等待反例是在修复后新增，无独立修复前RED；最终均验证403且版本、评论数和原分类不变，包含在537项回归。

### QUALITY-001 / OPS-001 · P1 · 真实质量与目标环境未验

- 未提供真实失败 PDF；本轮样本明确为合成测试，不能声称修好了那份企业原件。
- 未使用真实模型或批准的付费服务；controlled provider 从实际证据取值，证明输入链路，不证明真实模型能稳定正确使用证据。确定性 embedding 也不是生产语义质量。
- 合并/多层表头、跨页、无框、多栏、OCR 与未被启发式识别的复杂排版不承诺自动保真。所有 PDF 发布前仍需对照原件核对；可靠解析不等于回答语义正确。
- 既有 evaluation release gate 已区分 Mock 与真实模型，旧84例缺少完整相关集合，不得制造 citation precision；真实样本标注/模型 Recall、引用准确性、幻觉率属于下一阶段。
- 目标 Linux 完整部署、30分钟容量、完整空环境恢复/RPO/RTO、外部备份验证与实际告警接收没有本轮执行证据。相关脚本与手册属于实现/文档，目标验收属于环境阻塞。见 T06/T07。

## PDF 纵向证据与代价

合成原件（本地证据：`artifacts/pdf-evidence/synthetic-response.pdf`） / 原页渲染（本地证据：`artifacts/pdf-evidence/synthetic-response-page.png`） 的表头为服务等级、响应时间（分钟）、适用范围：普通/240/一般问题，紧急/15/业务中断。条件为工作日，注释明确响应时间不等于解决时间。

最终 chain-evidence.json（本地证据：`artifacts/pdf-evidence/chain-evidence.json`） 保存实际原文本、cell preview、两条完整 PG chunks、两个实际 Qdrant 点、候选分值/引用、模型真实接收的受控输入，以及“工作日紧急15分钟，响应不等于解决”的答案和第1页/p1-t1/第2数据行引用。通过后再停用，原 retriever 重新复核来源后返回空结果。

这是工程证据链成功与受控模型接受；报告中的 `real_pdf_quality_verified=false`、`real_model_quality_verified=false` 保持不变。解析与总链路耗时记录在 JSON，仅为小合成文件单次本机耗时，不能作为吞吐/P95。

复用已安装 PyMuPDF，无新增依赖、模型下载、网络上传或 OCR 服务。表格每行复制页面上下文增加字符/向量数量，800字超限会拒绝；全文模型上下文有上界但比240字摘要更大。替换 Docling/Unstructured/OCR 前必须用真实失败样本比较表格结构、来源定位、资源与部署成本；没有证据就不替换。

浏览器页面见 桌面预览（本地证据：`artifacts/pdf-evidence/pdf-preview-1440.png`）、390px预览（本地证据：`artifacts/pdf-evidence/pdf-preview-390.png`）、引用版本原文（本地证据：`artifacts/pdf-evidence/pdf-cited-source.png`）。已人工查看实际截图，无横向溢出，表头/行值可核对。这些 UI 数据来自 Mock HTTP，不能当实际 API 端到端部署证明。

## 复现、数据影响与完成边界

完整命令见根目录 [AGENTS.md](../AGENTS.md) 与 [实施计划](superpowers/plans/2026-10-02-pdf-evidence.md)。只使用 `compose.test.yml` 的 `itops-engineering-test` 本机服务与明确 TEST URLs，测试 fixture 使用独立 schema/collection 并清理各自创建的数据。没有访问真实模型、生产库、生产索引或外部联系人；没有执行部署、目录同步或历史数据自动迁移。

本轮新增/修改限于共享 PDF 提取、分块/引用/快照入口、管理与交接提示、相应测试、只读审查脚本和文档。用户已有正式实现、分支和其他未提交变更均保留。完整成功结果与命令另见 audit-implementation-evidence.json（本地证据：`artifacts/audit-implementation-evidence.json`）。后续工作的顺序、完成定义和需提供的环境证据已写入待办。


## 2026-10-03 发布前最终审查增量

整个剩余软件与319文件候选进行一次独立审查：重点回归137通过，13项新增反例发生实际业务断言失败，归为R1/R2/R3 P1、R4/R5 P2共5项Important。原结论ready: No保留；作者一次RED→GREEN修复及逆向回归，没有第二轮reviewer。场景、实际/预期、代码位置、根因、影响、最小修复和可执行测试见[完整修复记录](verification/final-review.md)。

知识回答与全部实际prompt来源保存身份，历史回放重新检查当前来源及原快照权限，无法核验则隐藏；不让来源型助手原文进入下一轮模型历史。缺ProductionRun关联的legacy/orphan回答保留数据库原文，读视图提示重新查询。交接修改行锁后重查当前enabled/role。信息收集优先识别普通取消并保留引文事实。CI要求执行前收集清单逐例等于XML/JSON实际记录，拒绝套件级错误、部分执行与源码变更。

最终作者实际报告：615后端、46前端、30 demo/production-mock浏览器passed，failures/errors/skipped均0；615唯一收集身份、0 deselected/collection errors。原13反例逐字节复制到新留存目录后作者重跑13passed，原失败证据不覆盖。21评估逻辑、157取消相关和51门禁为重复子集，不累加总数。构建/Ruff/锁/迁移一致性通过，原6只读探针输入保持不变。报告hash与类别见[安全机器摘要](verification/software-validation.json)，原证据和before/current/diff留在本地`artifacts/remaining-tasks/final-fixes/`。

先前611全量中的环境失败报告保留：572passed、4failures、35errors、0skipped；只读核查确认已被外部停止的tmpfs测试服务重启后public schema为空。该报告不计软件通过或产品RED；仅在现有可丢弃测试库upgrade head到0006后重新执行最终全量。没有共享/生产down、recreate、downgrade、清理、模型付费或企业文件上传。T06/T07真实门禁、GitHub精确提交的实际CI/镜像仍须分别执行与记录。
