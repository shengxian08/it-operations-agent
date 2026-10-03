# 显式人工请求与持久上下文（T04）

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../verification/README.md)。

2026-10-03，当前工作区的本机软件合同验收。员工明确请求人工支持后，系统记录可追溯的请求和有限故障上下文，支持人员可读取并更新处理状态。只有取得持久化编号，界面才显示“人工支持请求已记录”；`pending` 仍表示待处理。真实企业身份、实际人员接单和外部通知没有本阶段验收证据。

## 已复现的问题与修复

HANDOFF-002（P1）：原“请转人工”被分类为知识问答，空检索时仅以 `insufficient_evidence` 兜底；有高分资料时还可能继续回答。交接表原来只保存原因和会话/run 编号，支持工作台无法核对故障事实、尝试操作或引用。根因是图没有明确人工意图，持久记录没有上下文合同和明细读取接口，影响员工意愿执行和后续问诊效率。

现在 `agent/handoff.py` 识别明确人工动作，图以 `manual_handoff` 直接进入 `explicit_manual_request`，不调用检索、模型、查单或签名工具；原安全检查和步数预算继续优先。代码、引文、制度咨询和否定请求不触发该动作。逆向检查补充“请转人工帮我处理VPN故障”等带故障描述的动作，以及“请转人工，暂时不要转人工”的后续取消。该确定性识别的已验边界由 36 个实际图用例说明，不宣称覆盖所有自然语言表达。

HANDOFF-003（P1）：原分派校验只判断用户角色，停用的 support 仍可被选为处理人，实际测试先出现 `DID NOT RAISE HTTPException`。`business._assignee` 现在在同一事务中读取并锁定当前用户与启用账号，必须是有效 support/admin；处理人列表同样过滤停用账号。列表、明细和处理动作都重新读取当前账号与角色，不能用旧登录角色继续读取或修改。

## 快照、引用与事务合同

`production/handoff_context.py` 在当前员工拥有的会话中构建 `schema_version=1` 快照。问题、影响和已尝试操作只取最近一轮有效服务器收集状态，或该轮当前数据库草稿的事实与版本；取消、失败、其他意图后不借用更早故障字段。未知字段为 `null`，明确未尝试为空列表，并保存真实来源 run/draft/version。直接人工请求中的自然语言仍在员工原文中呈现，不推断独立结构化事实。

快照最多包含最近 5 条员工原文及真实 message ID，每条最多 2000 字。超长消息只保存编号和明确省略原因，较早消息未纳入也有标记；原始消息保留。快照不复制 assistant 回答，不接收客户端上下文或任意聊天日志。

引用从服务器消息的实际引用身份取得，最多 5 个，保存 document/revision/chunk 与来源 message ID。持久快照不保存可永久回放的标题或摘录。保存时核查员工当前访问权限；每次明细读取再按当前读取者核查来源 active/权限、原始 snapshot 权限及 chunk 权限。界面标题、出处和内容来自实际 chunk，不能被消息中伪造的标题或摘录替换。无法访问时明确不可展示；超过 4000 字的 chunk 不截成貌似完整的答案片段，提示打开相应版本原文核对。

`business.record_handoff` 与 `runs._finish` 的 worker 恢复路径共用构建函数。当前运行所有权与租约约束、唯一 run_id、快照和 created audit 的同事务落库共同保证重试只保留首次快照。10 个并发请求只有一条交接与一条 created audit；审计失败时整体回滚，重试可恢复。记录失败仍不能得到成功编号，也不能自动创建正式工单。

迁移 `0005_handoff_context` 只为交接表增加可空 JSONB `context`，本次仅在可丢弃的测试数据库执行 `0004 → 0005`。历史 `context=null` 显示“历史记录未保存故障上下文”，没有回填猜测数据；没有执行生产迁移或 downgrade。

## 支持工作台与权限

`GET /escalations/{id}` 返回当前授权明细。普通员工只可读取自己的记录，其他员工为 404；当前启用的 support/admin 可读取与处理。列表不携带未经逐引用鉴权的上下文原文。审计只返回操作者、时间、事件及状态/处理人/版本的 from/to 白名单字段，分页每页最多 50 条，游标绑定当前交接记录，其他记录的游标拒绝。

`EscalationDetails` 展示实际故障字段、未知/省略提示、员工原文来源、可访问引用及处理审计。引用入口携带真实 revision；处理保存后重新读取明细，刷新失败会清除原明细，避免继续显示失去权限的旧内容。现有版本冲突及合法状态迁移继续生效。处理后的恢复是重新打开工作台并读取持久记录；没有把页面导航位置持久化作为本任务行为。

## 验收与证据分类

最终全量为 **513 后端、43 前端、28 浏览器 passed，failures/errors/skipped 均为 0**。逐 testcase 统计和报告 hash、18 份修改前文件 hash、冻结的 T04 当前源码及差异见实施证据（本地证据：`artifacts/remaining-tasks/handoff-context/implementation-evidence.json`）。构建、Ruff、锁文件检查通过；Ruff 启动日志中的 uv 跨盘 hardlink 回退提示不是代码检查失败。

后端使用 `compose.test.yml` 的真实隔离 PG/Redis/Qdrant，全部 TEST URL 显式设置。新增 11 项上下文用例覆盖服务器事实/当前草稿、员工消息边界、并发不可变快照、审计回滚、worker 失效恢复、员工/当前角色权限、停用处理人、当前来源与快照原权限、旧行、56 条审计的 50+6 分页。资料、模型与身份输入为合成或受控数据；这些结果不能代替真实文档或模型语义质量。

前端新增 3 项测试；真实 Chromium 的 1440/390 两个 Mock HTTP 用例核对上下文、分派/状态保存、审计版本变化、刷新后重新打开、引用版本原文，并检查地址/标题、无错误 overlay、无 console warning/error、无横向溢出。最终 `browser-mock-final/handoff-processed-1440.png` 与 `390.png` 已人工查看，内容和按钮可读。这是浏览器渲染与受控 HTTP 证据，未执行本阶段 `production-live` 或真实企业登录。

失败复现独立保留：`intent-red.xml` 为 25 项中的 14 个业务失败；`persistence-red.xml` 7 个失败；`frontend-red.xml` 3 个失败；`intent-reverse-red.xml` 11 项中的 6 个失败，修复后人工意图 36 项通过。初始 `intent-first-red.xml` 另含作者使用不存在的 `FakeTicketService.token_calls` 属性这一测试错误，改用实际 `issued_drafts` 后重新取得有效 RED，没有将该错误算作产品缺陷。初轮浏览器 `browser-all.xml` 为 26 passed、2 errors：新测试错误假设刷新后仍停留支持页，改成实际“进入支持工作台”操作后重新核对同一持久记录；保留原报告，最终 28 项通过。

可执行验收：按根 `AGENTS.md` 设置全部隔离 TEST URL，从 backend 运行 `.venv/Scripts/python.exe -m pytest -q tests/unit/test_handoff_intent.py tests/production/test_handoff_context.py`，再执行全量后端、Ruff 和锁文件检查；前端执行 `npm test`、`npm run build`、`npm run test:e2e -- --project=demo --project=production-mock`。原只读 HANDOFF-002 输入“请转人工”保持不变，当前为 `handoff / explicit_manual_request`，无查单或草稿调用；CREATE-001 仍为正常信息收集。

这是作者软件验收及文档核对，整个剩余软件增量的唯一独立最终审查仍在发布阶段执行。T05 最新处理进度、T06 真实 PDF/模型、T07 批准目标环境及 GitHub 交付保持各自待办，不以本轮通过替代。
