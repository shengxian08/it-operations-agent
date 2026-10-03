# T03 必填故障信息收集合同

依据用户“完成剩下的几个任务并生成项目推送到github上面”与待办CREATE-001。业务范围沿用现有建单/确认事务，补问题、影响范围和已尝试操作的多轮收集；不创建第二套正式工单事务、不引入付费模型验证。

## 方案与选择

采用确定性字段收集＋服务器终态记录。它能逐项保存员工实际提供的事实，完整后复用现有签名与版本确认。可选模型结构化提取需要另外评估真实语义质量与成本，本合同不依靠模型补字段；仅把客户端或assistant历史文本作为事实会失去可核所有权，不采用。自然回复绑定到服务器当前所问字段，显式“问题：/影响范围：/已尝试：”支持一次填写或修订，不猜缺失事实。

用户已授权既有待办的完整实现，合同按该验收细化后直接实施；无需重复设计/提交审批。工作区脏正式实现以本轮before字节保留，不由HEAD覆盖。推送在统一验收与发布清单检查后进行。

## 收集行为

“创建工单”没有故障事实时先问问题，不发确认token、不写TicketDraftRecord/正式工单/人工请求。得到问题后问影响范围及是否阻断工作；然后问已尝试操作。员工明确未尝试时保存空列表，与未回答的null区分。完整后展示原问题、影响和操作，生成draft并签当前版本token；确认仍必须经现有原子事务。

首次指令后的实际描述可作为problem。有标签的完整字段可以一轮完成；待收集期间无标签回复绑定到next_field。修订标签仅更新该字段，再按实际缺失字段补问。空白、仅“好的/确认/不知道”等不具备字段事实的控制回答继续澄清。字段超过限额不截断为通过，保留之前有效字段并明确重新提供。

problem最多2000字符、impact最多1000字符、attempted_steps最多10项且每项300字符。描述保留问题及“影响范围：”原文，不以“用户请求IT支持”补默认事实。title由实际problem生成，category沿用现有明确关键词规则。priority规则：员工影响明确为全公司/全部中断/无法办公/业务中断时high；实际问题含安全事件/设备丢失时critical；其他已提供影响为medium。每个草稿摘要显示影响与优先级规则来源，medium不是猜测已影响多人。

明确取消收集返回ticket_collection_cancelled，0副作用。新建单指令开启新的字段收集；明确查单/知识问答/人工新意图关闭旧pending，自然字段回复不能误用其他会话。知识问答或查单历史不自动成为故障描述。已签草稿编辑继续走现有draft版本API；改后旧token失效。

逆向补充：TicketDraft增加可空的problem/impact/intake_version字段以读取旧记录，新工作流显式设置intake_version=1及全部事实，保存在既有draft JSON中，不需新表。签名、编辑与正式确认都校验当前完整字段、已尝试明确提供及description与结构化问题/影响一致。旧pending没有这些事实时标记requires_details、不返回可确认凭据，员工补全编辑后重签；不向旧行补猜事实，不允许旧不完整草稿直接写正式单。当前编辑表单分别呈现问题、影响、步骤并合成可审阅description。

## 服务器状态与持久恢复

输出ticket_intake元数据：schema_version=1、user_id、conversation_id、outcome=collecting/ready/cancelled、problem、impact、attempted_steps、next_field、reason。对应final_state=ticket_collection/awaiting_confirmation/ticket_collection_cancelled。只读取当前owner/conversation最近终态的有效collecting记录；非法结构、其他终态、取消或换意图关闭pending。ProductionRun.result/final原事务保存；demo实际collect_ticket_draft节点保存。同HTTP输入只允许content与既有幂等字段，不能注入intake context/history。

worker上下文读取需有效租约及当前账号/owner；读取故障传播到既有failed人工兜底，不能伪装空字段或继续签token。模型history20条窗口与intake记录分开。前端显示已收集字段与下一问题，刷新/SSE重放及会话隔离；未完整时不显示确认按钮或人工成功。显示“尚未回答”与“明确未尝试”的区别。

## 验收

实际图先RED：空建单、三步自然回复、完整标签一轮、显式未尝试、字段修订、控制回答/长度限额、取消/换意图、错绑定与任意history、token失败，不调用模型。实际隔离PG/Redis：enqueue→worker→final持久化→下一轮，缺信息无草稿/正式单/升级，完整只有待确认草稿，owner/会话/重放、账号停用/lease/DB故障、确认版本/编辑/过期/10并发/rollback/未知结果仍仅一单。Vitest/真实Chromium Mock HTTP核对缺信息、完成、刷新、切会话、1440/390。
