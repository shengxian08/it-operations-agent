# 评测、对照与采用决定

当前决定：保留现有混合检索和单 Agent 默认路由；Agentic RAG、关系扩展、双调查 Agent 均具备可运行实验入口。当前没有真实模型和独立人工相关标注，因此引用支持率 ≥95%、复杂任务成功率提升 ≥5 个百分点、成本与 P95 ≤1.5 倍均标记未验证。

`eval/agent-deepening/acceptance.jsonl` 冻结 120 个不重复公开合成任务，涵盖直接知识、关系问题、只读调查、证据不足、权限变化和依赖失败，各 20 个；开发集 6 条，独立且不交叉。冻结文件记录数据 SHA256、工具、Prompt 和数据版本。运行器拒绝 hash 漂移、重复 ID、数量不足或开发集交集。

| 策略 | 编排 | 检索 |
|---|---|---|
| A | 单 Agent | 混合检索 |
| B | 单 Agent | 混合检索 + 关系扩展 |
| C | 双调查 Agent + 主控制器 | 混合检索 |
| D | 双调查 Agent + 主控制器 | 混合检索 + 关系扩展 |

四组使用相同工具、角色、知识、数据和父预算：12 次模型、12 次工具、256,000 预留 Token、90 秒。每任务三次，共 1,440 次，比较 B/A、C/A、D/B、D/C。

`scripts/agent_evaluation.py` 默认运行 native scripted controls：实际使用 Retriever、只读工具与预算，但决策和观察是确定性控制输入。控制通过不代表自然语言任务能力；120 条合同任务的完整语义和真实 embedding 质量仍需真实模型及人工审阅。集合标签为 author_contract_labels，独立人工相关集合状态为 pending，不能据此计算真实检索准确率。

独立 MCP stdio/HTTP、实际 PostgreSQL/Qdrant 解析与发布、图预览、浏览器报告与刷新、Kubernetes 只读观察作为 real-services 证据独立归档。合成模型和公开文档与企业资料、真实模型、真实语义 embedding 分开标记。

真实模型适配器通过 `--tier real-model --adapter <可信模块:函数>` 显式提供，须返回实际回答、结构化任务结果、model_id、控制结果、违规数和实际费用（usage.cost/cost_unit）。运行记录保存代码 hash、数据 hash、Prompt、工具和知识版本；适配器为受信任代码，不能来自模型文本或未审阅工具输出。

独立人工评审 JSONL 字段：strategy、case_id、repeat、review_kind=`independent-human`、reviewer、dataset_sha256、code_sha256、model_id、task_success、supported_citations、total_citations、critical_violations。`scripts/agent_evaluation_review.py --records <runs> --reviews <reviews> --output <report>` 校验完整矩阵、相同模型与代码、评审 provenance、费用单位与数值。

同一任务三次全部成功计稳定成功。完整矩阵关键权限/事实违规必须为 0，可回答知识任务引用支持率目标 ≥95%；复杂任务稳定成功率至少提升 5 个百分点，成功任务平均费用与 P95 耗时各 ≤1.5 倍基线。采用报告计算资格但不自动修改路由。未通过门槛的复杂策略继续作为实验交付。

当前 `evidence/evaluation-report.json` 中模型成功率、引用支持率、成功任务成本均为 null。检索层的独立相关标注、真实 PDF 语义、真实工具环境和真实模型结果需要另行验收。
