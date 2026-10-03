# T02 多轮查单与澄清合同

目标来自用户当前持续目标与 improvement-backlog.md 的 T02 / QUERY-001。范围仅为查单对象解析、澄清和当前权限查询；T03故障收集、T04人工意图/摘要、T05评论进展、真实身份部署保持独立任务。

## 选择与授权

推荐服务端受控会话对象方案：从当前用户、当前会话的持久化查询记录和已确认建单记录取得候选，选择与授权分开。客户端回传history方案会允许任意对象注入，历史assistant文本方案会误用模型猜测，均不采用。用户已明确要求实施T02及其待办验收，继续细化这一合同并实现，不增加外部动作或重复设计审批。当前脏工作区按AGENTS精确增量，保存before字节，不从HEAD覆盖、不批量提交。

## 对象与多轮行为

1. 当前消息含一个完整工单号：查询该编号，去重、统一大小写，识别中文相邻书写与10000以上序号；拒绝相似但不完整编号。含两个以上编号：先澄清，不能只取第一个或依次试多个。
2. 明确指代当前会话工单（上次那个/上一张/刚才的工单）或紧接查单的进度追问：只在当前受控候选唯一时选择；多个候选不根据模型、时间或顺序猜选，返回编号选择；缺对象返回补号提示。澄清是正常完成的 `ticket_lookup_clarification`，没有handoff/escalation、确认token或建单副作用。
3. 紧接澄清可回复完整编号或明确第一/第二/第N个；序号严格对应上一条持久澄清中显示的候选顺序，越界继续澄清。中间出现知识问答/建单等新意图时不将裸序号当旧选择；显式取消终止待选择。知识问答不会被长久存在的工单上下文劫持。
4. 每次实际查询都调用原read-only工具，读取当前身份/角色/账号可用状态与当前工单。历史编号只用于对象选择，历史found/status/assistant回答从不授权或替代结果。多轮间工单更新、角色收紧、账号停用、对象删除各自验证；超时/数据库异常保持查询失败，不返回not_found。

## 受控持久上下文

Graph输出带 `ticket_lookup` 确定性元数据：schema_version、user_id、conversation_id、outcome、ticket_number、basis、reason、candidates。该字段从服务器解析/工具结果产生，进入ProductionRun.result/final事件原事务；普通HTTP输入只有content/client_message_id/idempotency key，传来的history/ticket_context不会进入graph。

Worker在有效租约及当前账号/角色校验后加载 `ticket_context`：绑定user/conversation、去重候选、上一完成轮是否查单、待选择编号、overflow。从真实terminal查询记录读取；可兼容旧已完成ticket_status运行对应的原user请求中明确编号，不解析assistant正文、不重写旧记录。已确认TicketDraftRecord同owner/conversation的ticket_number也是明确对象；未确认草稿不计。候选输出上限20，超过时要求完整编号，不把截断后的一项当唯一。

服务端扫描全部相关记录（流式、内存候选有界），不只看模型的20条历史窗口而遗漏旧对象。并发活跃会话已有串行/租约约束；取消、失效租约或未持久完成结果不得变成待选择记录。普通旧demo路径按相同纯解析/澄清规则，从其owned AgentRun.node_history/原user消息加载受控上下文，新的选择元数据随节点记录持久化，不添加demo权限。

不新增数据库schema或索引迁移，复用既有结果JSON和确认记录。新的上下文读取失败不得降级为空候选假装缺号；worker按既有受控失败事务处理。

## 界面与恢复

生产聊天结果明确显示“请明确要查询的工单”，正文说明缺号/多候选。候选只展示完整编号，点击“查询工单 …”提交该明确编号，经同一既有请求流程重新查当前状态。未选择前不显示已查询/已转人工/已建单。刷新/SSE重放从durable result恢复候选与答案；切换会话用各自RunView，不串候选。窄屏可用，无横向溢出。

## 可执行验收

纯graph：唯一受控对象、两个候选、当前两个编号、缺号、自然进度追问、序号/越界、取消/换意图、中文紧邻/大小写/10000号；任意history、跨user/conversation上下文不授权；工具超时/异常与not_found分开；调用精确编号、仅一次、无token/建单/模型副作用。

实际隔离PG/Redis：enqueue→claim→Worker→ChatService→真实ProductionTicketService→durable result/message/event→follow-up；状态更新、同会话两个对象、切会话/换用户、角色收紧/账号停用、对象删除、长期历史、同幂等key重放、取消/租约fence、旧明确请求与确认建单记录。实际ASGI API检查owner范围、客户端history/context注入、HTTP final/SSE恢复。

Vitest/Playwright：缺号和两候选正常澄清、选编号再查询、持久恢复、切会话隔离、1440/390视口；Mock HTTP明确标记，截图人工查看。全量后端/前端/构建/browser/ruff/lock按根AGENTS命令，四计数读取；只运行compose.test现有服务，不down/recreate，不读secrets/上传企业资料/调用付费模型。最后唯一独立代码审查，重要问题一次TDD修复，逐项完成审计。
