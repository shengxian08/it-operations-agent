# 必填故障信息收集（T03）

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../verification/README.md)。

2026-10-03，当前工作区的软件合同验收。业务对象为 IT 工单。本任务采用员工逐项提供事实的确定性收集，不调用模型推断缺失事实。真实企业身份、真实模型、目标 Linux 和容量/恢复/告警仍分别属于 T06/T07。

## 已复现的问题与修复

CREATE-001（P1）：员工只发送“创建工单”，旧图立即签发带“用户请求 IT 支持”等默认描述的草稿。工单可以缺少可处理的故障信息。根因是 `collect_ticket_draft` 直接从当前文本及默认值构造草稿，没有必填字段状态；原始输入、修改前文件和实际图 RED 均保留。

现在先收集故障现象、影响范围及工作阻断情况、已尝试操作与结果。未回答保存 `null`，员工明确“尚未尝试”保存空列表。自然回复只填写服务器当前询问的字段；“问题：/影响范围：/已尝试：”支持一次填写或修订。缺字段、控制回答、纯标点或超限不会签凭证。问题上限 2000 字、影响 1000 字、步骤最多 10 项且每项 2–300 字；超限保留之前有效内容并要求重新提供。明确取消结束本次收集，新建单请求重新收集；查单、知识问答等新意图不会借用旧字段。

字段完整后，图才调用原确认服务。草稿保存 `problem`、`impact`、`attempted_steps`、`intake_version=1`，描述由实际问题与影响组成。初始分类沿用关键词规则，优先级依据实际故障/影响生成，界面显示规则并允许员工核对。服务端签名、编辑和确认共同校验事实与描述一致。旧 pending 草稿没有这些事实时标记 `requires_details`，不返回确认凭证；员工明确补全、保存和重新签名后才能确认。演示接口也拒绝不完整草稿及其旧凭证，不能绕过收集直接写单。

## 状态来源与业务安全

`ticket_intake` 包含版本、当前用户/会话、已提供事实、下一字段和 collecting/ready/cancelled 状态。正式 worker 在当前租约、账号及会话所有权检查后，从当前会话最近终态读取有效 collecting 记录；不依赖模型历史窗口，也不接收客户端 history/context。新记录随原运行事务及 SSE final 持久化，演示路径保存在实际收集节点。读取故障走可追踪失败路径，不伪装为没有信息。

未完成收集时，实际依赖测试断言草稿、正式工单和人工请求计数均为 0；收集完整后只有待确认草稿。确认继续绑定当前版本：编辑旋转凭证，旧 token 拒绝；过期、回滚、同 key 异参、重复请求和未知响应沿用原事务与幂等校验。10 次并发同 key 确认仅创建一张正式工单。

## 验收与证据范围

本机隔离环境使用 `compose.test.yml` 的 PG/Redis/Qdrant，显式传入全部 TEST URL，模型为 Mock。全量结果为 **466 后端、40 前端、26 浏览器 passed，failures/errors/skipped 均为 0**；构建、Ruff、锁文件检查通过。完整报告、修改前字节、当前 hash 和失败复现保存在 实施证据（本地证据：`artifacts/remaining-tasks/ticket-intake/implementation-evidence.json`）。这是作者验收；整个剩余软件增量的独立最终审查尚在发布阶段执行。

实际图覆盖空建单、自然多轮、标签完整输入、修订、明确未尝试、控制/标点、长度、取消、换意图、错用户/会话和伪造历史。真实隔离 PG/Redis、worker 和 ASGI HTTP/SSE 覆盖逐轮恢复、无提前副作用、重复请求同一 run、客户端额外 context/history 422、其他员工读取 404、唯一 final、编辑重签、10 并发确认；原确认回滚、过期和幂等回归也实际执行。

Vitest 与真实 Chromium 的 Mock HTTP 页面验证 1440/390 宽度下逐项收集、刷新、切会话、完整草稿及确认前禁用。页面检查包含当前地址/标题、无空白页、无错误 overlay、无 console warning/error、无横向溢出，以及逐项请求事实与无确认调用。截图明确属于受控 HTTP；不能据此证明真实企业登录或模型语义能力。`production-live.spec.ts` 已更新为真实多轮操作流程，该目标环境用例尚未在本阶段执行。

原评测数据与质量阈值保持原样；已有确认类回归仅明确补充该场景所需的合成必填事实，保留原 token、错误、预算、状态与副作用断言。老评测中直接期待不完整建单签名的样本属于旧合同，不能修改预期或补猜企业真值来制造质量通过。

可执行验收：从 backend 目录按根 `AGENTS.md` 设置隔离 TEST URL，运行 `.venv/Scripts/python.exe -m pytest -q`；重点为 `tests/unit/test_ticket_intake.py`、`tests/production/test_ticket_intake_persistence.py` 和原确认事务测试。前端执行 `npm test`、`npm run build`、`npm run test:e2e -- --project=demo --project=production-mock`。只读 `scripts/audit_business_paths.py` 保留 CREATE-001 原始“创建工单”输入，其当前结果为 `ticket_collection`、无草稿；HANDOFF-002 仍显示原 `insufficient_evidence` 缺口，交由下一项 T04 处理。
