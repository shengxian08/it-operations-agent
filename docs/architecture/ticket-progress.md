# 最新可见工单进度（T05）

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../verification/README.md)。

2026-10-03，当前工作区的作者本机软件合同验收。依据[已授权设计](../superpowers/specs/2026-10-03-ticket-progress-design.md)，T05 已完成进度来源、评论可见性与历史回放过滤。最终 **537 后端、46 前端、30 浏览器 passed，failures/errors/skipped 均为 0**，构建、Ruff、锁文件及迁移一致性检查通过。冻结源码、20 份修改前 hash、RED/逆向用例与最终报告见本地实施证据（本地证据：`artifacts/remaining-tasks/ticket-progress/implementation-evidence.json`）。整个剩余增量的独立最终审查与 GitHub 交付尚未完成，真实企业身份、真实模型和目标环境仍属于 T06/T07。

## 问题、根因与修复范围

QUERY-002（P2）：员工问“进度呢”，原 `ProductionTicketService.get_ticket_status` 只返回“工单当前状态：status。”，没有最近处理记录与记录时间。工单页面虽然有评论与审计，聊天查询无法说明具体进展。原 legacy `TicketRepository` 还会把任意事件的自由 `summary` 当作进度，没有可见性约束。现在由实际数据库记录确定摘要与时间，不调用模型生成进度。

评论权限与回放属于本任务同时需要处理的 P1 问题：原 `ProductionTicketComment` 没有可见性，详情返回全部评论及自由审计 JSON；若简单把这些内容加入查单回答，内部备注和处理时间会向员工泄露。历史 run、事件、消息与模型历史原来主要依赖会话所有权，无法应对支持人员降级、来源改为内部、来源删除或内容修改。修复为明确评论分类、白名单审计、带来源身份的回答和每次回放的当前权限复核。

并发逆向检查另复现一个实际授权缺陷：支持查询先读取角色，等待工单行锁期间账号降级，获得锁后仍按等待前的角色返回内部记录。`permission-race-red.xml` 的真实依赖用例先失败。当前工具在获得工单锁后重新读取账号角色并检查归属，详情、状态更新、评论写入及分类也在锁后重新检查相应权限；查询反例先进入 22 项进度回归。随后新增两项“锁等待期间降级、工单仍属于本人”的内部写入与分类反例，要求403且评论、原分类、工单版本均无变化；这两项是在修复后新增，没有单独修复前RED。最终537项后端报告含24项`test_ticket_progress`用例，全部通过。该证据不等于证明任意并发时序均已覆盖。

主要位置为 `production/ticket_progress.py`、`production/business.py`、`production/runs.py`、`repositories/tickets.py`、`tickets/progress.py` 与 `agent/graph.py`；界面变更集中在 `TicketDetail.tsx` 及 API/类型。T02 的受控工单对象、T03 的当前版本确认和 T04 的人工交接保持各自合同，本任务没有将查单失败变成自动建单。

## 评论分类与写入事务

迁移文件 `0006_ticket_comment_visibility.py`（revision `0006_comment_visibility`）增加 `visibility`、`author_role`、合法分类约束及按 ticket/time/id 的查询索引。已有备注默认为 `unclassified`，作者原角色为未知，不补猜公开权限或历史身份。本阶段仅在可丢弃测试库执行 `0005_handoff_context → 0006_comment_visibility`，没有生产迁移或 downgrade。

新评论只能是 `public` 或 `internal`，写入时保存服务器读取的当前作者角色。员工只能补充自己工单的公开信息；当前有效 support/admin 可写公开回复或内部备注，也可在核对后通过分类接口将旧未分类记录设为公开或内部。分类不重写作者角色。未知分类、将新评论直接写为 unclassified、空内容、超长内容均拒绝。

`POST /api/v1/tickets/{number}/comments` 与 `PATCH /api/v1/tickets/{number}/comments/{comment_id}` 复用工单版本事务。所有权、当前角色/账号、版本和评论所属工单均在服务端核查；评论、版本变化及审计共同提交或回滚。6 个同版本并发写入只提交一个，其余为 409；审计落库故障不会留下评论或递增后的版本，随后可以重新操作。请求结果未知时界面提示刷新核对，不声称已经保存。

## 最近记录与可见时间

`latest_record` 分别按 `(created_at, id)` 倒序读取评论与 `created/updated` 审计，采用每批 50 条的游标读取，跳过空内容和不能安全解释的记录后继续查找。员工评论候选只含 public；支持人员可见 internal 与 unclassified，摘要类型明确区分支持回复、员工补充、公开回复、内部备注及未分类历史备注。最终按实际记录时间、稳定来源 ID 和来源类型确定最近一条。

审计摘要只接受固定“工单已创建”语义，或合法状态变化、分派/取消分派变化；自由 JSON、未知事件、未知状态、私有字段不能成为公开进度。详情中的 commented 审计必须关联当前可见评论；内部评论及其审计时间不会出现在员工详情。员工列表和详情的更新时间使用可见记录时间，最低为工单创建时间，避免把内部备注触发的通用 `updated_at` 暴露为公开活动。

