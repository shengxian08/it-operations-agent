# GitHub 工程交付记录

2026-10-03，完整项目已按明确文件清单正常推送到 [codex/production-engineering](https://github.com/shengxian08/it-operations-agent/tree/codex/production-engineering)。软件提交为 [`6aa3570ecfa6f662d99a5ffccbfc5d6d5346b630`](https://github.com/shengxian08/it-operations-agent/commit/6aa3570ecfa6f662d99a5ffccbfc5d6d5346b630)，tree 为 `d7543e968b7fd24dc132e01c78815c01d442ab56`，远端与本机核对一致。原 `main` 和用户工作区内容保留。

本页记录上述软件提交的实际证据；后续文档归档与小型镜像 metadata 输出由新提交继续验证。当前分支的精确提交和任务状态见 [GitHub Actions](https://github.com/shengxian08/it-operations-agent/actions?query=branch%3Acodex%2Fproduction-engineering)。

## 实际执行

[CI run 37100100559](https://github.com/shengxian08/it-operations-agent/actions/runs/37100100559) 的 backend、frontend、production-e2e、immutable-images 四个任务全部 success。

| 执行范围 | Passed | Failures / Errors / Skipped | 分类 |
|---|---:|---|---|
| 后端全量 | 616 | 0 / 0 / 0 | Ubuntu runner、真实可丢弃 PG/Redis/Qdrant，受控数据与模型 |
| 前端 Vitest | 46 | 0 / 0 / 0 | React 渲染与受控 HTTP |
| 浏览器 demo / production-mock | 31 | 0 / 0 / 0 | Chromium，Mock HTTP；新增进度消息及刷新用例 |
| production-live | 2 | 0 / 0 / 0 | 实际 Keycloak/OIDC、API/worker/数据库与静态前端，合成账号、Mock 模型 |

21 项评估逻辑是后端重复子集，单独通过且不累加。机器门禁逐例比对执行前收集清单与实际 XML/JSON，确认 checkout SHA 一致、源码未修改、无遗漏/重复/跳过/重试。Ruff、冻结依赖、迁移 upgrade/downgrade/upgrade/check、生产构建和 demo 排除也在隔离 CI 中执行通过。没有对共享或生产环境执行这些迁移或清理。

真实协议生命周期覆盖：知识上传/核对/异步发布、来源权限、持久回答与刷新恢复、故障事实收集、编辑重签与确认建单、公开/内部评论、查单引用实际公开评论 ID 和原始 UTC 时间、重新开启、持久人工编号与 pending、支持分派/处理审计、显式人工上下文、来源停用/版本激活、账号停用恢复及退出。并发、回滚和未知响应幂等还由后端实际隔离依赖用例验证。

## 保留的失败与最小修正

| 编号 | 实际复现与根因 | 最小修复及保持的验收 |
|---|---|---|
| CI-001 / P2 | [首轮 run](https://github.com/shengxian08/it-operations-agent/actions/runs/37098994877) 后端 614passed/1failure/0errors/skipped。HTTP 测试 job 的 `COOKIE_SECURE=false` 被“有效生产配置”fixture 继承，生产校验正确拒绝；本机同环境 7passed/1failure。 | [生产配置测试](../../backend/tests/unit/test_production_config.py)显式填写有效 Secure cookie，并单独证明不安全 cookie 仍被拒绝；继承 false/true 均 9passed。产品校验和门槛保留。 |
| CI-002 / P2 | [第二轮 run](https://github.com/shengxian08/it-operations-agent/actions/runs/37099390496) 后端/前端/Mock 浏览器通过，真实协议 1passed/1failure。`.outcome-card` 只显示状态，测试却在那里寻找进度正文；受控反例先证明助手正文可见，再复现旧定位失败。 | [实际生命周期](../../frontend/e2e/production-live.spec.ts)改用助手消息，保留原公开正文、UTC 和内部内容不可见；增加真实持久 run、评论 ID/时间和刷新断言。[受控浏览器](../../frontend/e2e/production-mock.spec.ts)新增消息/状态/刷新用例。原答案和质量阈值未改。 |

原失败报告、before/current 和差异继续留在本地 `artifacts/remaining-tasks/release-preparation/`；公开仓库保存测试源码、计数/hash 和本记录。没有为这些 CI 测试修正另做第二轮独立 reviewer，之前的一次审查原结论及作者修复见[审查记录](final-review.md)。

## 源码与制品

软件提交的 [GitHub 源码 ZIP](https://github.com/shengxian08/it-operations-agent/archive/6aa3570ecfa6f662d99a5ffccbfc5d6d5346b630.zip) 包含 323 个明确项目文件。后续归档文档加入清单，最终源码包以该分支提交为准。运行配置、未知笔记、私有文档、profile、缓存和原始模型/浏览器报告未纳入发布。Windows 本机源码 ZIP 用单次命令关闭 autocrlf，逐文件比较 canonical Git blob；没有改用户换行设置或工作区。

- [后端安全摘要](https://github.com/shengxian08/it-operations-agent/actions/runs/37100100559/artifacts/11265723698)
- [前端/浏览器安全摘要](https://github.com/shengxian08/it-operations-agent/actions/runs/37100100559/artifacts/11265718593)
- [真实协议安全摘要](https://github.com/shengxian08/it-operations-agent/actions/runs/37100100559/artifacts/11265359456)
- [API、worker、web commit 镜像](https://github.com/shengxian08/it-operations-agent/actions/runs/37100100559/artifacts/11265724146)

CI 使用精确 SHA tag 与 `org.opencontainers.image.revision`，从源码目录外实际 import 已安装 wheel，核对 API/worker 的全部 Python 文件与镜像源码一致；输出 image-manifest、包记录和 SHA256SUMS。镜像 artifact 为 439,832,158 bytes，GitHub 报告 ZIP digest 为 `sha256:4f3e72963ec91a37713a48c642d2aa8de5e8d6c02ceadce66ac53de3e5e44e0f`。本机大文件下载与范围读取未完成，没有把部分下载写成完整 tar 校验通过；新增独立小型 metadata artifact 便于后续读取这些原有证据。制品保留 14 天，下载需要对应 GitHub 访问权限；没有外部 registry 推送或部署。

## 未完成的真实门禁

T06 缺经批准的真实失败企业 PDF、人工逐页/单元格真值及完整相关集合、真实模型和固定企业 embedding 的执行证据。T07 缺批准目标 Linux/TLS/真实企业身份、30 分钟容量/P95、完整空环境恢复与外部备份/RPO/RTO、同 schema 回滚和实际 receiver 收到告警的证据。当前 Ubuntu 隔离 CI、合成 Keycloak 账号和 Mock 模型按其范围通过，整个目标继续保留这些未完成项，详见[待办](../improvement-backlog.md)和[正式验收](../production-acceptance.md)。
