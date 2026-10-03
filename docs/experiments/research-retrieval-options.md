# 检索选型复审：官方事实与隔离实验边界

查询日期：2026-10-02。本文保留初次官方资料研究的时间口径：当时尚未下载候选权重、安装依赖、调用模型或访问数据库。后续已完成真实 BGE 编码、应用内 RRF/来源章节对照与固定 revision 的 Qwen3-Reranker-0.6B 离线 CPU 实验，实际结果以[对照报告](technology-comparison.md)及[证据索引](technology-review-evidence.json)为准；本文件中的“当时未运行”不代表最终实验状态。私有 IT 质量、真实答案/引用、吞吐与目标部署容量仍未验证。

本轮保留 BGE embedding 与 `lexical-v1` 为业务基线。Embedding 只增加 `Qwen3-Embedding-0.6B` 一个比较对象；神经重排只保留 `bge-reranker-v2-m3` 与 `Qwen3-Reranker-0.6B` 两个候选。融合优先做应用内 RRF 隔离实验，DBSF 留作有原始得分分布证据后的第二种方法。候选用于知识问答的证据排序，不能替代查单鉴权、当前草稿确认或人工交接持久化回执。

## 当前实现与适配位置

- `backend/app/production/knowledge.py:47` 的正式 embedder 仅接受 BGE 模型及 40 位 revision；`:84` 的 pipeline identity 固定 512 维默认配置、归一化、中文查询指令、词法重排与 0.65/0.35 融合。更换 embedding 必须在独立索引实验中进行，即使新模型输出同样为 512 维也不能复用原语义空间。
- `backend/app/production/knowledge.py:608` 的正式检索从向量和应用内 BM25 各取最多 40 个候选，逐路 min-max 后加权，随后调用 `LexicalReranker`，按重排分数排序、用融合分数打破平局；返回前重新检查当前来源权限。它尚未接入神经重排或 Qdrant sparse BM25。
- `backend/app/rag/retriever.py:86` 的 `SentenceTransformerReranker` 使用 `CrossEncoder(model_name).predict([(query, document), ...])`，只传模型名，没有固定 revision、设备、离线、token 上限或分数转换参数；正式 `RevisionRetriever` 未调用它。
- 当前 `backend/uv.lock` 记录 Sentence Transformers 5.7.0、Transformers 5.18.0、PyTorch 2.14.1+cpu 与 qdrant-client 1.16.2。`backend/pyproject.toml` 的 torch 源明确为 CPU 索引。锁文件版本不等于本机已安装或真实模型已验收；现有集成检索测试使用 `DeterministicEmbedder` 和词法重排。
- `backend/app/agent/graph.py:131` 用 `hit.score` 与证据阈值比较。候选重排分数范围不同，不能把当前 `minimum_evidence_score=0.35` 原样当作各模型的可靠证据阈值。

## 模型身份、发布日期与许可证

“发布日期”采用官方发布公告；“revision 日期”是仓库更新日期，不表示重新训练。以下 revision 是初次研究时核查到的固定身份；后续 Qwen 重排已按其中固定 revision 准备完整文件并运行，BGE 重排与 Qwen embedding 仍未执行。