聊天中的处理时间来自选中记录的 `created_at`，以带 `UTC` 标记的时间呈现，不用工单通用更新时间代替。无可见记录时返回“暂无可见处理记录。”且进度时间为 `null`；这与工单不存在、无访问权或数据库故障不同。真实评论查询故障仍产生 `unavailable` 并按既有人工失败合同处理，不改报为无记录或查无结果。

`clean_summary` 清除控制字符、归一化空白，最多保留 600 字并加明确的“后续内容请查看工单详情”提示。此处是工单记录摘要，完整评论保留在授权详情中。返回数据携带来源类型、ID、原可见性及按原内容/时间/类型生成的指纹；历史回答不凭当前标题、模型文字或猜测事实获得授权。

## 历史回答与所有回放入口

`source_readable` 联合核查当前 enabled 账号角色、当前工单归属、来源存在、来源当前可见性、回答快照原可见性和原内容指纹。原内部回答即使其评论后来公开，也不会因此变成员工可读的旧快照；当前已公开内容可以通过一次新的真实查询读取。来源删除、编辑或转为不可见后，旧回答整段替换为“这条历史工单回答的来源当前无法核验或已不可访问，请重新查询工单。”，不只删一个摘要字段。原持久化结果不覆盖，只生成当前读取视图。

| 入口 | 当前实现与返回边界 | 当前验收证据 |
|---|---|---|
| `get_run` / `GET /runs/{id}` | `_read_payload` 过滤 result，`_read_events` 同时过滤内嵌 final 事件；保留运行/终态必要标识 | 角色降级、原内部权限、来源改权限/删除/编辑；实际 ASGI GET |
| `events` / `GET /runs/{id}/events` | 每次读取 final 经同一来源校验；SSE 循环重新取得当前服务器会话身份后读取事件 | 实际 HTTP/SSE 在降级后不返回内部标记，仍只出现一个 final |
| `conversation_runs` / 会话运行列表 | 当前会话所有权及账号检查后，每个 result 经 `_read_payload` | 原内部权限反例与实际 ASGI 列表读取 |
| `messages` / 会话消息列表 | 按 `result_message_id` 关联运行结果，隐藏对应旧回答并清空其引用 | 角色降级、来源失效与实际 ASGI 消息读取；不是直接返回旧 assistant 原文 |
| 幂等 `enqueue` 重试 | 同 key 命中已有 run 后返回 `_read_payload`，不能从原 `run_payload` 旁路重放内容 | 实际 HTTP 用例验证降级后同 Idempotency-Key POST：202、同 run、redacted，同时断言数据库原 answer 保留；最终全量通过 |
| `cancel` 的终态回放 | 所属 run 加锁，最终返回 `_read_payload`，包括已经结束的旧查询 | 当前实际 HTTP测试内另有降级后的 cancel 服务断言 |
| 下一轮模型 `history` | 当前账号有效性核查；可关联为 `ticket_status` 的旧回答一律替换为固定“查询已完成，再次查单需重调工具”占位语 | 当前模型历史测试确认内部工具答案不进入后续历史，无真实模型调用 |

无 `progress_source` 的旧 `ticket_status` 结果按无法核验处理；`not_found` 结果不含可访问工单正文，保持原查无回复。消息与模型历史通过服务器保存的运行关联识别查单回答，缺少运行关联的历史消息是否符合既有数据约束须在最终审查核对，不能仅由上述关联路径测试推定全部旧数据已验。

## Demo、界面与可执行验收

Demo/legacy 查询始终限于本人。它只读取明确 public 的已知事件类型，或不带可见性的 created 事件所对应的固定创建语义；未知事件、内部事件及旧任意自由 summary 不当作公开进度。真实事件时间随结果返回，缺记录仍明确为空。

支持界面提供公开/内部选择、历史未分类提示及“核对后公开”操作；员工只提供公开补充。保存或分类后重新读取持久详情与版本，刷新失败清除旧详情；401/403/404 后不继续展示原敏感详情。1440/390 Mock HTTP 用例分别检查支持分类、内部写入与刷新恢复，以及员工只见公开记录。这些接口拦截数据不代表真实身份或实际后端部署。

按根 `AGENTS.md` 设置全部隔离 TEST URL，从 backend 执行 `.venv/Scripts/python.exe -m pytest -q tests/production/test_ticket_progress.py`，并执行相关查单/运行/事务回归与最终后端全量、Ruff、锁文件检查。重点用例包括公开/内部/未分类/空记录、同时间排序、超过50条空记录后的真实来源、600字与控制字符、真实时间、当前账号与工单权限、来源权限/删除/编辑、锁等待期间降级、所有回放入口、并发提交、审计回滚以及数据库故障分类。前端执行 `npm test`、`npm run build` 和 demo/production-mock 浏览器验收。

