# 发布前独立审查与修复记录

2026-10-03，整个剩余软件增量和完整项目候选进行了**一次独立审查**。审查读取实际未提交工作区、逐任务 before/current 差异和 319 文件候选清单；不是以空的 Git HEAD 范围替代审查。独立重点回归 137 项通过；新增 13 个合成反例均发生业务断言失败，归为以下 5 项 Important。作者保留原审查、失败报告和原始数据，执行一次修复与回归，没有第二轮 reviewer。

## 已复现的问题与修复合同

| 编号 | 场景、原行为与影响 | 根因、最小修复与可执行验收 |
|---|---|---|
| R1 / P1 | 知识来源停用、当前角色不再允许或原快照受限后，运行、引用事件、消息和模型历史仍暴露原回答。展示引用可能只是实际模型上下文的子集。 | 原过滤仅处理工单。现在图与 durable final/citations 保存全部 prompt 来源身份；每次回放交集检查当前来源状态/权限及原 snapshot/chunk 权限，任一来源不可核验则隐藏答案/引用，原记录保留。知识回答不以自由文本进入下一轮模型历史。[实际 PG/HTTP/SSE/worker 用例](../../backend/tests/production/test_answer_replay.py)覆盖未展示来源撤权、下一轮 prompt、重复请求、终态取消、缺 revision/chunk 与支持降级，并保留当前允许来源的正常回答。 |
| R2 / P1 | 支持人员等待交接行锁时被降级或停用，释放锁后仍能变更状态/分派并写入持久审计。 | actor 只在锁前刷新。现在锁后、任何版本或业务写入前再次检查 enabled/current role。[交接 HTTP 用例](../../backend/tests/production/test_handoff_context.py)覆盖状态/分派 × 降级/停用，要求 403 且状态、版本、处理人和 audit 无副作用；既有有效支持人员、并发与回滚回归保留。 |
| R3 / P1 | legacy writer 或缺 ProductionRun result link 的助手消息绕过工单来源校验，显示或送入模型。正常新 ProductionRun 的完成仍是原子关联，没有证明它自己产生孤儿。 | 缺关联时原来退回原文。现在缺可信 production result 的 assistant 行显示来源不可核验提示，不进入模型原文历史；用户原文与数据库原始答案不改。R1 的[历史用例](../../backend/tests/production/test_answer_replay.py)分别覆盖正常 legacy writer、有/无 legacy link 和 production nullable link 缺失。不猜单号、相邻消息或页码，也不读取生产库证明存量。代价是部分旧回答需要重新查询。 |
| R4 / P2 | 收集“已尝试操作”时，“取消”“算了”“不用了”被当成事实并签草稿；否定建单被当成重新开始。引文中的“不要创建工单”还会重置已收集事实并丢失原文。 | 取消语法过窄，creation 搜索/替换不分动作与引文。现在在 owned pending intake 中优先识别明确取消，拒绝控制文字作为字段；建单匹配屏蔽引文及字段值，只移除实际指令。[纯图](../../backend/tests/unit/test_ticket_intake.py)与[实际 worker](../../backend/tests/production/test_ticket_intake_persistence.py)要求三个字段取消时 0签名/草稿/正式单/交接，后续不能续用旧状态；实际取消按钮操作和引用文字完整保留。语言覆盖仍有边界。 |
| R5 / P2 | 套件级 error 和每模块仅一例的部分报告被判完整；同项目只登录也可能掩盖生命周期未执行。 | 只看 testcase 计数和模块出现不足以证明完整。现在验证 XML 支持拓扑，逐项比对执行前收集的 testcase 身份与 XML/JSON，拒绝遗漏、重复、deselected、收集/执行失败、跳过、预期失败、重试；GitHub 摘要还要求 checkout SHA 一致且源码未被测试改动。[门禁用例](../../backend/tests/unit/test_ci_report_gate.py)、[pytest collector](../../scripts/ci/pytest_inventory.py)和[工作流](../../.github/workflows/production-validation.yml)覆盖三类运行器，只有安全计数/hash 摘要上传。没有硬编码当前测试总数作为门槛。 |

## 证据与当前状态

原审查和独立反例在本地 `artifacts/remaining-tasks/final-review.md`、`review-probes/` 留存，作者 RED/GREEN 与修复前源码在 `final-fixes/` 留存。这些是本地留存位置，公开仓库只保存本页、测试源码和安全摘要。实际 PG/Redis/Qdrant 为可丢弃测试服务；文本、身份、模型输出与浏览器 HTTP 为受控数据。

修复后作者最终全量为 **615 后端、46 前端、30 demo/production-mock 浏览器 passed，failures/errors/skipped 均为 0**，完整收集清单逐例匹配实际报告；构建、Ruff、锁与迁移一致性检查通过。取消相关 157 与 CI 门禁 51 属于重复子集，不另加到总数。原审查 13 项反例复制到独立留存目录，源码逐字节一致，由作者重跑全部通过；原 13 项失败报告不覆盖。这是作者修复核验，没有改变 reviewer 原 ready: No，也没有第二轮独立批准。随后源码正常推送，616 后端、46 前端、31 Mock 浏览器、2 项真实协议 E2E 及 commit 镜像实际 GitHub 门禁通过；两项测试配置/定位修正与精确来源见[GitHub 交付记录](github-delivery.md)、[公开验收索引](README.md)和[机器摘要](software-validation.json)。

## 审查未判定的范围

真实故障 PDF、人工相关集合、批准真实模型与企业 embedding（T06），以及目标 Linux/TLS/企业身份、容量、完整恢复/外部备份、回滚和实际 receiver（T07）仍缺批准输入和执行证据。此次审查、受控回归、源码或镜像交付不能关闭这些门禁。

生产库中 legacy/orphan 数量与频率没有读取；非支持 PDF 布局、任意自然语言的普遍语义正确性、所有依赖的 CVE/供应链与穷尽渗透测试未由本次审查证明。Reviewer 没有重跑完整视觉/跨平台性能，作者截图与测试仅按其原分类留证。后续 GitHub Actions、production-live 和镜像已实际运行，按[交付记录](github-delivery.md)的合成身份/Mock 模型边界留证，T06/T07 继续未验。
