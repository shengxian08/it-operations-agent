# 自查后的独立实施任务

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](verification/README.md)。

依据 [项目审查](project-audit.md) 与[共享对话](https://chatgpt.com/share/6abf375e-b318-83ea-bfbf-2556d572e59b)。按照 P0→P1→P2，每次完成一个可独立验收的任务。表中“待实现”保持为待办，不计作已完成；后续若改变行为，先补实际失败测试，再修改本表与项目审查。

| 任务 | 优先级/状态 | 关联发现 | 最小独立结果 | 验收与证据 |
|---|---|---|---|---|
| T00 真实人工交接提示 | P0 / 完成 | HANDOFF-001 | 无持久编号不宣称转交；有编号显示请求已记录与待处理，断流恢复保留编号 | Vitest 与两例真实浏览器渲染通过，有/无记录截图；不把待处理当实际接单 |
| T01 PDF 结构证据闭环 | P1 / 完成限定支持范围 | PDF-001～010 | 原单元格、上下文、原子行、实际PG/向量召回、完整模型证据、页表行/引用版本、旧平铺来源隔离、管理员表格预览 | 19实际PDF解析用例、21知识快照集成、全量287后端/27前端/18浏览器通过；合成与Mock标签保留，未替换库或自动OCR |
| T02 多轮查单与澄清 | P1 / 完成本机软件合同，目标环境未验 | QUERY-001 | 当前用户/会话受控对象持久化、缺号/多候选澄清、精确选择后重新鉴权；无必要人工/建单副作用 | [报告](architecture/ticket-lookup.md)、证据（本地证据：`artifacts/ticket-lookup/implementation-evidence.json`）：419后端/36前端/24浏览器全量通过，0 failures/errors/skipped；唯一独立审查4项Important已一次RED→GREEN修复，保留原始结论与作者验收；真实隔离PG/Redis/worker/工具/HTTP/SSE、角色降级/账号停用/对象删除/长历史/异常，Mock浏览器1440/390与刷新/切会话分开标注 |
| T03 必填故障信息收集 | P1 / 完成本机软件合同，目标环境未验 | CREATE-001 | 受控当前会话逐轮收集问题/影响/尝试事实，完整才签当前版本；旧草稿明确补全重签 | [报告](architecture/ticket-intake.md)、证据（本地证据：`artifacts/remaining-tasks/ticket-intake/implementation-evidence.json`）：466后端/40前端/26浏览器通过，0 failures/errors/skipped；实际PG/Redis worker、HTTP/SSE、确认事务及Mock浏览器分别留证 |
| T04 显式人工意图与上下文 | P1 / 完成本机软件合同，目标环境未验 | HANDOFF-002/003 | 明确人工动作直接路由；持久化有限服务器事实/员工原文/引用身份，当前权限读取明细与处理审计，有效处理人 | [报告](architecture/handoff-context.md)、证据（本地证据：`artifacts/remaining-tasks/handoff-context/implementation-evidence.json`）：513后端/43前端/28浏览器通过，0 failures/errors/skipped；36项人工图意图、11项真实依赖上下文、3项前端与1440/390 Mock HTTP新增用例；并发一次、回滚/恢复、旧行/省略、员工隔离、引用当前/原权限、停用处理人、处理后恢复。作者阶段验收；整个增量独立审查及作者修复见[最终审查](verification/final-review.md)；pending不冒认接单，无外部通知 |
| T05 工单最新处理进度 | P2功能/P1权限 / 完成本机软件合同，目标环境未验 | QUERY-002/003/004 | 最近可见评论/白名单审计及实际时间；公开/内部/旧未分类、全回放入口当前/原权限、锁后重新鉴权 | [报告](architecture/ticket-progress.md)、证据（本地证据：`artifacts/remaining-tasks/ticket-progress/implementation-evidence.json`）：537后端/46前端/30浏览器通过，0 failures/errors/skipped；24项实际依赖进度、3项前端与1440/390 Mock HTTP新增用例，空/多/内部/长内容、来源修改/删除/权限、幂等回放、并发/回滚/错误分别留证；作者阶段验收；独立审查及修复已完成，真实环境仍未验，见[最终审查](verification/final-review.md) |
| T06 真实 PDF 与模型质量 | P1 / 环境阻塞 | QUALITY-001 | 获得企业真实失败PDF与人工cell/页码/条件真值；在批准的真实模型/固定embedding revision下验证 | 原页→结构→chunk→向量/关键词→召回→模型上下文→答案/引用；独立完整相关集合计算Recall/引用质量/幻觉；保留不支持样本的失败原因；不得给Mock贴真实质量标签 |
| T07 目标环境发布门禁 | P1 / 环境阻塞 | OPS-001 | 按现有生产手册跑批准目标Linux环境；不扩大为新架构 | 真实身份全栈、新版PDF、30分钟容量/P95、完整空环境恢复与RPO/RTO、备份外部副本、实际receiver接收、同schema旧镜像回滚；镜像与报告来源一致 |
| T08 来源型 Markdown | P1 / 完成软件合同，私有质量未验 | MD-001～008 | 真实章节/source spans、完整原子代码、真实token预算、新旧版本/逐篇审批、管理员与员工预览 | [实施报告](architecture/markdown-structure.md)；真实PG/Qdrant、离线固定BGE与仓库33篇资料、Mock HTTP浏览器分开留证；私有文档/真实答案不计已验 |

T02 的“上一单”只能引用服务端受控会话对象，不能通过用户发来的任意 history 获得访问权；T04 持久引用只保存身份并在读取时重新鉴权；T05 则同时约束当前与原进度快照权限，并保持原始持久答案。业务对象均为工单。T03/T04/T05已分别完成作者本机软件合同；整个增量的一次独立审查发现5项Important，作者一次修复及最终615后端/46前端/30浏览器全量通过，0 failures/errors/skipped，原13项反例由作者重跑通过，没有第二轮独立批准。GitHub软件提交6aa3570已正常推送且616后端/46前端/31Mock浏览器/2真实协议E2E和镜像实际通过，见[交付记录](verification/github-delivery.md)；最终文档/小型metadata制品继续核对，T06/T07真实门禁保持未完成。

T06 只有实际失败样本证明现有布局策略不足后，才比较 Docling、Unstructured 或 OCR。比较维度是表头与单元格保真、条件/单位/注释、页表行来源、拒绝未知布局、CPU/内存/耗时、模型下载与离线部署；不要只比较输出字符数量。

旧版 PDF 切换策略属于 T01 已实现行为：原来源和旧索引保留，新索引排除无逐页出处的内容，管理员得到待重解析提示。同bytes重新上传/prepare后预览、核对、逐篇发布；未经核对不迁移、不删除、不填猜测页码。正式切换前在隔离库用企业样本验收并备份；这次没有替生产环境执行切换。

每项结束需要更新项目审查的当前行为、测试命令与证据分类；发现新问题添加独立编号。只有完成实际验收，才能把“待实现/未验证/环境阻塞”改为完成。

## Markdown 已复现与修复（T08）

| ID / 优先级 | 场景、实际/预期、根因与影响 | 最小修复及位置 | 复现与可执行验收 |
|---|---|---|---|
| MD-001 / P1 | 命令块内有空行；原实现按空行切成两块，缩进/命令关系丢失；预期原子完整且源范围匹配 | `rag/markdown.py` block/source map；`rag/ingest.py` 原子800上限，超限明确失败 | `test_fenced_command_with_blank_line_stays_one_complete_source_span` 及 fenced/indented/tilde/嵌套/超限用例；`unit-red.xml`→结构GREEN |
| MD-002 / P1 | 型号/错误码只在章节，代码内又有伪 `#`；原无来源章节，regex可认错title，预览与检索上下文分裂；预期只使用实际heading位置 | 同一解析结果供 chunk/preview，真实path供 dense/BM25/rerank；原文与前缀分开 | `tests/unit/test_markdown_structure.py`、`test_markdown_versions.py`；固定BGE82块输入逐项相等，原holdout未修改 |
| MD-003 / P1 | >240字含配置代码召回后被归一化/截断，模型无法获得完整操作；预期 actual prompt 保留空行/缩进/完整块 | `rag/retriever.py::_to_hit` 及 `agent/graph.py::_answer_prompt` 按新身份携带raw/path/spans | `test_new_code_evidence_reaches_model_prompt_without_normalization_or_truncation` RED→GREEN；`real-bge-final.json.actual_prompt_proof` |
| MD-004 / P1 | 完整标题+章节+原文超512 tokens，原adapter会让模型截断；预期在collection/指针副作用前拒绝并保留可操作失败 | `SentenceTransformerEmbedder.token_counts`、`KnowledgeService._create_revision/_process_index_claim`、`AdminPage` 提示 | 512/513/较小模型上限；真实613-token输入监测 encode未调用；真实依赖 budget-index RED→GREEN |
| MD-005 / P1 | 旧source/ready job无结构身份；自动套新版会冒认管理员核对，历史搜索与引用失真；预期保留v1/NULL、新版本排除并逐篇复核 | 可空后继迁移、严格v1/v2 manifest、source/job_requires_reparse、CLI同hashprepare、UI禁止旧ready发布 | `test_markdown_versions.py` 旧source/引用/rollback/samehash/审批/三角色拒绝及旧job409；没有改旧快照 |
| MD-006 / P2 | 超2M字符文件先进入结构解析才检查大小，新增解析可消耗不必要资源；预期先检查既有硬上限 | `knowledge_parser.extract_document` 将大小/空文检查置于共享结构解析前 | `test_extraction_checks_size_before_structural_parser` 1实际失败→26结构单测通过；原限制未扩大 |
| MD-007 / P2 | 独立审查复现：4空格或`>`后的伪closer是代码内容，宽松正则却认为闭合；预期明确失败 | `markdown._fence_with_closure` 记录CommonMark规则实际body_end/closer消费；不猜容器缩进 | `test_commonmark_code_content_cannot_masquerade_as_fence_closer` 8实际失败；合法混合嵌套8例继续通过；review-red→green |
| MD-008 / P2 | 独立审查复现：共享HybridRetriever重排丢掉heading中的E-42，实际score0被阈值过滤；预期与dense/BM25同源 | `HybridRetriever.retrieve` reranker统一`chunk_search_text`，旧无结构回退title+raw | `test_hybrid_reranking_uses_source_context_and_preserves_legacy_raw` 新结构RED→GREEN、旧结构保持；citation只raw、原阈值0.35未改 |

Markdown 场景的 Mock tokenizer/HTTP、真实公开BGE、仓库seed与合成corpus分别标明；软件合同完成不意味着私有质量完成。正式迁移、目录同步、生产来源发布均未执行。


## 最终审查修复

R1知识回放权限与全部prompt来源、R2交接锁等待期间撤权、R3无可信来源的历史助手文本、R4普通取消及引文保留、R5完整收集/执行报告，5项Important已由作者一次复现修复并最终全量通过。问题合同、原审查分类与可执行验收见[最终审查](verification/final-review.md)；公开615/46/30报告hash见[软件摘要](verification/software-validation.json)。原ready: No与失败报告留存，没有第二轮独立批准。GitHub实际CI/production-live/commit镜像已按[交付记录](verification/github-delivery.md)分别执行通过；T06/T07真实门禁继续未验。