| 模块 | 模型/基线 | 发布日期；固定 revision | 许可证与输出 | 官方证据 |
| --- | --- | --- | --- | --- |
| Embedding 基线 | `BAAI/bge-small-zh-v1.5` | v1.5：2023-09-12；`7999e1d3359715c523056ef9478215996d62a620`，2023-10-12 的 README 更新 | MIT；512 维 | [官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)、[固定提交](https://huggingface.co/BAAI/bge-small-zh-v1.5/commit/7999e1d3359715c523056ef9478215996d62a620) |
| Embedding 比较对象 | `Qwen/Qwen3-Embedding-0.6B` | 2025-06-05；`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，2026-04-20（README 更新） | Apache-2.0；默认/最大 1024 维，MRL 支持 32–1024 维 | [官方模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[官方元数据](https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B)、[发布公告](https://qwenlm.github.io/blog/qwen3-embedding/) |
| Reranker 基线 | 项目 `LexicalReranker` / `lexical-v1` | 当前工作区实现；无模型 revision | token 覆盖、短语、编号、标题规则产生标量；无模型权重 | `backend/app/rag/retriever.py:52` |
| Reranker 候选 1 | `BAAI/bge-reranker-v2-m3` | 2024-03-18；`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`，2024-06-24 | **模型为 Apache-2.0**；输出相关性标量，不输出 embedding | [官方发布记录](https://github.com/FlagOpen/FlagEmbedding/blob/master/README.md?plain=1)、[模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3)、[固定提交](https://huggingface.co/BAAI/bge-reranker-v2-m3/commit/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e) |
| Reranker 候选 2 | `Qwen/Qwen3-Reranker-0.6B` | 2025-06-05；`e61197ed45024b0ed8a2d74b80b4d909f1255473`，2026-04-16 的 ST 适配更新 | Apache-2.0；输出相关性标量，不输出 embedding | [发布公告](https://qwenlm.github.io/blog/qwen3-embedding/)、[官方元数据](https://huggingface.co/api/models/Qwen/Qwen3-Reranker-0.6B)、[适配提交](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/commit/e61197ed45024b0ed8a2d74b80b4d909f1255473) |

FlagEmbedding 库的 MIT 许可证与 `bge-reranker-v2-m3` 模型卡的 Apache-2.0 应分别记录，不能用库许可证覆盖模型许可证。上述开源权重可用于本地推理；是否满足企业采购、归档与分发要求仍由企业的模型资产流程核对。

## 实际接口与得分含义

**BGE embedding** 的 ST 配置是 CLS pooling、512 维与 `max_seq_length=512`；“512 维”与“512 token”是不同约束。当前中文指令仅加在 query，文档保持原文并归一化，符合官方检索用法。800 字符 chunk 不能自动视为小于 512 token，实验应记录 token 截断是否丢失表头、单位、条件或注释。[Pooling 配置](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/main/1_Pooling/config.json)、[长度配置](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/main/sentence_bert_config.json)、[官方用法](https://huggingface.co/BAAI/bge-small-zh-v1.5)

**Qwen embedding** 可以通过 `SentenceTransformer` 加载。官方示例在 query 侧使用 `prompt_name="query"`，document 不加任务指令；Transformers 路径使用末 token pooling、归一化，要求 Transformers ≥4.51.0。现有通用 adapter 对 Qwen 不会自动选择该 query prompt，正式 model allowlist 与 identity 也会拒绝它，因此只改配置无法得到等价实现。若实验选择 512 维 MRL，需固定截断维度与归一化顺序；1024 维另做索引，不能混查。[Qwen 官方代码与用法](https://github.com/QwenLM/Qwen3-Embedding)

**BGE reranker** 是 `XLMRobertaForSequenceClassification`、单标签分类头，官方用 query/passage pair 得到 logit，可经 sigmoid 映射到 0–1。它是常规 CrossEncoder 的合适候选，ST 官方预训练列表也列出该模型；因此现有 `predict(pairs)` 的形状契约在文档层面兼容，真实加载仍未验证。配置的 `max_position_embeddings` 为 8194，但官方普通推理示例明确设为 512；实验必须固定实际 pair 的 `max_length`，不能把位置配置上限当作当前运行值。[模型配置](https://huggingface.co/BAAI/bge-reranker-v2-m3/blob/main/config.json)、[官方推理用法](https://huggingface.co/BAAI/bge-reranker-v2-m3)、[ST CrossEncoder 模型列表](https://www.sbert.net/docs/cross_encoder/pretrained_models.html)

**Qwen reranker** 的底层是 `AutoModelForCausalLM`。官方 Transformers 路径组合任务指令、query、document 与固定 chat prefix/suffix，在最后位置取 yes/no logits；0–1 分数为 `sigmoid(logit_yes - logit_no)`。它不是任意 `AutoModelForSequenceClassification` 换名即可工作的模型，也不需要生成长答案来重排。[Qwen reranker 官方模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)

截至查询日，官方模型卡已经给出 `CrossEncoder("Qwen/Qwen3-Reranker-0.6B").predict(pairs)`。ST **5.4** 引入 `Transformer(text-generation) + LogitScore`，使这种生成式模型可经同一个 CrossEncoder API 调用；Qwen 的 2026-04-16 revision 携带适配配置。该路径默认返回 **yes/no 原始 logit 差**，官方示例需显式 sigmoid 才得 0–1。所以“Qwen 永远不能用现有 CrossEncoder API”已过时，但“任意旧版本库和旧 revision 都能直接替换”也不成立。[ST 5.4.0 发行说明](https://github.com/huggingface/sentence-transformers/releases/tag/v5.4.0)、[Qwen 适配提交](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/commit/e61197ed45024b0ed8a2d74b80b4d909f1255473)

ST **5.6.0** 修复了长输入截断把 chat template suffix 丢掉、导致 Qwen reranker 等模型静默输出错误分数的问题。本项目锁定的 5.7.0 在该修复之后；实验应固定该锁版本并核查 suffix 与截断，不能只验收“≥5.4 可调用”。即使转成 0–1，也只是模型打分，尚不是经过本项目校准的证据可信度。[ST 5.6.0 发行说明](https://github.com/huggingface/sentence-transformers/releases/tag/v5.6.0)

未来模型实验 adapter 至少应显式记录：本地模型目录/完整 revision、库版本、device/dtype、batch size、token 上限、query prompt、raw score 与转换方式、耗时及失败。保证每个 pair 恰好一个有限标量，不悄悄降级到词法并继续标为真实模型结果。当前 adapter 的模型名参数可以接收目录，但它本身尚未实现这些可审计配置。

## RRF、DBSF 与重排顺序

RRF 使用各召回列表的**名次**融合，DBSF 对每路返回得分按该次结果的均值/标准差归一化后求和。Qdrant Query API 从 1.10.0 提供多阶段查询，DBSF 从 1.11.0 可用；当前文档的 RRF 默认常数是 2，显式配置 `k` 从 1.16.0 可用。实验若采用常见的 `k=60`，要把 rank 起点、公式与 `k` 写入 manifest，不能冒称与服务默认值相同。当前 qdrant-client 1.16.2 锁版本也不证明服务端版本支持所有最新参数；加权 RRF 的新 API 需单独核对，当前文档标为 1.17.0。[Qdrant 官方融合文档](https://qdrant.tech/documentation/search/hybrid-queries/)

当前 BM25 在 Python 中计算，向量在 Qdrant 查询。本轮的最小可审计实验是对**相同权限过滤、相同候选快照**在应用内计算 RRF；无需新建 sparse 索引。若未来使用 Qdrant 的 `prefetch` + `FusionQuery`，必须先有对应 named dense/sparse 向量或其他可查询表示。DBSF 依赖返回 top-k 的得分分布，候选数和离群值会影响它，应在独立验证集比较稳定性后选择。

顺序应为：权限与来源状态过滤 → 各路召回 → 融合排列候选 → 神经/词法重排 → 再验当前权限 → 来源分散、证据阈值与引用。RRF 不能找回候选池之外的文档；神经重排也无法修复解析时已丢失的 PDF 单元格。当前实现重排整个召回并集，替换融合只影响前序排列及重排 tie-break；若未来在融合后新增 top-k 截断，则还会改变进入重排的候选池。实验应分别固定候选池做消融，不能把组合提升全归给 reranker。

## CPU、GPU、离线与资源代价

| 对象 | 官方权重信息 | 本轮可以推断的代价；尚不能宣称的能力 |
| --- | --- | --- |
| BGE 小 embedding | 约 24M 参数；单份 safetensors 95.8 MB，F32 | 本地基线的权重规模最小；实际加载峰值、长 chunk 截断损失与并发需实测。[固定版本文件树](https://huggingface.co/BAAI/bge-small-zh-v1.5/tree/7999e1d3359715c523056ef9478215996d62a620) |
| BGE reranker | 约 0.6B 参数；safetensors 2.27 GB，F32 | 每个 query/candidate pair 都要跑模型；FP16 权重约减半属于参数字节推算，实际峰值还含激活和工作区。[官方文件树](https://huggingface.co/BAAI/bge-reranker-v2-m3/tree/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e) |
| Qwen 0.6B embedding / reranker | 各约 0.6B 参数；各 safetensors 1.19 GB，BF16 | 按约 595.8M 参数推算，BF16 权重约 1.19 GB，转换为 F32 约 2.38 GB；两模型同时常驻需合计。32K 是模型上下文上限，官方样例常设 8192，并非在现有 CPU 上达到交互延迟的保证。[Embedding 文件树](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3)、[Reranker 文件树](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/tree/e61197ed45024b0ed8a2d74b80b4d909f1255473) |

以上 GB/MB 为官方十进制文件展示或参数字节推算，**不是整机 RAM/VRAM 最低配置**。读取资料未发现可对本项目直接承诺的 CPU QPS、P95、并发量或最小显存。CrossEncoder 支持显式 `cpu`/`cuda`，未指定时按可用设备选择；FP16/BF16 是否更快取决于硬件与算子，当前锁与源选择的 CPU torch 构建不提供 CUDA 推理。GPU、Flash Attention 或 vLLM 优化应放在另一个批准的隔离环境记录，不改变当前锁与业务运行时。[ST 设备与 dtype API](https://sbert.net/docs/package_reference/cross_encoder/model.html)

若都存 F32 单向量，512 维的原始向量约 2048 字节/点，1024 维约 4096 字节/点，后者约翻倍；这是本报告的算术推断，不含 HNSW、payload、副本和历史版本。更换 embedding 还要重新编码实验 corpus，增加构建时间与独立 collection/快照存储。

离线前提是已由允许的资产流程准备完整、固定 revision 的本地权重、tokenizer、ST module 配置与所需依赖。ST 提供 `local_files_only`，HF 的 `HF_HUB_OFFLINE=1` 会禁止 Hub HTTP 调用，缓存缺失时报错。只写“本地开源”或只缓存权重文件不足以证明全程离线。初次研究未操作模型缓存；后续 BGE 使用已有只读缓存，Qwen 使用独立目录，正式推理均阻断出网并记录冷加载/热重复。[ST 离线加载 API](https://sbert.net/docs/package_reference/cross_encoder/model.html)、[HF 离线环境变量](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables#hfhuboffline)

## 暂缓项与重新考虑的条件

| 技术 | 官方事实与本项目判断 | 重新考虑条件 |
| --- | --- | --- |
| Qwen3-VL embedding/reranker | 官方系列支持图像、视频与混合输入，最小型号为 2B；embedding 2B 输出 2048 维。其资源与数据链超出本轮文本检索候选。多模态匹配得分不能自动证明表格单位、合并单元格与逐页引用正确。[Qwen 官方仓库](https://github.com/QwenLM/Qwen3-VL-Embedding) | 有获准的扫描/视觉文档真实样本、人工原页标注、页图至引用映射与 GPU 预算；先独立评估，不能用合成 PDF 或公共视觉榜单替代。 |
| Late interaction / ColBERT | 一个文档产生多个 token 向量，以 MaxSim 比较；Qdrant 的候选重评分示例关闭 multivector HNSW（`m=0`）以减少索引代价。这需要新的表示与存储，不是把现有单向量的 reranker 名称换掉。[Qdrant 官方说明](https://qdrant.tech/documentation/advanced-tutorials/using-multivector-representations/) | 先证明 dense+BM25+两种重排仍有具体细粒度漏召回，并能承担 token 向量存储、构建、延迟及权限回归成本。 |
| GraphRAG | Standard GraphRAG 用 LLM 提取实体/关系、总结并生成社区报告，额外引入索引推理成本。它适合跨材料关联与全局汇总；本轮 IT 操作步骤、错误编号和逐页证据排序未证明需要该代价。[Microsoft 官方索引说明](https://microsoft.github.io/graphrag/index/methods/) | 存在人工标注的跨文档关系/全局问题，现有检索失败有证据；具备关系级权限、更新/停用传播与批准的模型预算。 |
| Temporal | Durable Workflow 可从持久化历史恢复，但 Activity 可能重试；“只观察一次完成”不等于外部写入只执行一次，建单幂等仍需业务服务保证。[Temporal 官方 Activity 语义](https://docs.temporal.io/activity-definition#idempotency) | 有跨系统、长等待、多步骤补偿与恢复需求，现有队列不足的故障证据，以及 Service/Worker 运维方案。先验收建单、交接与索引发布幂等，不因框架宣传迁移。 |
| MCP | MCP 是 AI 应用接外部系统的标准协议。它不提供本项目的证据相关性或工单业务授权规则。[MCP 官方介绍（2026-07-28）](https://modelcontextprotocol.io/docs/2026-07-28/getting-started/intro) | 有具体、已授权的外部系统/客户端互通需求；可提供与既有鉴权、审计、幂等、草稿确认等价的工具边界和协议版本测试。 |

## 可执行验收与未知项

1. **可重现身份**：获准的本地实验以完整模型 revision、文件清单/hash、库版本、设备、dtype、prompt、长度、维度、归一化、融合公式与候选快照形成 manifest；出网阻断后重复加载成功。初次研究仅完成官方身份核验；已执行的 BGE/Qwen 实验身份见对照报告与原始 manifest。
2. **单变量比较**：embedding 用相同文本/权限/问题比较 BGE 与 Qwen；重排在同一候选池分别比较词法、BGE、Qwen；融合独立比较现有加权与 RRF，DBSF 最多作为第二候选。调参/阈值校准与最终评估问题分开，记录 Recall@k、MRR/nDCG、拒答、错误引用和权限泄漏，而非只看问答关键词。
3. **分数与 token 契约**：每对一分、有限数值、固定激活与分数方向；短/长 pair 记录 token 数和被截断位置，Qwen suffix 仍存在。PDF 验收继续串联原页/单元格、chunk、PG/向量、候选、实际模型上下文、答案及引用；模型提升不放宽解析支持范围。
4. **容量证据**：分别记录冷启动、构建耗时、CPU/RAM 或 GPU/VRAM 峰值、查询 P50/P95、并发、batch 与输入长度，单独列出失败/OOM/超时。没有实测不能承诺设备配置或部署成本。
5. **质量边界**：真实故障 PDF/私有 IT 问题仍没有本轮质量证据。后续合成 corpus 使用真实 BGE/Qwen，另有业务测试使用确定性 embedding/受控模型；两类证据分别记录，均不能替代私有文档与答案质量。官方 Qwen 公共 rerank 对比采用 Qwen embedding 召回的 top-100，与本项目当前候选池不同，不能推出企业 IT 的赢家。[官方评测条件](https://qwenlm.github.io/blog/qwen3-embedding/)

初次研究形成两类 embedding、两种神经重排和 RRF 的有限实验路径；最终已执行部分、保留/暂缓决策以[选型复审](../architecture/technology-review.md)为准。真实 PDF 内容保真、目标 Linux、真实身份/答案模型与完整恢复仍分别验收。
