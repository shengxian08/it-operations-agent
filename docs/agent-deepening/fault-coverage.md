# 故障覆盖与证据边界

| 故障 | 可执行覆盖 | 证据层与结果 |
|---|---|---|
| 工具超时、错误格式、范围或模型伪造身份 | `backend/tests/unit/test_operations_mcp.py`、`test_agentic_retrieval.py`、`test_agent_budget.py` | scripted；严格输入、未知/失败、审计和共享预算。 |
| 预算耗尽、子任务失败、取消 | `test_investigation.py`、`test_agent_budget.py`、`backend/tests/production/test_runs_worker.py` | scripted + PostgreSQL；父预算和取消传播，迟到回调不能越过租约。 |
| 模型调用超时或取消后的未知消费 | `test_investigation.py`、`backend/tests/production/test_runs.py` | scripted + PostgreSQL；保留 usage_uncertain，Worker 按原月预算预留消费结算，不把未知调用当作零成本。 |
| 服务范围撤销后的报告、事件与消息历史 | `backend/tests/production/test_runs.py` | PostgreSQL；三个读取入口使用当前服务范围，缺失范围为空，已经生成的日志不能通过历史消息泄露。 |
| 撤权、来源停用、私有关系桥接 | `test_relation_retrieval.py`、`backend/tests/production/test_agent_relations.py` 与既有历史权限回归 | PostgreSQL；发布/查询/历史展示重新核查当前权限。 |
| 知识版本变化、候选未发布、重复关系与回滚 | `test_agent_relations.py`、`test_relation_retrieval.py` | PostgreSQL；固定知识与图版本，原文证据、唯一激活指针。 |
| 重复提交、Worker 中断、响应丢失、建单确认 | `backend/tests/production/` 中既有 run、ticket、worker 回归 | PostgreSQL/Redis；任务指纹、版本确认、稳定回执和租约 fencing。 |
| 实际协议与来源链路 | `scripts/agent_protocol_demo.py` | real-services；stdio/授权 HTTP MCP、公开 Markdown 解析、Qdrant 索引、图预览发布与检索。 |
| 页面重连与快照来源 | `scripts/agent_browser_demo.py`、前端 `agentDeepening.test.tsx` | 浏览器 + scripted；实际任务模式、报告、快照时间、刷新恢复与未知受理重试。 |
| Kubernetes 服务状态、日志、发布、未配置指标 | `scripts/agent_kubernetes_demo.py`、`test_kubernetes_reader.py` | real-services + scripted；ready 状态可读，请求可达性和无提供器的延迟为 unknown。 |

具体执行次数和日志摘要见 `verification.json` 与 `evidence/`。旧生产恢复、企业身份、真实 PDF 和模型目标验收未由本表替代。取消或中断后的任务级恢复不等于任意模型节点续跑。
