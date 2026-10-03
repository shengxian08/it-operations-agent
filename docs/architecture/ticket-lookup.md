# T02 多轮查单与澄清

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../verification/README.md)。

本任务修复 QUERY-001：员工在当前会话已明确查询或确认创建工单后，可以继续问“上一张工单现在怎么样”“进度呢”；对象不明确时先澄清。每次真正查单都由工具读取当前权限和当前状态。当前源码、验证计数和独立审查见 实施证据（本地证据：`artifacts/ticket-lookup/implementation-evidence.json`）。

## 缺陷与最小修复

| 项目 | 可复核记录 |
|---|---|
| 场景 | 当前会话已查询 IT-2026-0001，再说“查询我上次那个工单的进度” |
| 原实际 / 预期 | 原图 missing_ticket_number/handoff，工具调用为空；预期唯一受控对象重新查询，缺号/多对象先澄清 |
| 位置 / 根因 | agent/graph.py 只从当前消息取第一个编号；history 只有模型文本，没有独立对象状态；旧正则的单词边界漏掉中文相邻编号 |
| 影响 / 优先级 | 连续查单中断，多候选可能被猜第一项，缺号触发不必要人工记录；P1 |
| 最小实现 | 纯对象解析、绑定当前用户/会话的数据库记录加载、查单元数据持久化、当前账号/角色检查、候选选择界面；无新表、迁移或依赖 |
| 初始复现 | graph-red.xml 26 failures/3 passed；persistence-red.xml 10 failures/0 errors；frontend-red.xml 2 failures/4 passed |
| 可执行验收 | 根 AGENTS 的显式测试 URL；backend cwd 跑 test_ticket_lookup_context.py、test_ticket_lookup_persistence.py；frontend npm test/build，Playwright demo/production-mock；本次最终报告链接如下 |

## 对象来源与当前鉴权

`app/agent/ticket_lookup.py` 只选择编号，不授予任何访问权。当前消息中的一个完整编号优先，去重并统一大小写；支持中文相邻书写和序号 10000 以上。两个以上编号先展示候选。存在不完整、带 ASCII 后缀或过长编号时要求补充完整编号，不回退到旧工单。不通过相似匹配修补编号。

`app/repositories/ticket_context.py` 验证当前 Conversation.user_id，从当前用户、当前会话的终态运行记录加载服务器产生的 ticket_lookup 元数据。正式已 confirmed 的草稿记录可以提供创建后的编号；pending 草稿不能。旧服务器 ticket_status 记录只使用关联原始 user 消息的明确编号，不解析 assistant 答案，不回写或补造旧元数据。演示路径从 AgentRun 实际 lookup_ticket 节点及成功创建的 ToolAudit 取受控对象。

查询上下文与供知识模型使用的最近 20 条 history 分开。较早对象仍需考虑，避免历史裁剪后误认为唯一候选。历史指代核对当前会话全部已知对象；序号才使用最近一次澄清的显示顺序。待选择子集不能让多对象误变唯一，空补号提示不抹除之后明确的历史指代。候选最多保留 20 个；发现第 21 个或已有溢出澄清记录时，要求完整编号，不使用截断后的唯一项或序号选择。读取错误向上传递，不能当作“缺号”。

Production worker 通过有效租约加载上下文，并检查当前账号访问状态和会话 owner。图每轮仅对最终选定编号调用一次 get_ticket_status。ProductionTicketService 再读取当前 User.access_level、IdentityAccount.enabled 和工单记录；员工限制本人范围，support/admin 按当前角色读取。账号停用/缺失是工具不可用；无可见对象是 not_found；超时或数据库异常是 unavailable。历史查询成功不能代替本次权限检查。工具返回的编号必须与所选编号相同。

## 澄清、取消和恢复

| 情况 | 结果与行为 |
|---|---|
| 当前明确一个完整编号 | 重新查询该编号，basis=explicit |
| 明确历史指代或近期短追问，且当前会话受控对象唯一 | 重新查询，basis=conversation |
| 没有明确历史指代的新查问 / “另一张工单” | 先确认编号或要求补号，不能用旧唯一对象替代新对象 |
| 缺号 | ticket_lookup_clarification，正常 completed，不发 token、不建单、不创建人工请求 |
| 多候选 | 展示完整编号；可回“第2单”或点击编号；序号仅对应最近一次持久化澄清顺序 |
| 越界序号 | 保留澄清，不尝试任意候选 |
| “算了，不查了” / “取消查询工单” | ticket_lookup_cancelled；完整取消指令才取消，普通状态问题中的“取消查询”不当作命令 |
| 换为知识/建单意图 | 最新非查单终态关闭序号/短追问；历史对象仍只供明确查单指代使用 |
| 换会话/换用户 | 不借用旧会话或其他用户对象；HTTP 越权仍返回 404 |
| 工具错误 | 保留 ticket_lookup_failed 失败/人工兜底；只有持久化 escalation_id 才能宣称记录成功 |

正式路径元数据随现有 ProductionRun.result 与唯一 final 事件一起保存。HTTP 202 的同幂等 key 重放不重复创建运行；SSE/刷新恢复保持相同候选与顺序。演示 ChatService 将实际节点元数据保存到 node_history 并返回 final。客户端不能传入 history 或 ticket_context，RunCreate 的 extra=forbid 返回 422。

`TicketLookupNotice` 只展示查单所需的编号和输入提示。候选按钮提交完整编号到现有 startRun 流程，保留 CSRF、会话和幂等处理；不会改动输入框里另一个尚未发送的问题。当前用户/会话绑定不匹配时不展示候选。进行中、未知受理结果、加载或已归档时禁止再次提交。未确定对象不展示人工成功或创建工单提示。

