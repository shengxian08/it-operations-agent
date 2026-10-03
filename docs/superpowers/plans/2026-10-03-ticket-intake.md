# T03 故障信息收集实施计划

> **For agentic workers:** Use superpowers:executing-plans inline. 本任务顺序实现并独立验收，整个剩余任务软件增量在发布前做唯一fresh final review。

**Goal:** CREATE-001不再用默认事实签确认，员工当前会话逐项提供问题、影响、已尝试操作后才能审阅确认。
**Architecture:** 新纯ticket_intake解析器＋owned最近终态loader，复用现有ProductionRun JSON与确认签名事务；前端显示字段及下一问题。
**Tech Stack:** 现有Python/LangGraph/PG/Redis/React/Vitest/Playwright，无新依赖或数据库迁移。
**Spec:** ../specs/2026-10-03-ticket-intake-design.md

## Task 1：纯收集与图

Files: 新backend/app/agent/ticket_intake.py、backend/tests/unit/test_ticket_intake.py；修改agent/state.py、graph.py及必要schema测试fixture。

- [x] 在旧实际graph写失败测试，使用dict传ticket_intake_context，不导入未实现模块造成collection error。第一例：message='创建工单'，断言final_state='ticket_collection'、token工具calls=[]、next_field='problem'。
```python
result = await graph.ainvoke({'user_id':'owner','conversation_id':'conv','message':'创建工单','step_count':0})
assert result['final_state'] == 'ticket_collection'
assert tokens == []
assert result['ticket_intake']['next_field'] == 'problem'
```
Run backend cwd `.venv/Scripts/python.exe -m pytest -q tests/unit/test_ticket_intake.py --junitxml=../artifacts/remaining-tasks/ticket-intake/graph-red.xml`。Expected业务断言失败，0collection errors；RED文件不覆盖。
- [x] 实现owned_intake(value,user_id,conversation_id)、is_intake_followup(message,context)、collect_intake(message,context,user_id,conversation_id)与intake_answer(record)。固定字段/长度/取消规则见spec；图仅ready才issue_confirmation，其余正常END；最终状态白名单增加两项。
- [x] 同文件GREEN及原图回归，label完整信息、自然三轮、未尝试、修订、控制/长字段、取消/换意图/错owner/history均用实际图。旧确认测试输入补实际必填字段，保留确认/错误/预算原断言，并保留before。

## Task 2：服务器持久上下文与实际事务

Files: 新backend/app/repositories/ticket_intake_context.py、tests/production/test_ticket_intake_persistence.py；改production/runs.py、worker.py、services/chat.py、schemas.py、production/business.py与fake接口。

- [x] 写真实fixture失败测试：先enqueue“创建工单”，worker后result为ticket_collection，TicketDraftRecord/ProductionTicket/Escalation计数均0；第二、三、四轮保留实际字段，最后只有pending draft，正式单仍0。读取durable result/SSE，额外客户端history/context422，错owner404。
- [x] load_ticket_intake_context(session,user_id,conversation_id,exclude_run_id,production=True,before=None)读取owned最近终态有效record；RunService.ticket_intake_context验证lease/access/owner；worker/ChatService传字段，final与demo节点持久保存。DB异常不捕获为空。
- [x] 真实签名/编辑/确认RED证明默认旧事实、impact被删除或description与事实不一致、legacy pending都不能发有效签名/正式单；validate_draft_facts检查problem/impact/attempted_steps与version1及description一致，沿用既有draft JSON；旧requires_details只通过员工明确补全编辑重签，不补猜旧行。
- [x] GREEN并执行现有business transactions/run/worker回归，实际验证编辑重签/旧token失效、过期、10并发、rollback、未知结果同key重放只一单。Expected全部passed、0failures/errors/skipped，记录确切四计数。

## Task 3：界面与恢复

Files: 新frontend/src/production/TicketIntakeNotice.tsx；改types.ts、ConversationWorkspace.tsx、runState.test.ts、Workspace.test.tsx与e2e/production-mock.spec.ts。

- [x] 旧Workspace RED：ticket_collection durable result应显示问题/影响/已尝试状态和下一问题，无确认或人工成功；错绑定不显示字段。runState final/reload保留元数据。
- [x] 增加可选类型TicketIntake与summary card，使用原composer继续补充、既有请求/幂等；ready仍原TicketDraftCard。Expected正常刷新/会话隔离，空列表显示明确未尝试，null显示尚未回答。
- [x] npm test/build与1440/390浏览器收集→恢复→完整草稿，检验当前页/非空/overlay/console/溢出及实际请求；保存Mock标签截图。

## Task 4：逆向、报告与独立任务验收

- [x] 按根AGENTS全量真实隔离依赖＋前端/build/e2e＋Ruff/lock；只读四路径探针CREATE-001无token，T04/T05原待办不伪装完成。
- [x] 场景/实际预期/位置/根因/影响/P1/最小修复/验收写入docs/architecture/ticket-intake.md、audit/backlog及证据；before/current hash与所有RED保留。T03通过后继续T04，整个目标保持active直至所有剩余任务和GitHub交付完成。

## Review Focus

缺字段或控制回答不能签token；未尝试与未回答区分；字段超长不截断通过；来源为owned服务器终态，错用户/会话、换意图/取消、读取故障不能借旧字段；无模型补猜事实；签名仅完整当前摘要，编辑/过期/并发/rollback/未知重放只一单；前端摘要与服务器事实一致，刷新和会话隔离；既有知识/查单/人工和run租约、幂等、final事务不回归。
