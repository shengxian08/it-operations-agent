# 最终整分支复核与一次修复记录

复核范围为两个获准基线之上的完整实现：IT `293b37b..f7640a0`，电商 `3788835..da8c181`。电商基线包含用户已经批准的待提交实现。复核使用一个独立上下文、只读的最终 reviewer；没有逐任务 reviewer，没有第二次复核。以下为发现、作者按用户实际影响定级，以及同一次 TDD 修复后的证据。

固定 Review Focus：Authorization after revocation, task fingerprint legacy compatibility, immutable proposal confirmation, snapshot vs current fact semantics, conflict/no-evidence behavior, shared budget enforcement, relation ACL/version intersection, uncertain tool writes, cancellation/lease fencing, recovery idempotency, model quality claims.

| 发现 | 定级 | 修复与验证 |
|---|---|---|
| 电商显式任务及协作结果形成多个或不在首位的 SystemMessage；继承子调用伪造父提供器 continuation | Important | 合并为一个首位 SystemMessage；子 Agent 输出进入标记为数据的用户上下文，观察保留用于事实核对，不伪造提供器轮次。真实 Responses serializer 的四配置与子观察契约 RED→GREEN。 |
| IT 消息历史没有当前服务 scope；缺省回退允许 checkout/catalog | Important | messages、任务/事件/进度都用当前授权范围，缺失授权范围 fail closed。实际运行、事件和消息历史撤权测试 RED→GREEN。 |
| 电商在模型等待期间撤销商家角色仍可能保存/同步返回私有成功答案 | Important | 模型返回时、数据库终态短事务、同步响应的持久回放投影分别复核当前角色。运行单测、真实 PostgreSQL 终态与同步服务测试 RED→GREEN。 |
| IT 模型超时后未知用量按零消费释放月预算预留 | Important | 模型调用异常/取消标记 usage_uncertain，预算快照保留该字段；单/多调查及 Worker 月预算结算 RED→GREEN。 |
| 电商比较结果用数组位置或缺失顶层 product_id 判定过期，误使不同实体事实失效 | Important | 按商品/订单/政策实体身份定位字段；换序和其他实体查询保留事实，同实体变价或未知使旧事实失效。RED→GREEN。 |
| 电商输出预留用可见字节上限，没有约束提供器实际最大输出和真实总用量 | Important | bootstrap 传入提供器输出上限，父预算统一预留，实际返回用量超过总预算时停止。输出 cap 与短答案超额用量 RED→GREEN。 |
| 同一提供器契约修复中发现新工具 optional schema 不满足显式 strict=true | Important（补充契约） | 封闭 schema 的所有属性 required、可选字段保留 nullable、移除默认注释；动态对象或不支持的结构保留原 schema 并显式 strict=false，执行器验证与授权继续独立。实际商品工具/动态元数据两例 RED→GREEN。 |

最后一项按 [OpenAI 官方 function calling 文档](https://developers.openai.com/api/docs/guides/function-calling) 核对，验证的是本地协议格式；没有调用真实模型 API。提供器的原有 continuation 严格校验没有放宽。

复核同时认可：知识/关系 ACL 与版本交集、关系激活指针、不可变售后提案与事务回执、旧请求身份和租约、共享父预算，以及明确分层的质量声明。没有 Critical；所有 Important 均在同一次修复中处理；没有 deferred minor。

## 未独立判断的范围与作者裁决

1. **real-model semantic quality, citation support and strategy benefit**：public scripted controls and real-service contracts are delivered; real-model and independent human acceptance remains unverified。代价：quality and the 5pp/1.5x adoption benefits remain unknown until separately measured。
2. **full semantic correctness of merchant draft prose**：free_text_support stays unverified and content requires human review; deterministic critical-field verification does not certify all prose。代价：manual review remains necessary and can miss unsupported creative claims。
3. **enterprise/private documents, true embeddings, PDF semantic quality and target identity**：only approved public synthetic sources and isolated environments were used; existing PDF/target-environment acceptance boundaries stand。代价：a separate target-environment validation is still required。
4. **real funds, production capacity/HA, cross-instance disaster recovery and node checkpointer**：existing controlled business execution and task-level recovery stand; kind and same-instance separate-database restore are local evidence only。代价：no production availability, financial-channel or automatic node-resume guarantee。
5. **every mid-tool crash or transaction combination**：actual claim/interruption/replay and receipt-loss recovery are tested, while the hard-Pod waiting runner stops before business tools。代价：remaining fault-matrix combinations need additional drills before production use。
6. **theoretical stale AgentAftersaleProposal component proposal ID**：CommerceApp remounts the component with key=proposal_id; the proposed stale-ID bug does not occur for current callers, so code stands。代价：future callers must preserve remount semantics or add explicit ID synchronization。
7. **independent reviewer verification of the final full suite**：the reviewer was read-only; the author ran and archived complete fresh suites after the one TDD fix pass。代价：the final suite evidence is author-owned rather than independently rerun by the reviewer。

## 最终核查

IT 后端 662 passed（含新增撤权和未知消费测试）；前端 49 passed，端到端 31 passed，TypeScript/Vite 和现有 lint/lock 检查通过。修复后真实 stdio MCP、授权 HTTP MCP、知识解析/索引/关系发布、浏览器调查及只读 kind 数据再次通过。

两个项目各 120 条冻结验收任务及 6 条独立开发任务，四配置各重复三次；每项目 1440 次 native 控制通过。它们验证实现合同，没有替代真实模型或独立人工语义评测，采用门槛仍未获得真实测量，因此默认仍选择 single。

完整日志 SHA 与失败/通过记录见 [verification.json](verification.json)，逐任务及所有裁决见 [execution-ledger.md](execution-ledger.md)。第一次错误预留测试断言、历史镜像/分页文件中断和被中止的测试运行均保留，不能算作通过记录。原工作区回写按冻结基线逐文件 hash 核对；源分支和用户暂存区分别保留。
