# 企业 IT 助手技术选型复审

审查日期：2026-10-02。依据：[用户指定的共享对话](https://chatgpt.com/share/6abf55eb-56f8-83e9-ad6c-4a295bdb43ec)、[工作区规则](../../AGENTS.md)、[四路径审查](../project-audit.md)、[现有待办](../improvement-backlog.md)。本轮范围是**技术选型、可执行隔离实验与模块决策**；未迁移正式应用、数据库、索引或模型配置。

## 结论与证据边界

主组合保留 **FastAPI + 单个 LangGraph 流程 + PostgreSQL 业务事务/运行台账 + Qdrant 不可变知识版本 + 应用内 BM25 + 固定 BGE small 中文 embedding**。目前没有“整体更换技术栈”的证据，也没有依据宣称当前组合在所有维度最优。优先改进文档表示和业务状态，比默认增加 Agent、GraphRAG 或更大模型更有直接依据。

在运行前固定的合成 IT 探针中，当前加权融合的留出集 Recall@5 为 **0.8667**，应用内 RRF 为 **0.8000**；保留来源章节上下文为 **1.0000**。最终词法重排后的 MRR@5 分别为 **0.7333 / 0.6833 / 0.9667**。章节组只改变检索表示，原文、分块边界、BGE、权限、融合与重排函数保持相同。此语料有意覆盖“关键型号/错误码只在标题中”的故障，不是企业真实分布，不能把 100% 当上线质量。原始结果和控制条件见[对照报告](../experiments/technology-comparison.md)。

真实 Qwen3-Reranker-0.6B 同池重排把留出 MRR@5 提升到 **0.8333**，Recall仍是0.8667；54候选的固定查询三次热运行中位 **18.77s**、峰值进程RSS **3833.91MiB**。它满足实际可加载接口，但没有达到当前CPU部署的默认采用条件。Docling也已完成同字节PDF、Linux 2CPU/4GiB对照：28次转换成功，规则表九格文字与坐标保留，但九格`column_header`均false；五类越界样本没有自动拒绝。因此保留现有PDF基线，候选表头映射与失败门禁需另验收。官方接口可用、字段存在、权重可下载和本地合成成功均不替代真实企业 PDF → 原页/cell → chunk → 实际模型上下文 → 答案/引用验收。

## 现状：以当前工作区为准

“生效”指正式实现路径与配置选择，不表示生产实例已部署。运行环境、部署样例和 Mock 分别记录。

| 模块 | 当前实现与代码证据 | 状态与限制 |
| --- | --- | --- |
| API/数据 | FastAPI 0.142.2、SQLAlchemy 2.1.1；[runtime.py](../../backend/app/runtime.py)、[业务模型](../../backend/app/production/business_models.py) | 正式路径已实现；PG 隔离实测；目标 Linux 发布未验收 |
| 员工/支持前端 | React18.3.1、Vite6.4.3、TypeScript5.9.3，固定[package-lock](../../frontend/package-lock.json)；[生产界面](../../frontend/src/production/ProductionApp.tsx)与typed API | 当前正式入口与业务状态实现；此前27单测/18浏览器测试是历史证据，本轮未改前端，不将其Mock链路当真实模型质量 |
| PDF | PyMuPDF 1.28.2；[pdf.py](../../backend/app/rag/pdf.py) 使用严格线框表格、复制页/表/行/cell 坐标、旋转归零并保留原角 | 普通文字与规则单层表头支持；扫描、合并、多栏、无框、跨页等保守拒绝；真实故障 PDF 未验证 |
| 文档表示 | PDF sections/context/tables/cells；[KnowledgeChunk](../../backend/app/rag/ingest.py) 有版本、路径、字符范围、权限；[RevisionChunk](../../backend/app/production/knowledge_models.py) 保留版本快照 | 部分结构已实现；Markdown section path/type/父子关系没有统一合同；并非完整 DoclingDocument 等价物 |
| 分块 | `chunk_markdown` 800 字符、120 重叠；PDF 表格行携带页/表/行与完整上下文，超过上限失败 | 表格行原子化生效；孤立标题被丢弃、空行会拆 code fence、长命令被切断，本轮稳定复现 |
| Embedding | 正式 `build_embedder` 仅允许 `BAAI/bge-small-zh-v1.5`、固定 revision、512 维；[knowledge.py](../../backend/app/production/knowledge.py) | 正式配置选择语义模型；本轮真实 CPU 离线验证；默认开发环境 hash embedding 是测试用途 |
| 词法检索 | `rank-bm25` 0.2.2，应用内 BM25；[tokenize](../../backend/app/rag/ingest.py) 为中文字/双字及 Latin token | 生效；标点、版本号、完整命令的语义单元仍需独立检验；没有 Qdrant sparse BM25 索引 |
| 融合 | 每路 40，逐路 min-max 后 0.65 dense + 0.35 BM25；[RevisionRetriever](../../backend/app/production/knowledge.py) | 生效；不是 RRF；当前整个并集都进入词法重排，融合还承担平分时排序 |
| 重排 | 正式 `RevisionRetriever` 固定 `LexicalReranker`；[retriever.py](../../backend/app/rag/retriever.py) 另有 `SentenceTransformerReranker` | 神经 adapter 类已定义但正式路径未启用；类缺少完整模型 revision/设备/长度/得分合同 |
| 模型与引用 | [provider](../../backend/app/llm/providers.py)、[graph.py](../../backend/app/agent/graph.py)；生产 runtime 要求结构化 citation JSON 并核对引用 | OpenAI-compatible 路径已实现；默认 Mock。当前是提示+事后 JSON/citation 检验，不能冒称所有 provider 都启用原生 strict tool schema；真实答案质量未验证 |
| 路由/上下文 | LangGraph 1.2.12，规则路由知识/查单/建单；历史加载见 [runs.py](../../backend/app/production/runs.py) | 生效；“上次那张单”未解析、低信息建单生成泛化草稿、显式转人工原因未单独路由，本轮只读探针复现 |
| 业务读取 | `ProductionTicketService.lookup_ticket` 按可信 user/当前角色重新查表；固定业务方法，没有自由 SQL 工具 | 生效；所有权/支持范围实测，模型不能授权或猜测结果 |
| 确认与写入 | [business.py](../../backend/app/production/business.py)：PG 草稿版本、令牌 hash、行锁/事务锁、唯一约束、确认记录/工单/审计同事务 | 生效且隔离实测。当前令牌权威存储已经在 PG，不能沿用对话中“Redis token + PG 双写”的旧推断 |
| 持久运行 | PG run/event/budget/job/lease；[worker.py](../../backend/app/production/worker.py) 独立消费、取消隔离、失效 lease 围栏 | 生效；graph `compile(name=...)` **未传 checkpointer**。自建运行台账不是 LangGraph 节点级 checkpoint/resume |
| 人工交接 | `Escalation` 有编号、run/会话/用户/原因；支持端队列和状态 API，失败恢复同事务兜底 | 持久化与 operator 状态已实现；待处理不等于接单；完整用户问题/已尝试/证据快照上下文缺口仍在 |
| 缓存/部署/观测 | 32 个 revision/access BM25 cache；批量 embedding；worker/API 分离；[compose.prod.yml](../../compose.prod.yml)、监控/验收脚本 | 配置和局部测试存在；完整告警接收、容量、灾备、真实身份/模型、目标 Linux 仍未验收 |
| 评测 | 现有 84 条 cases、本轮固定 synthetic corpus、原始 JUnit/向量/解析产物 | 现有 cases 不等于完整私有检索金标；Mock 浏览器与确定性模型测试不能代表实际答案正确性 |

核心锁依赖由 `backend/uv.lock` 核对，worker 实际安装的 ST 5.7.0、Transformers 5.18.0、torch 2.14.1+cpu 与 qdrant-client 1.16.2 在本轮容器核实。服务端 Qdrant **1.15.4**、PG **16** 来自隔离服务与部署声明；客户端更新不能使旧服务支持所有新 API。

## 使用约束与未知

| 约束 | 当前证据 | 决策影响 |
| --- | --- | --- |
| 文档/语言 | 本地中文 IT Markdown、PDF；知识要求逐页/cell 出处、命令/版本/型号/错误码准确 | 文档语义完整性先于排行榜；扫描/复杂表格不得默认为已支持 |
| 规模 | 部署文档声明单企业 100 员工、10 并发、500 篇；不是实测容量 | 先在目标规模验收，不默认引入分布式检索/多 Agent |
| 目标硬件 | 8 vCPU/16 GB/100 GB Linux；worker 4 CPU/6 GB；扩容需要重新验收 | CPU 基线优先；大模型常驻、长 token pair 成本必须另测 |
| 本机实测 | Windows、16 逻辑处理器、约 15.8 GiB RAM；RTX 4050 Laptop 6141 MiB，Docker WSL2实际可用7.654GiB；worker Python 3.11.16 CPU torch | GPU 存在不等于当前镜像能用 CUDA，也不代表 8B/VL 可满足预算；本机不能替代目标 Linux。候选推理串行以避免内存争用 |
| 延迟/并发 | 验收文档规定1800秒/10并发/500文档、读接口P95<0.5s、检索<2s、回答端到端<30s；本轮只是微基准，目标未验收 | 不把 120 秒 run timeout 写成用户延迟目标，不把单查询秒数承诺为 10 并发能力 |
| 费用/真实答案 | 模型 endpoint/name/prices/budget 样例未获实际运行证据，0 预算生产校验拒绝 | 不选择未经真实费用/质量验收的答案模型，不调用付费 API |
| 更新与恢复 | 知识版本不可变、切换/回滚可测；验收目标RPO≤24h/RTO≤4h尚未完成；文档更新频率、版本保留策略未知 | 新 parser/embedding 必须独立 revision，不能覆盖旧证据或混向量 |
| 隐私/许可 | 文档不得上传；本轮只处理本地合成资料；PyMuPDF 采用 AGPL/商业中的哪一路未有企业依据 | 候选下载与推理分离、推理断网；代码和每个权重许可独立归档 |
| 运维/团队 | 单机部署文档存在；实际团队、人力与允许新增服务数量未知 | 不假设无限 GPU、预算、维护能力 |

## 各模块决策：每模块最多两个候选

| 模块 | 基线；候选与拟解决问题 | 本轮证据/代价 | 决策与采用/回滚条件 |
| --- | --- | --- | --- |
| PDF | 当前 PyMuPDF；① Docling 标准本地 PDF pipeline ② MinerU 固定 4.0.10 档位 | 同Linux 7PDF基线2接受/5拒绝；Docling 28次success，九格坐标/文字保留但header flag失败，越界未自动拒绝；MinerU未实测 | **保留基线，Docling直接替换暂缓**。候选映射与失败门禁、真实失败样本逐cell/页/单位/注释、资源/离线/许可通过才按篇新revision；旧parser/revision可切回 |
| 统一表示 | 当前 chunk + PDF sections；① 增补来源型 section/type/raw-span/cells/quality/error/parser-version 合同 | 原页/cell 已有，Markdown heading/code 无统一合同 | **调整候选**。原文/坐标须来自 parser，允许空值/未知，禁止补猜页码/cell；旧快照不冒认新 parser |
| 分块 | 当前 800/120；① Markdown AST/受限结构原子块 ② 有预算且权限复核的父段扩展 | 空行 fence 与 998 字符命令被切断；隔离原子原型保留短 fence，超限拒绝而非拆执行文本 | **优先调整**。同预算下保存完整 command/config，超限进入人工整理；父段不能扩大授权范围。任何出处/拒绝回归切回旧 revision |
| 检索上下文 | 当前 title+raw；① 来源章节/型号/版本前缀 | 同边界/模型的留出 Recall 0.8667→1.0000；针对性合成，非私有质量 | **首个实施候选**。按真实 source headings 构建，原文与 citation 分开保存；token 长度/权限/真实语料通过才启用新 pipeline identity |
| 表格数值/汇总 | 当前按完整表行检索回答；① 对已核实cells的确定性筛选/计算工具 | 当前没有跨全表求和/计数专用工具与真实汇总场景金标；top5行不等于全表 | **条件增强**。出现汇总需求时明确表版本、单位、筛选与完整行范围后由工具算，不让模型凭top-k估计总数；原cells不完整则失败 |
| Embedding | 固定 BGE small zh；① Qwen3-Embedding-0.6B | BGE 真实离线 512 维；Qwen 默认1024、MRL不等于同语义空间，更多权重/RAM/重建成本 | **保留 BGE，Qwen 暂缓**。独立 collection、正确 query prompt/pooling/维度与私有质量/CPU成本胜出后再迁移，旧指针可回滚 |
| BM25 | 中文字/双字、Latin token；① 保留精确型号/命令/版本的辅助词项 ② 独立中文分词器 | 关键 heading 被丢弃时换分词也不能找回；本轮没有调 tokenizer | **先保留**。精确标点语义失败有金标后独立改变 tokenizer，再测 build/RSS/更新/权限，不同时改模型 |
| 融合 | 0.65/0.35 min-max；① 应用内 RRF ② DBSF | 固定 k=60 的 RRF 留出下降；当前重排并集，tie-break 很重要 | **保留当前加权**。不同分布真实留出稳定改善才改；不以“RRF 流行”升级。DBSF 需原始分数分布验证，服务/API契约单列 |
| 重排 | lexical-v1；① BGE-reranker-v2-m3 ② Qwen3-Reranker-0.6B | Qwen真实同池MRR+0.1000，但54候选热中位18.77s、RSS3833.91MiB；BGE reranker未运行 | **保留词法，Qwen当前CPU默认方案暂缓**。小候选池/合适推理硬件须另做单变量与质量回归；CPU P95/RSS、长输入后缀、阈值校准与真实金标同时通过才启用；超时须明确故障 |
| 答案/引用 | 已批准 provider+事后 JSON 校验；① provider 支持的原生结构化输出 | 当前没有真实答案 API 实测；schema 只约束形状，不授权建单/查单 | **保留并待验收**。provider 能力和 token/费用/超时/引用实测后启用结构输出；拒绝、schema错误、无证据均保持受控失败 |
| 路由/澄清 | 当前单 graph 规则；① 显式缺槽/待澄清/人工请求状态 | 三条只读探针暴露业务缺口；与换 LLM/框架没有因果证据 | **业务优先调整，下一独立任务另行实现**。历史对象候选必须重新鉴权；空问题不得生成正式可确认草稿 |
| 业务 query/write | 固定 service 方法、Pydantic 参数、PG事务；① 必要时原生 typed tool 调用 | 39 测试覆盖并发、版本、回滚、scope、lease、出处；模型不执行写库 | **保留**。无自由 SQL；可信身份从认证上下文注入，严格参数不能替代授权；任何替换须同等 race/rollback 证据 |
| 持久运行 | PG run/event/lease；① LangGraph PG checkpointer ② Temporal | 当前有任务级恢复/终结，无节点级 checkpoint；短图无需默认长流程平台 | **保留台账，候选暂缓**。需要跨节点长等待恢复时才加 checkpoint；跨服务补偿/长流程才评 Temporal，业务幂等仍留 PG |
| 人工交接 | Escalation 持久编号+支持状态；① 不可变上下文快照 ② 外部通知需要时 transactional outbox | 队列/编号已实现，上下文包不完整；目前无实际外部双写需求 | **先补上下文，outbox 条件引入**。用户可见待处理/接单/失败真实状态；通知只在有外部可靠送达需求时测重复、回滚、投递失败 |
| 缓存/异步/运维 | bounded BM25 cache、PG index-job、独立 worker、现有监控 | 数据与权限key已包含revision；目标容量与告警仍未知 | **保留并验收**。先测500文档/10并发与真实更新，再按证据分离解析worker/缓存服务，不默认新增集群 |
| 前端 | 当前React/Vite/typed API；本轮无已证明必要的替代框架 | 新旧业务状态与权限由已有UI/接口测试约束；技术检索试验不要求换UI框架 | **保留**。新增澄清/交接上下文时先定义员工与支持状态，再做相关渲染/交互回归，不以版本新旧推动迁移 |

## 官方事实更正与版本风险

已独立核验 PyMuPDF **1.28.2（2026-08-06）**、Docling **2.132.0（2026-10-01）**、MinerU **4.0.10（2026-09-29）**。代码、模型与安装接口详见[解析研究](../experiments/research-parser-options.md)。Docling 代码 MIT 与各权重许可分开；MinerU 4.0.10 是 Apache-2.0 基础加附加商业与第三方在线服务标识条款，不能写成纯 Apache 或照搬旧 AGPL。PyMuPDF 企业许可依据保持未核实。[Docling release](https://github.com/docling-project/docling/releases/tag/v2.132.0)、[MinerU 版本许可](https://raw.githubusercontent.com/opendatalab/MinerU/mineru-4.0.10-released/LICENSE.md)、[PyMuPDF 许可](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)

Qwen3-Reranker-0.6B 官方模型卡如今提供 `CrossEncoder.predict`。其固定 revision `e61197ed45024b0ed8a2d74b80b4d909f1255473` 带有 ST 的生成式 LogitScore 适配；ST 5.6 修复截断丢失 chat suffix 的错误。项目 5.7 在该修复之后，但原始 logit 差仍不能套用 lexical 的 0.35 证据阈值。旧断言“Qwen 无法使用 CrossEncoder”需要更正。全部模型/权重固定身份、许可、文件体积、设备与 prompt 见[检索研究](../experiments/research-retrieval-options.md)。[Qwen 官方卡](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)、[ST 5.6 release](https://github.com/huggingface/sentence-transformers/releases/tag/v5.6.0)

LangGraph checkpointer 保存图状态，当前图未接入；恢复节点前的副作用仍可能重跑，因此不得把 graph checkpoint 当建单幂等。现有 PG 草稿确认和事务是权威。`interrupt` 提供暂停/恢复机制，也不等于支持人员接单队列。[官方 persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[官方 interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

Outbox 解决数据库与外部通知的双写一致性，需要投递器与消费者幂等；当前仅 PG 内建单/交接没有必要为框架先进性增加它。PG 唯一约束与事务继续保护业务记录。[AWS outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)、[PostgreSQL 16 constraints](https://www.postgresql.org/docs/16/ddl-constraints.html)

## 主组合、条件增强与暂不采用

**主组合**：单图业务路由、受限业务 service、PG事务/台账、版本化 Qdrant、当前 BGE + BM25 + 加权融合 + lexical；PDF维持保守支持边界。它与当前 CPU/单机规模、权限和审计目标一致，真实生产验收仍是独立事项。

**条件增强**：先做来源型章节与原子 command/config；Docling先验证可靠表头映射、原页/cell与失败门禁，再按篇引入；神经重排只有真实金标和CPU成本都达标才启用；父段扩展、精确词项、provider原生结构输出按失败证据逐项测；外部可靠通知才加 outbox。

**暂不采用**：Multi-Agent 没有四条短业务路径的必要性证据；GraphRAG 没有跨文档关系/全局汇总金标；MCP 没有具体外部系统互通需求；Temporal 没有跨服务长等待补偿需求；VL/late-interaction/4B/8B 模型没有视觉金标、存储/显存/延迟预算。这些技术不自动解决标题丢失、低信息草稿、权限或交接上下文。重启条件及官方资料见[检索研究暂缓表](../experiments/research-retrieval-options.md)。

## 下一独立任务

**来源型 Markdown 分块合同**：先用本轮两个命令反例和 heading-only 型号/错误码样本建立失败测试；实现 section path/type/原始范围，完整 code/config 原子块，同一800字符上限下超限明确要求整理；搜索前缀来自真实标题而非 LLM 猜测。新 pipeline identity 建独立 revision，旧 revision/引用保持可访问；管理员预览后逐篇发布。验收包含原文范围、fence/命令不拆、512-token上限审计、员工/支持/管理员过滤、旧快照引用、真实获准文档与质量留出集。失败则回切旧指针，不能补猜出处或降阈值。

本轮只实现实验原型，不把这个候选直接写入主应用。业务 T02/T03/T04、真实文档/答案 T06、目标 Linux T07 仍按[已有待办](../improvement-backlog.md)分别处理。
