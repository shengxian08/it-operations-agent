# 技术对照实验（2026-10-02）

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../verification/README.md)。

本报告对应[选型复审](../architecture/technology-review.md)。所有实验在 `experiments/technology_review` 实现，原始产物在 `artifacts/technology-review`。正式应用、正式数据/索引、主依赖锁与模型配置没有迁移。参数与合成金标在运行前固定，未按留出集调整。

## 实验合同

| 项 | 固定条件 |
| --- | --- |
| 数据 | 人工编写的 82 篇合成 IT Markdown，82 chunks；60篇近似维护干扰文档；26查询：8 dev、18 holdout，其中留出15有答案、3无答案/无权访问 |
| 金标 | 单一明确来源，由样本事实预先指定；无答案与拒答质量分开，不把空 gold 算 Recall=0；ID与权限预检 |
| 身份 | corpus SHA256 `ef522ef95f528a80067c046d64763d83d85f0e9f8c9d7a8e0f4de23296ee1e86`；模型/输入/向量结果关联hash |
| Embedding | 真实 BGE `7999e1d3359715c523056ef9478215996d62a620`，512维、归一化、中文query指令，仅CPU |
| 模型运行 | 已有worker image `sha256:11943d9ed8d4036b954523ee0ec4e53d476d43695605aa430af5a62c7ba26ade`；只读缓存、禁网络、4CPU/6GiB；初次运行默认cwd加载镜像app，独立审查后以明确workdir/source guard补当前源码对比，保留原证据 |
| 召回 | 隔离 Qdrant1.15.4；每路40；应用内BM25；同角色过滤；整个并集重排；top5、每文档最多2 |
| 三臂 | baseline weighted；只换融合为应用层RRF（1-based rank，k=60，每路等权）；只换检索表示为source-heading context，其余同baseline |
| 重排 | 同一实际 `LexicalReranker`。生产最后平分无显式ID；实验以稳定chunkID打破完全平分，三臂同策略并在manifest声明 |
| 索引 | 新建 `technology_review_20261002a_baseline` / `technology_review_20261002a_heading_context`，各82点，同BGE身份；从未向已有collection写入 |
| 重复 | 真实BGE query batch三次；每查询检索三次；确定性排名与向量漂移核查；不伪装为随机答案模型多次评测 |

这是针对“标题承载型号/版本/错误码”的故障模式语料，包含刻意构造的反例；gold完整性只针对这些 authored probes。不能推论企业文档分布、普遍语义质量、答案正确率或生产 SLO。精确命令、语义改述、版本、权限和无答案病例分开保存。现有84条业务cases未被改写成新金标。

## 检索实测

留出集仅15个有答案查询进入相关性均值。

| 试验臂 | 融合后 Recall@5 | 融合后 MRR@5 | 最终 Recall@5 | 最终 MRR@5 | 最终 nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 当前 min-max 加权0.65/0.35 | 0.8667 | 0.7389 | 0.8667 | 0.7333 | 0.7662 |
| 只换为应用层RRF k=60 | 0.8000 | 0.6889 | 0.8000 | 0.6833 | 0.7128 |
| 只加入来源章节上下文 | 1.0000 | 1.0000 | 1.0000 | 0.9667 | 0.9754 |
| 同基线池，只换真实Qwen3重排 | 0.8667 | 0.7389 | 0.8667 | 0.8333 | 0.8421 |

每个answerable query只有一个相关文档，始终返回5个时Precision@5最高仅0.2；它不能解释成“准确率20%”。指标按document去重，完整逐查询列表与候选得分保存于 `retrieval.json`，不只给总分。

RRF在此固定试验中下降，尤其 PrintBox P100 E-17；保留现有加权。RoomBox B200 E-42 与 PrintBox P200 E-17 的关键型号/编号只在原文章节标题，当前分块丢弃后无法稳定找回；来源章节修复后找回。固定baseline候选并集有52–64个，其中这两例的gold根本不在池中，因此任何同池重排的Recall@5上限是13/15=0.8667；重排可以改善次序，不能恢复这两例。会议听不见讲话的改述在章节组仍排第2，MRR并未达到1。上下文组不是全部问题解决的证明。

