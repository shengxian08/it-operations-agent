# IT 运维知识助手：需求与交付核对

| 需求 | 本次结果 | 复查入口 |
|---|---|---|
| 有界 Agentic RAG | 已实现并通过控制与应用集成测试；默认实验开关关闭 | `backend/app/agent/agentic_rag.py`、`tests`、评测说明 |
| 六个只读运维工具 / MCP | 已实现，独立 stdio 与授权 HTTP 客户端通过 | `evidence/protocol-evidence.json` |
| 来源、权限与版本关系层 | 已实现候选、校验、发布、两跳、激活与回滚 | `backend/app/production/relations.py`、迁移0007 |
| 主控制器与两个调查 Agent | 已实现独立上下文、循环、共享预算与取消 | `backend/app/agent/investigation.py`、报告证据 |
| 持久状态 / UI / 人工流程 | 已接入任务级进度、报告、引用与刷新；复用工单/交接 | `evidence/browser-evidence.json` |
| Kubernetes 只读调查 | 已在隔离 kind 验证；无法观测的指标明确未知 | `evidence/kubernetes-evidence.json` |
| 冻结集与对照 | 每项目120验收任务、独立6开发任务；4配置×3次，1440控制运行 | `eval/agent-deepening/`、`evaluation.md` |
| 权限、预算、失败与恢复 | 有自动化与真实服务覆盖，结果按证据层分别归档 | `fault-coverage.md`、`verification.json` |
| 真实模型与独立人工质量 | 未验证；有显式可信适配器与完整人工评审入口 | `evaluation.md` |

当前控制集通过不代表120条自然语言任务语义已验收。多 Agent、关系增强与上下文策略的5个百分点收益、成本/P95门槛，以及IT引用支持率95%仍需真实模型和独立人工标注。默认路由未因合成测试切换。

真实企业数据、真实支付/退款、图片提供器的真实质量、生产高可用及灾备目标均维持独立验收边界。节点级checkpointer保留为后续长等待需求扩展，本次交付任务级恢复及明确失败位置。