本阶段发现默认 4173 被另一个工作区服务占用，原服务被保留；本任务使用本地临时 Playwright 配置，将 demo/production-mock 分别放在 4175/4176，设置 `strictPort` 且不复用已有服务。历史浏览器测试硬编码 4174 的地址断言已改为比较当前配置 baseURL 的 origin，继续验证当前应用地址。此配置属于本地隔离证据，未改写另一工作区或把其页面作为本项目结果。

## 阶段与最终验收证据

以下为实际解析 JUnit testcase 所得，除列出的失败外，errors/skipped 均为 0；报告保存在本地 `artifacts/remaining-tasks/ticket-progress`，没有将原始诊断上传公开仓库。

| 报告 | 实际计数 | 分类与说明 |
|---|---|---|
| `persistence-red.xml` | 14 tests / 14 failures | 原进度、分类、来源和回放合同的实际 RED |
| `persistence-green.xml` | 52 tests / 51 passed / 1 failure | 作者新请求误用降级前旧 principal，worker 按原访问快照拒绝；旧 principal 回放反例保留，新请求改用当前 principal |
| `persistence-current.xml` | 69 passed | 当时真实隔离依赖及相关回归通过，不替代后续最终全量 |
| `legacy-red.xml` | 1 failure | 原 legacy 自由事件摘要与公开边界缺口 |
| `progress-reverse.xml` | 21 passed | 分类、并发、错误、长内容和 HTTP 回放逆向覆盖 |
| `permission-race-red.xml` | 1 failure | 锁等待期间降级仍沿用旧角色，属于实际产品缺陷 |
| `progress-current.xml` | 22 passed | 修复上述锁后角色复核的阶段回归 |
| `frontend-red.xml` | 3 failures | 新可见性、旧分类及刷新清除合同 RED |
| `frontend-green.xml` | 3 tests / 2 passed / 1 failure | 作者文本定位同时匹配 option 与 strong；改为准确的可见标签定位，业务断言保留 |
| `frontend-current.xml` | 3 passed | 对应三个新增用例通过 |
| `frontend-all.xml` | 46 passed | 已读取前端全量报告；构建通过 |
| `browser-current.xml` | 30 tests / 24 passed / 6 failures | 历史测试硬编码4174，与隔离端口配置冲突；该报告不能记作全量通过 |
| `browser-final.xml` | 30 passed | 修复地址断言后demo/production-mock全部通过，failures/errors/skipped均0 |
| `backend-all.xml` | 535 tests / 531 passed / 4 failures | 首轮后端全量尚有兼容问题，不能记作通过 |
| `compatibility-current.xml` | 20 passed | 修复旧Demo响应类型及停用账号验收读取方式后的相关回归 |
| `backend-final.xml` | 537 passed | 最终后端全量，含24项真实隔离PG/Redis/ASGI/worker进度用例，0 failures/errors/skipped |
| `frontend-final.xml` | 46 passed | 最终前端全量，0 failures/errors/skipped |
| `browser-verified.xml` | 30 passed | 最终CSS标签分行与间距修改后重跑，demo/production-mock均通过，0 failures/errors/skipped |

首轮后端4个失败分别是旧Demo GET的dict响应类型不接受新增datetime，以及3处旧停用账号测试仍通过现已正确返回403的服务入口读取终态。Demo现改用`TicketStatusResult`序列化；停用用例新增403断言，另从隔离schema读取持久终态，同时保留原failed/error/claim和并发无死锁断言。20项兼容回归已通过；这些修正不删除账号停用的安全预期，也不降低业务质量阈值。

`migration.log` 记录测试库 0005→0006；最终 `migration-check.log` 为 `No new upgrade operations detected`。`build-final.log`、`ruff-final.log`、`lock-final.log` 分别确认构建、Ruff 与锁检查通过。最终 `browser-mock-verified/ticket-progress-support-390.png` 与 `ticket-progress-employee-1440.png` 已人工查看，分行标签、下拉间距及正文可读、无横向溢出；修改CSS前的支持/员工1440/390四个视图也分别查看，图像均为Mock HTTP。

原六个只读业务探针输入保持不变：CREATE-001 仍为信息收集，HANDOFF-002 为显式人工原因。QUERY-002 的真实记录和时间由24项实际依赖进度用例验证，没有让固定Mock探针返回伪造进度充当证明。`production-live.spec.ts` 已增加公开/内部回复、员工只见公开记录及聊天实际时间断言，本阶段没有在目标环境执行；其执行结果需由后续CI/目标验收实际取得。

20份before文件已逐一验证hash；25份产品/测试文件与3份任务文档明确纳入T05源码快照，审查/待办/整体计划/账本另存收尾快照。最终报告与来源hash独立保存，不使用后续发布准备中的文件替换已验T05状态。这是作者本机验收，整个剩余增量的唯一独立最终审查、T06/T07 和 GitHub 实际交付仍保持独立验收，全目标继续进行。
