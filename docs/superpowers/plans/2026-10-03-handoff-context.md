# T04 实施计划

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../../verification/README.md)。

> 继续 executing-plans inline，按业务任务独立验收；整个剩余增量发布前只有一次最终独立审查。Spec: ../specs/2026-10-03-handoff-context-design.md。

- [x] Task 1 — 实际图显式人工请求 RED→GREEN。新增 agent/handoff.py 与 unit/test_handoff_intent.py，修改 graph/state；安全与预算优先，明确动作不调用工具，否定/制度咨询/引文不误触发。原25项加逆向11项，最终36 passed。
- [x] Task 2 — 新 production/handoff_context.py、Escalation 可空 context 迁移、business.record_handoff 与 runs._finish 共用构建。真实依赖 RED→GREEN 证明原文/结构字段/引用身份可信且有限、当前引用权限、重试快照不变、并发/回滚/恢复/lease。上下文安全11项通过，旧租约/事务回归含于全量。
- [x] Task 3 — 明细/审计 API 与有效处理人。当前数据库角色/enabled、员工/支持边界、from/to/version 审计、停用处理人反例；支持页展开上下文和审计，操作后恢复。新增3项Vitest与1440/390两项Mock HTTP浏览器留证；最终截图已人工查看。
- [x] Task 4 — 隔离迁移、全量后端/前端/build/e2e/Ruff/lock，核对四计数；原 HANDOFF-002 探针改为显式原因，T05保持未完成；before/current/RED 与实施文档、审查和待办更新。最终513/43/28 passed，failures/errors/skipped均0，build/Ruff/lock通过。随后继续 T05 与发布准备。

验收时间：2026-10-03。详见[架构与复现](../../architecture/handoff-context.md)、本地实施证据（本地证据：`artifacts/remaining-tasks/handoff-context/implementation-evidence.json`）。本任务完成作者本机软件合同；尚未执行整个剩余增量的唯一独立最终审查，真实企业身份/模型/目标环境不计已验。首次图测试属性拼写及浏览器刷新导航假设的作者错误已修正并保留报告，未改业务质量阈值或原探针输入。

Review focus: 不伪造事实/来源，不保存整段assistant或完整任意历史，旧行不补猜；有持久编号才记录成功，pending不等于接单；权限停用/降级与snapshot原权限同时约束引用；保存和恢复合同一致；副作用/审计一次性、租约/回滚安全；处理人有效，员工隔离，支持工作台实际可核对。
