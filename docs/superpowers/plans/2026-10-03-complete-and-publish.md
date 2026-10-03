# 完成剩余任务并交付 GitHub 项目

> **For agentic workers:** Use superpowers:executing-plans inline for each independently accepted business task. Parallel agents may perform independent read-only analysis; one fresh final reviewer assesses the complete software increment before publication.

**Goal:** 完成待办T03/T04/T05、取得T06/T07要求的真实验收证据，并将经过检查的完整项目推送到用户现有GitHub仓库。软件推送不能代替真实门禁；任何未取得的证据保持未完成，整个目标不缩小。
**Architecture:** 沿用现有图、受控服务器上下文、当前鉴权、确认事务、人工持久记录与运行租约。按业务任务逐项RED→GREEN→逆向核查，最后统一回归、审查和发行。
**Tech Stack:** 当前Python/FastAPI/LangGraph/SQLAlchemy/PostgreSQL/Redis/Qdrant、React/TypeScript/Vitest/Playwright、Docker及GitHub Actions。

## 当前证据与权限

2026-10-03核对当前工作区：T02已完成；T03/T04/T05已完成作者本机软件合同，T06/T07缺真实样本与批准环境。根AGENTS仅一份，既有未提交正式实现继续保留。origin为https://github.com/shengxian08/it-operations-agent.git，公开仓库，当前账号ADMIN，远端main与本机HEAD均979ed7f8a6a821e4ba2ad30b2c2d9d216cb2f63b。

用户当前明确授权项目推送，不再要求重复确认该动作。仅提交逐项审阅的项目文件与任务增量，禁止git add -A、覆盖用户修改、推送企业文档、真实secrets、浏览器profile、tmp/output或原始诊断。现有业务分支codex/production-engineering继续使用，远端不强推。正式部署、生产迁移、容量、恢复与告警必须对应用户提供的批准环境。

## 顺序与完成证明

- [x] T03：故障信息收集。仅“创建工单”不发token、不写正式单；问题、影响、已尝试操作逐轮收集且来自当前owner/conversation服务器记录；完整摘要才签确认。原版本编辑重签、过期、并发10次、回滚、未知响应重试仍仅一单。子spec/plan为2026-10-03-ticket-intake。
- [x] T04：明确人工动作直接路由、持久化有限可追溯上下文、支持端读取/处理审计。真实编号才记录成功，pending不是接单；当前引用权限复核、员工隔离、有效处理人、并发/回滚/失败/恢复只有一条记录。子spec/plan为2026-10-03-handoff-context，最终513后端/43前端/28浏览器passed，0 failures/errors/skipped；属于作者本机软件合同，整个增量独立审查与真实目标验收仍待执行。
- [x] T05：最新可见评论/审计的时间和安全摘要。显式可见性、旧备注不补猜公开；当前角色/账号/工单与锁后重新鉴权，全部历史回放入口、空/多/内部记录、并发与故障分别验收。子spec/plan为2026-10-03-ticket-progress，最终537后端/46前端/30浏览器passed，0 failures/errors/skipped，build/Ruff/lock与迁移一致性通过；作者本机合同，真实环境及整体最终独立审查仍待执行。
- [x] 软件交付准备：明确发布清单、ignore/Docker构建排除、可公开的证据索引、README、CI机器可读报告与零跳过门禁。原始本机证据与真实数据留在本地。
- [ ] T06：真实企业失败PDF与人工真值、完整独立相关集合、批准真实模型与固定embedding revision、完整原页到答案/引用证据链。缺输入不能按合成或Mock完成。
- [ ] T07：批准Linux与真实企业身份/TLS、新版知识、30分钟容量/P95、完整空环境恢复/RPO/RTO/外部备份、同schema回滚、实际receiver接收。缺目标不能按本机通过完成。
- [x] 统一作者验收：显式TEST URL、真实隔离依赖、后端全量/前端/build/浏览器/静态/锁，读取tests/passed/failures/errors/skipped；发布准备最终569后端/46前端/30浏览器均passed、0 failures/errors/skipped；真实API、受控身份与Mock浏览器分别标注。
- [x] 唯一最终独立审查：319文件候选与before/current精确增量已审查，137重点回归通过、13反例失败归为5项Important；作者一次RED→GREEN及最终615后端/46前端/30浏览器通过，0 failures/errors/skipped，原13反例由作者重跑全部通过。原ready: No保留，没有第二轮独立批准；详见[修复记录](../../verification/final-review.md)。
- [ ] 项目推送：分逻辑提交经过检查的明确文件集，不混入临时/私有内容；正常推送origin，验证远端commit/tree与GitHub Actions实际结果，生成可下载项目与可公开验收索引。
- [ ] 全目标完成审计：逐项核对上述业务、真实门禁及GitHub交付，不将剩余环境阻塞重新命名为完成。

## Review Focus

建单不能填默认故障/影响/尝试事实；客户端history或assistant文本不能写受控上下文；字段修订、换意图、取消、错owner/conversation、数据库故障不能借旧状态签token；确认版本、重试、租约、副作用幂等保持。明确人工请求与否定/制度问答区分，最小故障快照及来源/引用当前权限、停用处理人、实际编号与真实pending/处理审计。评论公开/内部/旧未分类范围准确，当前和历史回放均不能泄露；故障不伪装不存在或无记录。只提交明确项目文件，忽略secrets/profiles/私有文档与诊断，公开证据不死链，CI不跳过真实依赖且镜像对应当前commit。T06/T07只有实际批准环境证据才通过。
