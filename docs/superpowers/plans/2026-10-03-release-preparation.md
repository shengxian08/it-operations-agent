# 完整项目交付准备

用户已授权完整项目推送，继续 inline 执行。T03/T04/T05 已完成作者本机软件验收；T06/T07 真实门禁不由本次源码/CI交付替代。原未提交正式实现全部按明确文件清单保留与审阅。

- [x] 收紧 Git/Docker 排除：运行时环境文件、缓存、profile、tmp/output及原始证据均留本地；仅明确安全样例可提交。只验证匹配规则，不读取真实配置。
- [x] CI报告门禁 RED→GREEN：标准库解析真实 testcase 与JSON执行记录，拒绝空/缺/坏报告、失败/错误/跳过/预期失败/重试、进程失败、漏模块/项目与伪计数。输出限制为安全计数、hash、commit/run身份和原因码。
- [x] workflow 接入机器报告与精确上传：冻结依赖、真实隔离服务、迁移/build/Mock协议及commit镜像原门禁全部保留。全部原始报告只在runner内供检查，公开artifact限定安全summary；不部署。
- [x] 整理公开说明/证据索引与明确manifest。历史本机artifact用本地留存路径说明，避免作为不存在的公开链接；旧真实未验保持未验。精选截图必须是已人工核对合成/Mock，不发布浏览器trace、模型上下文或企业文件。
- [x] 整个增量一次最终独立审查，5项Important作者一次RED→GREEN修复、逆向及最终615后端/46前端/30浏览器通过；原13反例逐字节相同后作者重跑通过。原ready: No保留，没有第二轮独立批准。
- [x] 逐文件逻辑提交/正常push origin，远端6aa3570/tree一致、实际CI四任务success；CI-001生产有效配置和CI-002正文定位已稳定复现并最小修正，616/46/31/2均passed0f/e/s，镜像构建与包检查通过，原失败记录保留。
- [ ] 最后文档/小型metadata归档提交及其实际CI、制品和canonical源码ZIP核对。

首轮作者业务验收：537后端、46前端、30 demo/production-mock浏览器，0 failures/errors/skipped；迁移0006、Ruff、锁与构建通过。后续CI工具/发布变更必须使用新的对应报告，不能继承为新代码已通过。

发布准备作者核验：新增CI门禁32项RED→GREEN及逆向通过；全量569后端/46前端/30 demo、production-mock浏览器，failures/errors/skipped均0，实际机器报告门禁读取通过，Ruff/lock/build通过。production-live仅收集2项，实际执行和镜像仍待GitHub CI；不能用本机结果替代。
