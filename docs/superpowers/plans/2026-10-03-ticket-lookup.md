# T02 多轮查单与澄清 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline；唯一最终fresh-context reviewer，不为每个步骤派agent。Steps use checkbox syntax。

**Goal:** 当前会话明确工单对象可重新鉴权查询，缺号/多候选先澄清，完整持久化/API/界面恢复验收。
**Architecture:** 纯ticket lookup resolver＋owned DB context loader；现有结果JSON存储确定性选择元数据，原工具每次读取当前权限与状态。
**Tech Stack:** 现有Python/LangGraph/FastAPI/SQLAlchemy/PG/Redis/React/Vitest/Playwright，无新增依赖或schema。
**Spec:** ../specs/2026-10-03-ticket-lookup-design.md

## Task 1：纯选择与graph（RED→GREEN）

Files: 新app/agent/ticket_lookup.py、tests/unit/test_ticket_lookup_context.py；改agent/state.py、graph.py。

- [x] 首先以实际旧graph写失败测试，直接传绑定上下文的dict，不导入不存在的模块造成collection error：

```python
state = {'user_id':'owner','conversation_id':'conv','message':'上次那个工单进度呢','step_count':0,
         'ticket_context':{'user_id':'owner','conversation_id':'conv','candidates':['IT-2026-0001'],
                           'pending_candidates':None,'recent_lookup':True,'overflow':False}}
result = await graph.ainvoke(state)
assert ticket_service.calls == [('owner','IT-2026-0001')]
assert result['final_state'] == 'ticket_status'
```

Run backend cwd: `.venv/Scripts/python.exe -m pytest tests/unit/test_ticket_lookup_context.py -q --junitxml=../artifacts/ticket-lookup/graph-red.xml`。Expected actual assertions fail，0 collection errors。

- [x] 实现 `extract_ticket_numbers(message)`、`lookup_followup(message, context, user_id, conversation_id)`、`resolve_ticket(message, context, user_id, conversation_id)`、`lookup_metadata(...)`；context字段严格绑定并校验，上限20；解析完整ID/指代/有限明确序号；结果为选定一个编号或明确missing/ambiguous/overflow。Graph lookup在选择不足时返回ticket_lookup_clarification，不handoff；工具错误仍独立failed路径；增加final-state验证。
- [x] 同命令GREEN并回归test_agent_graph/test_production_graph/model_limits；验证历史文本无授权、异常非not_found、当前两个号不猜、自然追问/取消/换意图。

## Task 2：持久上下文、当前鉴权与真实依赖（RED→GREEN）

Files: 新app/repositories/ticket_context.py、tests/production/test_ticket_lookup_persistence.py；改runs.py、worker.py、chat.py、business.py；必要的两个旧test fixture补真实接口/IdentityAccount，不改预期答案。

- [x] 写实际PG测试：先一个真实工单明确查，再更新DB状态并说上一单，必须调用工具重新查；当前两个号/缺号不得查/升级。新run从真实owned记录加载context，跨user/conversation不串；client history/context不进入图；权限收紧/停用/删除/超时分开。先跑RED保存XML。
- [x] 实现 `load_ticket_context(session,user_id,conversation_id,exclude_run_id,production=True)`；owner检查，stream terminal记录，不读取assistant文本；canonical元数据验证来源绑定，legacy明确user请求仅在server ticket_status执行记录内取；confirmed草稿补受控对象。读取失败抛错。RunService.ticket_lookup_context(run_id,lease_token)验证lease/current access。Worker传参数，ChatService把字段输出/保存node_history和legacy上下文；ProductionTicketService检查当前User和enabled IdentityAccount。
- [x] 同命令GREEN加runs/worker/business/chat-api相关回归，显式TEST URL和现有3个compose.test服务，不重建/清理共享资源。保存一条实际API→PG→tool→final事件多轮证据JSON。

## Task 3：澄清UI与恢复（RED→GREEN）

Files: 新production/TicketLookupNotice.tsx及测试；改types.ts、ConversationWorkspace.tsx、runState.test.ts、production.css、e2e/production-mock.spec.ts。

- [x] 写失败测试验证两候选只编号、缺号提示、选编号回调、没有handoff/建单提示；durable final恢复保留ticket_lookup。Expected旧界面缺明确澄清/选择。
- [x] Final类型增加可选metadata；新增result label与TicketLookupNotice。send函数添加可选明确内容参数，候选按钮走同一请求/幂等/会话流程，查询当前权限。页面仅显示用户需要的信息，不输出resolver/DB实现细节。
- [x] npm test/build以及demo、production-mock浏览器项目，1440/390截图核对实际文本与候选选择请求、刷新恢复/换会话隔离；Mock标注。不要降低测试期待。

## Task 4：全量、报告和唯一独立审查

Files: docs/project-audit.md、improvement-backlog.md，新docs/architecture/ticket-lookup.md、artifacts/ticket-lookup/implementation-evidence.json，扩展只读audit_business_paths.py。

- [x] 按AGENTS显式TEST URL运行全量pytest、npm test/build/适用e2e、ruff/lock；四业务只读探针显示QUERY-001受控上下文修复与不信任任意history，其他待办不混入。
- [x] before/current增量生成review.diff，给唯一fresh reviewer spec/plan/ledger/Review Focus。重要发现先实际失败测试，一次修复并全量GREEN，无二次review；保留原证据与用户已有修改，不混合提交。
- [x] 缺陷记录场景/实际预期/位置/复现/根因/影响/优先级/最小修复/可执行验收；completion audit逐项核对。完成T02软件合同与真实企业身份/目标环境区分，不宣称T03/T04/T05完成。

完成证据：artifacts/ticket-lookup/completion-audit.md。最终419后端/36前端/24浏览器，0 failures/errors/skipped，构建/Ruff/uv lock通过；独立原始审查及作者一次修复验收分别保留，未合并、提交或部署。

## Review Focus

多候选不猜第一项，候选截断不伪装唯一；context绑定current owner/conversation且不解析assistant文本；旧明确请求不得推断不明确对象；完整ID边界/重复/中文相邻/10000号；pending序号只对应持久显示顺序，换意图/取消/换会话无串；状态/角色/disabled身份每次重查，异常不变not_found；context加载失败不伪装缺号；取消/租约和幂等final只能一次；确认建单候选不得使用未确认草稿；UI选择、SSE恢复与当前会话绑定，无无必要人工交接和无建单副作用；Mock/PG/API/浏览器证据标签准确。
