# T05 实施计划

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../../verification/README.md)。

延续已授权的 executing-plans inline；整个剩余软件增量仅发布前一次最终独立审查。设计见 ../specs/2026-10-03-ticket-progress-design.md。

- [x] Task 1：真实隔离依赖测试稳定复现状态无进度、评论无可见性、历史回放无当前授权。persistence-red14fail、legacy-red1fail均保留；旧业务断言继续保留。
- [x] Task 2：评论分类迁移、当前鉴权、同事务可见进度/时间、白名单审计；来源快照和全部历史读取过滤；legacy 有据事件及确定性答案。锁等待期间降级查询先RED后修复，另两项写入/分类反例为修复后新增；最终24项进度用例通过。
- [x] Task 3：工单详情公开/内部分类与旧记录核对、记录标签、真实 API 权限测试、Vitest 与1440/390浏览器交互。3项新增前端用例与2项浏览器交互含于全量；最终截图核对可读、无溢出，Mock HTTP分类保留。
- [x] Task 4：可丢弃服务0005→0006迁移，全量537后端/46前端/30浏览器passed且failures/errors/skipped均0；build/Ruff/lock与迁移一致性通过。原6业务探针不改输入，QUERY-002进度由真实依赖24项测试证明；20份before/hash/current证据与架构/审查/待办更新，继续发布准备。

2026-10-03完成作者本机软件合同。详见[架构与复现](../../architecture/ticket-progress.md)、本地实施证据（本地证据：`artifacts/remaining-tasks/ticket-progress/implementation-evidence.json`）。旧principal新请求、前端重复文本定位与浏览器硬编码端口的作者测试修正保留原报告；首轮全量4个失败及修复后的537项通过分别留证。production-live新增流程本机未执行，真实企业身份/模型/目标环境不计已验；整个增量的唯一最终独立审查与GitHub交付仍待执行。