## 证据与边界

- 后端全量 XML（本地证据：`artifacts/ticket-lookup/backend-final.xml`）、日志（本地证据：`artifacts/ticket-lookup/backend-final.log`）：实际本机测试，包含隔离 PG/Redis/Qdrant 与受控模型场景；读取 passed/failures/errors/skipped。
- 真实 HTTP→PG→worker→当前工具→final/SSE（本地证据：`artifacts/ticket-lookup/actual-api-ticket-lookup.json`）：真实 disposable PG/Redis、真实图与工单服务；ASGI Principal 受控，无企业 OIDC、模型/embedding 调用。第二轮在 DB 状态改为 resolved 后重新读取；创建工单数保持 1，人工记录与 legacy run 均为 0。
- 前端 XML（本地证据：`artifacts/ticket-lookup/frontend-final.xml`）、构建（本地证据：`artifacts/ticket-lookup/frontend-build.log`）、浏览器 XML（本地证据：`artifacts/ticket-lookup/browser-final.xml`）：真实 Chromium 渲染，HTTP 为 Mock。1440/390 两候选恢复、选择、切会话和缺号；检查页标识、非空、无 overlay/相关 console 错误、无横向溢出、请求精确编号。
- 只读四路径探针（本地证据：`artifacts/ticket-lookup/business-after.json`）：受控对象唯一时查单；仅任意 history 或两候选时澄清。CREATE-001/HANDOFF-002 仍保留原缺失证据；空知识 probe 仅证明缺证据兜底，不证明知识质量。
- 实施 ledger（本地证据：`artifacts/ticket-lookup/ledger.md`）、before 字节与 hash（本地证据：`artifacts/ticket-lookup/before-manifest.json`）、精确增量（本地证据：`artifacts/ticket-lookup/review.diff`）：保留既有未提交实现和失败证据，不基于 HEAD 覆盖，不混合提交。

逆向检查另发现并修复：畸形编号误选旧对象/普通状态问句误取消（number-guard-red.xml 7 failures）；双会话侧栏 min-width:auto 在 390px 撑至 410px（browser-overflow-red.log 与截图）。后者只加 min-width:0，保留原失败断言与截图。

唯一独立审查（本地证据：`artifacts/ticket-lookup/final-review.md`）还复现四类路由/选择问题；作者按一次TDD pass修复并留处理记录（本地证据：`artifacts/ticket-lookup/review-fixes.md`）。“查询已提交工单”只查不发token；另一张/未指明对象不借旧对象；bare IT-/左侧ASCII粘连/非ASCII数字不查询；“刚才的工单”按唯一性与当前范围处理。review-red.xml 15 failures、列表边界2 failures均保留，修复后相关138测试通过。审查者没有独立重跑最终依赖/浏览器套件，不能把作者验收冒认独立重验。

| 审查发现 / 优先级 | 场景、实际 / 预期、根因与影响 | 最小修复 / 可执行验收 |
|---|---|---|
| R1 / Important，P1 | “查询已提交工单 IT-2026-0001 的进度”原被创建子串优先路由成建单，发token/草稿；预期只查；graph._classify_intent优先级错误会产生无必要确认 | 明确编号/查询优先；test_querying_a_submitted_or_created_ticket_never_issues_a_new_draft及真实test_review_query_route_creates_no_draft_and_a_real_new_intent_closes_pending；保留新建单意图 |
| R2 / Important，P1 | 只有旧候选时问“另一张工单”或普通新查询原自动取旧对象；pending子集还可能掩盖其他历史对象；预期补号/确认，避免答错对象；resolve_ticket无条件复用唯一值 | 唯一性用global，自动选择还需明确指代/近期短追问；ordinal才用pending；unit新对象/子集/空提示/序号反例及真实持久follow-up |
| R3 / Important，P1 | bare IT-、XIT-2026-0001或非ASCII数字原漏拒绝并回退旧对象；预期明确补号，避免错对象；合法编号提取与畸形提示检测边界不一致 | 严格ASCII完整号与独立疑似号检查，不修补；test_partial_prefix_glued_prefix_or_unicode_digits_do_not_select_old_or_prefix_object；IT-Operations知识主题正例保持 |
| R4 / Important，P1 | “刚才的工单现在怎么样”原落入知识/人工；预期按受控对象唯一性查询或澄清；lookup_followup遗漏合同内“刚才”会产生无必要检索/交接 | 补确定性历史指代；test_just_now_reference_obeys_uniqueness_and_scope覆盖唯一/多/无/异owner/异conversation；0模型/建单/无必要交接 |

作者修复后重新运行完整检查：419后端、36前端、24浏览器通过，全部0 failures/errors/skipped，构建/Ruff/锁检查exit0。原被审查版本的hash清单（本地证据：`artifacts/ticket-lookup/reviewed-implementation-evidence.json`）与增量（本地证据：`artifacts/ticket-lookup/reviewed-increment.diff`）单独保留；逐项完成审计（本地证据：`artifacts/ticket-lookup/completion-audit.md`）核对的是当前修复源码。

支持确定性的中文查单、当前会话对象指代、近期查询的有限短追问和明确序号；没有增加模型驱动的通用意图/实体推断。聊天最新可见评论/审计摘要属于 T05，必填故障收集属于 T03，显式人工意图/摘要属于 T04。本机软件测试不代替真实企业身份、目标 Linux、容量/恢复/外部告警或真实文档/模型质量验收。