所有三臂候选集合的角色过滤泄漏数为 **0**，admin/support私有文档在employee池中不可见。此实验固定来源状态，不模拟权限在远程调用中途变化；动态权限、历史快照与所有权由已有集成测试单独覆盖。无答案/无权访问的3个留出病例没有调用真实答案模型，因此**拒答、错误答案、引用正确性没有测出**。

本轮RRF位于应用内，因为BM25在Python；没有新增Qdrant sparse索引。Qdrant最新文档的可配置RRF k与加权RRF依赖更新服务版本，不能据客户端1.16.2宣称服务1.15.4支持。此k=60不是服务默认k=2。[官方融合接口](https://qdrant.tech/documentation/search/hybrid-queries/)

## 解析与语义单元

当前 PyMuPDF1.28.2的7个合成PDF：规则线框表格/普通文字 **2个接受**；合并、无框、带脚注页码的扫描、拼接扫描、支持表格混入无框表格 **5个明确拒绝**。规则表格原始headers/rows与truth完全相同，全部cell bbox非空且与已知绘制网格每条坐标误差≤1 point，page=1，chunk保留分钟、工作日、响应不等于解决时间。原始PDF和完整parser输出见 `synthetic-pdfs/` 与 `parsing.json`。这些是合成支持边界，不能证明任意PDF或真实故障文档质量。

| Markdown反例 | 当前800/120 | 同800上限的隔离原子原型 |
| --- | --- | --- |
| fence内有空行的短只读命令 | 形成3块，没有一块包含完整fence | 保留完整code span与原始起止范围 |
| 超800字符的一条命令 | 形成2块，任何块都没有完整命令 | 明确拒绝，要求人工整理；没有扩大输入预算或切断可执行文本 |

原子原型仅覆盖受限Markdown，未声称完整AST实现，也未接入主应用。对源章节抽取的研究函数明确假设检索语料没有fence内伪标题；正式化时必须先识别fence并处理标题、列表、表格、混合结构。

本轮BGE tokenizer对照：baseline文档最大 **71 tokens**，章节组最大 **90 tokens**，都未超过512；这仅覆盖短合成语料，不能把800字符等同于不截断。真实中文长表行、条件/脚注、代码与新增前缀仍需记录实际token上限和丢失位置。`token-audit.json`来自固定缓存tokenizer，未改变向量。

## 性能与一致性

| 微基准 | 实测 |
| --- | --- |
| CPU依赖导入/模型冷加载 | 1.964s / 6.509s；冷加载以已缓存权重为前提 |
| 82段baseline/context编码 | 0.567s / 0.569s，batch16；只是一批短合成文档 |
| 26query热批次 | 三次0.07046 / 0.07111 / 0.06747s，中位0.07046s |
| 单一固定query热编码 | 三次7.05 / 8.15 / 5.87ms，中位7.05ms |
| 编码进程峰值RSS | 648.25MiB，Linux`ru_maxrss`；不含整个Docker/数据库/主机 |
| 重复query向量最大绝对漂移 | 0.0；检索三次最终排名一致 |
| 加权/RRF/章节组检索中位 | 15.29 / 15.08 / 5.03ms（留出54个短测量/臂） |
| 加权/RRF/章节组检索P95 | 25.62 / 26.54 / 26.03ms；固定顺序、本机HTTP微基准 |

检索计时从预计算query vector开始，**排除在线embedding、模型生成、DB快照/API、OIDC、排队和前端**，不能作为端到端延迟。章节组中位更低可能受执行顺序/服务缓存影响，不能宣称表示变化导致更快；本轮性能用于记录预算，没有采用依据。CPU锁、短输入、单客户端与小语料不能证明500篇/10并发或GPU性能。

真实PG/Redis/Qdrant隔离环境重新运行事务/worker/知识出处测试：**39 passed、0 failures、0 errors、0 skipped，20.262s**。覆盖10并发同草稿只建1单、编辑换令牌/所有权、回滚后令牌仍可用、交接编号、取消/旧lease隔离、索引快照/权限/发布失败/旧版本出处。依赖是真实，embedding与答案模型在这些业务测试中是受控组件，不能拿这些39项替代真实模型质量。

只读业务探针重现：历史“上次那个工单”仍missing_ticket_number；只说“创建工单”仍产生泛化待确认草稿；“请转人工”仍被归为insufficient_evidence。此次选型评审没有改变这三处业务行为。技术替换不自动修复它们。

## 候选准备与尚未验证

初次只读缓存预检：Docling/MinerU未安装，BGE reranker/Qwen embedding/Qwen reranker固定权重均未缓存。原始状态保存在 `encoded.json`；该状态是**预检时间**，后续准备不回写历史。

Qwen3-Reranker-0.6B 已完成实际离线CPU对照：固定 revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`、ST5.7/Transformers5.18/torch2.14.1+cpu、F32、batch8/max_length512；每query完全复用baseline weighted候选并集，只改变重排模型。原始yes/no logit差来自固定Transformer+LogitScore/Identity模块，sigmoid单调转换单独保存，没有沿用lexical阈值或偷偷降级。实际加载成功证明此前“Qwen不能用CrossEncoder”的断言过时。

| Qwen候选成本 | 实测与口径 |
| --- | --- |
| 资产 | 13文件共1,207,486,774B，其中权重1,191,588,280B；下载245.97s，逐文件hash；无HF凭据、无文档上传 |
| 质量 | dev7例Recall/MRR均0.7143；holdout15例Recall0.8667、MRR0.8333、nDCG0.8421，MRR较词法+0.1000 |
| 全体评分 | 26查询、1482pairs，共539.07s；是批量全语料评分，不是单query延迟或P95 |
| 固定热查询 | 第一个预先固定留出query/test-mfa，54候选，三次19.291 / 18.767 / 18.075s，中位18.767s |
| 进程资源 | 峰值RSS3833.906MiB，4CPU/6GiB；不是CUDA、全栈内存或10并发容量 |
| 浮点重复 | 该query最大score漂移1.5259e-5；没有保存全26例的重复排序，不作全体重复排名声明 |
| 冷加载 | 3.961s，从Torch/ST导入后计时；与BGE cold字段口径不同，不直接比谁冷启动更快 |

该固定54候选的18.77秒超过文档的检索P95<2秒目标，但一个query的三次值本身**不是P95验收**。因此暂缓作为当前CPU默认方案；若缩小候选池、改变推理dtype/硬件，需要重新做单变量试验和遗漏回归，不能把本次质量提升直接嫁接到更快配置。两例缺候选仍无法靠重排修复。

BGE tokenizer审计不是Qwen pair/chat template的长度证据。Qwen固定chat配置与yes/no token已核验，实际max_length=512；长输入suffix/截断位置和阈值校准仍未验收。本次不评估真实答案、拒答或引用。候选模型缓存与结果在独立artifact目录；正式推理禁网络/只读根文件系统，主缓存/主配置不变。

Docling标准本地CPU路径已完成实际解析，结果如下。BGE reranker、Qwen embedding、MinerU仍未执行；不写模拟分数或猜测RSS。

## Docling标准本地CPU与同环境解析基线

候选固定Docling2.132.0、Heron+TableFormer accurate，`StandardPdfPipeline`、OCR关闭、remote services关闭；固定模型文件先核验hash，再断网推理。最终镜像`sha256:b7cbe6cd953f4e07948a6dc932924b3cfef749506c44be3e32a8dd5689fafa5e`，2CPU/4GiB、只读`/inputs`与`/models`，7PDF各首次setup+3次warm。基线另在相同本机Linux容器、2CPU/4GiB、当前源码下处理相同PDF，**7/7输入SHA一致**。依据`docling/comparison.json`和`pymupdf-linux/parsing.json`，不使用Windows的毫秒值计算Linux候选速度。

| 样本 | PyMuPDF状态；三次API中位s | Docling状态；三次warm中位s | 语义合同 |
| --- | --- | --- | --- |
| 规则线框表 | accepted；0.1060 | success；1.5249 | 九格文字、row/col关系、真实bbox、分钟/工作日/注释保留；四轮header flag均false，项目表头合同失败 |
| 普通文字 | accepted；0.0341 | success；0.8388 | 五句文字与页/bbox检查通过 |
| 合并单元格 | rejected；0.1085 | success；1.5451 | 仍属基线支持边界外，未验收为可自动接受 |
| 无框表格 | rejected；0.0399 | success；0.8391 | 同上 |
| 线框混入无框表格 | rejected；0.1359 | success；1.5492 | 同上 |
| 扫描含页脚 | rejected；0.0095 | success；0.8232 | OCR关闭，只余页脚不能算扫描内容提取成功 |
| 拼接扫描 | rejected；0.0117 | success；0.8353 | 同上 |

Docling converter **28/28 success、本样本0自动拒绝**。五类越界的`requires_review`是实验根据预先已知fixture合同作的人工评审标记，不是模型自动检测或已经实现的生产门禁。支持表的九个真实bbox为文本紧框，位于相应已知原格内（2point容差）；不同于PyMuPDF整个网格格子的坐标，不能要求两类框数值相等或据此推论任意文档定位。表级provenance有BOTTOMLEFT，cell有TOPLEFT，原字段和显式坐标转换均保存。没有根据truth补造header/cell/坐标。

首次规则表`convert()`7.7837s，包括懒加载模型；warm API计时排除Python/import、原页渲染、JSON序列化与整条ingest。Docling进程峰值RSS **1438.70MiB**、cgroup peak **1293.41MiB**；同Linux基线进程峰值 **235.99MiB**、cgroup peak **206.72MiB**。它们是不同内存度量，不用互相替代；两边memory.events均OOM=0。单页固定样本和上述API时延不代表长PDF吞吐、生产并发或端到端SLO。

固定输入的重复波动由原始三次计时直接计算；样本标准差采用`n-1`分母，不是置信区间或容量估计：

| 固定输入/API | 重复数 | 中位s | 最小–最大s | 样本标准差s |
| --- | ---: | ---: | ---: | ---: |
| Qwen重排，同一query/54候选 | 3 | 18.767472 | 18.074902–19.290720 | 0.609871 |
| Docling规则表，warm | 3 | 1.524949 | 1.513805–1.555145 | 0.021389 |
| PyMuPDF规则表 | 3 | 0.106033 | 0.104282–0.139029 | 0.019575 |
| Docling普通文字，warm | 3 | 0.838789 | 0.786913–0.844591 | 0.031758 |
| PyMuPDF普通文字 | 3 | 0.034098 | 0.033012–0.076109 | 0.024574 |

实际维护成本也留证：先遇到CUDA torchvision与CPU torch不匹配的`operator torchvision::nms does not exist`，在独立镜像只改成对应CPU wheel；首次正式转换又遇到OpenCV `libxcb.so.1`缺失，根据ldd补`libxcb1/libgl1/libglib2.0-0t64`。两次环境失败的原始日志、失败JSON及修正镜像保留，均不是解析质量失败；主venv、锁文件和业务镜像未改。

仅准备Heron/TableFormer资产 **384,434,788B**。Heron固定revision为`8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`，TableFormer `v2.3.0` resolved `fc0f2d45e2218ea24bce5045f58a389aed16dc23`；该旧TableFormer README只标CDLA-Permissive-2.0，最新卡的双许可声明不能冒认为选定旧README。精确许可及逐文件hash见[解析研究](research-parser-options.md)。

决策：**保留当前保守PyMuPDF，不把Docling默认输出直接替换生产解析器**。Docling有真实结构输出，但还需可验证的表头语义映射、未知布局/失败门禁、真实故障样本和完整RAG链验收；本次没有开启OCR，也不对候选OCR质量作胜负结论。

未验证事项：真实企业故障PDF与私有IT金标；真实答案/拒答/引用；新parser在真实布局的完整坐标/表头映射与拒绝门禁；真实模型长输入截断；候选安装/冷启动的目标Linux适配；容量、并发、预算/费用与外部告警送达。它们没有被标成已达成。

## 复现与证据

运行命令与隔离控制见 [实验README](../../experiments/technology_review/README.md)。原始证据：`corpus.json`、`encoded.json`（真实512维向量/输入/初始候选预检）、`retrieval.json`（每query候选/分数/排序/指标）、`token-audit.json`、`parsing.json`、`qwen-assets.json`、`qwen-reranker.json`、`business-and-provenance.xml/log`、`business-probes.json`。简化且带hash的[证据索引](technology-review-evidence.json)由 `evidence.py` 从实际结果生成。

独立审查发现首次BGE命令未指定workdir，Python从镜像cwd导入旧app；修正为 `--workdir /workspace --required-app-root /workspace/backend` 后实际重新编码。`current-code-encoded.json`记录源码路径与byte hash；`current-source-verification.json`逐项验证语料、模型、82chunk完整记录、两组输入和82+82+26全向量与原结果**完全一致**，三组max drift=0.0。当前源码byte hash与工作区核对一致。因此保留原检索/Qwen同池结果，原产物不覆盖；复现命令与source guard已经修正。

下一步采用条件在[模块决策](../architecture/technology-review.md)列明；本轮没有声明生产就绪或“最佳最前沿”排名。

## 本轮缺陷与最小处理

| ID / 优先级 / 状态 | 场景、实际与预期 | 根因、影响、最小处理与验收 |
| --- | --- | --- |
| TECH-CHUNK-001 / P1 / 主应用待实施，实验已复现 | RoomBox B200 E-42、PrintBox P200 E-17只在章节有型号/码；gold不在候选池。预期来源身份随chunk保留 | `backend/app/rag/ingest.py:chunk_markdown`丢弃孤立heading；模型无法重排池外证据。候选为来源型section路径/检索前缀，保持原文/citation不变；运行corpus→encode→retrieval复现，独立留出/权限/出处与新pipeline identity验收后才启用 |
| TECH-CHUNK-002 / P1 / 主应用待实施，原型已验证 | 短fence空行形成3块，长命令形成2块且无完整命令；预期完整语义单元或明确拒绝 | 同函数按空行/字符滑窗处理code；可能提供断裂命令。原子原型保持短code范围、同800上限超长拒绝；`parsing.json`与6项controls测试留证；正式化需Markdown结构解析及混合反例 |
| TECH-REPRO-001 / P2 / 已修复 | 只挂当前源码+PYTHONPATH，实际从镜像cwd导入app；预期当前源码 | Docker cwd优先的Python import；可使结果冒认工作区。README加workdir，encode加source guard；实际当前源码补验，82chunk/全部输入/190向量完全一致、drift0，源码hash匹配；原产物保留 |
| TECH-PERF-001 / P2 / 已修复 | 初始same-input-comparison时延字段来自Windows，预期同Linux基线 | 对照数据选错，可能夸大速度差。改指定pymupdf-linux文件/路径/SHA，compare_parsers也明确同输入校验；独立复核七项时延/文件hash一致；总报告只用Linux值 |
| TECH-ENV-001 / P2 / 隔离环境已修复 | Docling导入torchvision::nms失败，预期CPU依赖可导入 | CPU torch与默认CUDA torchvision不匹配；候选不能启动。仅独立镜像替换匹配CPU wheel；首失败日志不覆盖、断网预检exit0，无主锁修改 |
| TECH-ENV-002 / P2 / 隔离环境已修复 | Docling首次convert缺libxcb.so.1，预期标准模型管线可运行 | slim镜像缺OpenCV native libs；不是质量失败。依据ldd补三库；第二运行目录保留首失败，最终28次success/exit0/OOM0 |
| TECH-PDF-001 / P1 / 候选采用暂缓 | 九格文字/关系/坐标正确但header flag全false；预期可验证表头角色与未知布局失败 | 原生模型输出不等价于本项目表头/支持合同；converter success不能直接入索引。需要独立表头映射/风险门禁，不根据truth补猜；本轮四次一致反例与五类未自动拒绝留证，真实全链验收后再考虑替换 |

独立检索审查与独立PDF审查均完成；两项P2复现/对照口径问题修正后复核关闭。源heading/code业务缺口和Docling合同差异仍明确保留，不用实验修正冒认为主应用修复。共享对话要求的[逐项验收](technology-review-completion.md)与最终原始产物核对（本地证据：`artifacts/technology-review/final-verification.json`）另存，当前服务在线状态不由历史实验结果推定。
