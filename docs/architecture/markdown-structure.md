# 来源型 Markdown 实施与验收

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../verification/README.md)。

2026-10-02；依据用户批准的[技术复审方案](technology-review.md)、[设计合同](../superpowers/specs/2026-10-02-markdown-structure-design.md)和[实施计划](../superpowers/plans/2026-10-02-markdown-structure.md)。本次任务是 Markdown 来源与结构合同，不包含 T02/T03/T04 业务补全、生产迁移或正式发布。审查当前工作区，保留已有未提交实现。

## 当前行为

解析使用直接锁定的 `markdown-it-py==4.2.0` block tokens 和原始行映射。ATX、Setext、fenced/indented code、列表、引用、inline code、pipe table 和参考定义保存原文范围与真实章节。代码中的 `#` 不成为标题；重复标题按实际位置区分。frontmatter 不当正文。CRLF、空行、缩进、反引号及大小写留在原文块里；范围是 Python 字符范围，不冒充 PDF 页码或文件 byte offset。

普通段落保持 800/120 窗口。代码、含 inline code 的段落、列表、引用和表格为完整原子块；超过 800 字符明确失败，要求人工整理。未闭合 fence、HTML block 或无法完整定位 source map 也拒绝。提取子进程原有资源上限保留，2,000,000 字符检查先于结构解析。旧 `extract_text` API 的通用换行处理继续兼容已有调用；正式 `knowledge_parser` 直接解码源 bytes，chunk 范围针对实际持久化的 content。

`KnowledgeChunk.content` 是真实原文。dense、BM25、lexical reranker 共用 `title + 真实 section_path + raw` 搜索表示，前缀单独生成。新 Markdown citation 保留整个原文块，进入实际 `_answer_prompt` 时不再归一化空白或截到 240 字，并单独提供章节与字符范围。提示中的原文经 JSON 转义，不执行文档中的 HTML。

管理员和员工原文页共用 typed blocks；含代码的块以 `pre/code` 字面显示，窄屏可换行；面包屑来自解析结果。PDF 原页、表格单元格、表头及条件预览保留。

## 发布与历史兼容

新增 `KnowledgeSource.parser_version`、`KnowledgeSnapshot.parser_version` 和 `RevisionChunk.structure`，均可空。Alembic `0004_markdown_structure` 不回填历史记录。已知 v1 的完整 manifest 和原 embedding 身份继续严格比较；旧版使用原 title+raw 表示，历史引用按原 snapshot 读取并同时复核当前来源权限和 snapshot 原权限。

新 v2 manifest 固定 parser、atomic source spans、章节搜索前缀、未知历史 Markdown 排除策略和 512-token 政策。未重新解析、预览并逐篇发布的旧 Markdown 不进入新索引，原来源、旧 revision 和历史内容保留。管理端显示待重解析；同 bytes/hash 允许重新 prepare。旧 ready 任务不能以新合同发布。新任务的预览数据须与实际 content 的共享解析结果一致。

新来源发布写入明确 parser 身份，revision 保存独立 snapshot 和 chunk structure。失败不切换 active pointer，旧 collection、来源及引用仍在。v1/v2 回滚与当前权限复查已有真实隔离依赖测试。生产数据库尚未执行这份迁移，生产来源尚未重解析或发布。

真实语义 embedder 用实际 tokenizer 检查完整标题/章节/正文输入（含 special tokens），上限取模型上限与 512 的较小值；不允许静默截断。索引准备在创建 Qdrant collection 前检查所有输入，chunk 保存已核对的 token count。超限任务持久化 `embedding_input_too_long`，管理端明确要求人工整理并重新上传、核对。确定性 embedding 测试没有被标为真实 token 质量证据。

## 已执行证据

以下文件均在独立的 本轮证据目录（本地证据：`artifacts/markdown-structure`）；旧技术实验、金标、留出集和旧 PDF 报告保留。

| 证据 | 实际结果 | 能证明的范围 |
|---|---|---|
| `unit-red.xml` → `structure-regression-green.xml` | 25 实际失败 → 结构/PDF/分块 56 passed；完整 prompt 在 Task 3 验证 | 命令断块、伪标题、CRLF、源范围、原子拒绝和 PDF 回归 |
| `versions-red.xml` → `versions-green.xml` | 6 failed/2 passed → 29 passed | 真实 PG/Qdrant 的新旧身份、审批、同 hash prepare、旧引用、权限和回滚；模型受控 |
| `budget-context-red.xml` → `budget-context-green.xml` | 6 实际失败 → 97 passed | 实际 tokenizer 接口形状的边界测试、完整原文进入实际 prompt；tokenizer 为明确受控组件 |
| `budget-index-red.xml` → `budget-index-green.xml` | 1 实际失败 → 9 passed | 超限不创建 collection、不切指针，持久任务失败代码；真实 PG/Qdrant、受控 tokenizer |
| `frontend-red.xml` → `frontend-green.xml` | 4 failed/4 passed → 8 passed | 完整代码、空行/缩进、字面 HTML、章节、旧任务不能发布和预算失败提示 |
| `size-red.xml` → `size-green.xml` | 大小检查反例 1 failed → 结构单测 26 passed | 大文件在调用结构解析器前拒绝 |
| `review-findings.md`、`review-red.xml` → `review-green.xml` | 唯一独立审查发现2个Important，9实际失败/10通过→82通过 | 伪closer拒绝及合法嵌套、共享reranker章节与旧raw兼容；没有降低原阈值 |
| `real-bge-final.json` | 82 合成块 + 33 仓库文档/132 块；max tokens 分别 90/112；全部 source spans 匹配 | 真 BGE tokenizer/编码器，固定 revision、512 维、归一化；不是企业私有文档验收 |
| `retrieval-final.json` | 固定语料 26 查询，每分支重复 3 次；权限候选泄漏 0 | 实际隔离 Qdrant 与真实向量；实验评分路径，未验生产 snapshot API 或真实答案模型 |
| `browser-all.xml`、`browser/markdown-preview-1440.png`、`browser/markdown-preview-390.png` | 20 passed、截图已人工查看 | 真实 Chromium；demo / production-mock HTTP；无横向溢出、未执行代码、完整 DOM 文本 |
| `migration.log` | 从空的 compose.test 库 upgrade head | 只在可丢弃测试库执行；不代表生产迁移 |
| `business-probes.json` | 四路径中的已知缺口仍存在 | 只读、确定性图探针；T02/T03/T04 保持待实现 |

全量命令、最终计数、源码/证据 hash 与独立审查结论记录在 implementation-evidence.json（本地证据：`artifacts/markdown-structure/implementation-evidence.json`）。

最终回归：后端 **346 passed**（52.08 秒）、前端 **33 passed**、demo+production-mock 浏览器 **20 passed**，均 **0 failures / 0 errors / 0 skipped**；TypeScript/Vite build、Ruff、`uv lock --check` 通过。独立审查的两个 Important 已在唯一修复轮中 RED→GREEN，并包含在这次全量结果。没有遗留 Minor。审查本身没有代替实测或重新评审既有业务缺口。

可复现命令：根 AGENTS.md 的三个明确 TEST URL 与 test/mock 环境变量，后端工作目录为 `backend`（不加载根目录环境文件）。先 `python -m alembic upgrade head`，后 `python -m pytest -q --junitxml=../artifacts/markdown-structure/backend-final.xml`；`uv run --locked ruff check --config pyproject.toml app tests ../scripts ../experiments/markdown_structure`；`uv lock --check`。前端 `npm test`、`npm run build`、`npm run test:e2e -- --project=demo --project=production-mock`，截图目录由 `AUDIT_EVIDENCE_DIRECTORY` 指定。

模型实验使用 `experiments.markdown_structure.verify`，完整已执行参数及边界见 implementation-evidence.json 的 commands；固定语料与缓存只能只读。最终只读证据审计：设置同一 PYTHONPATH，在 backend 运行 `.venv/Scripts/python.exe -m experiments.markdown_structure.complete_evidence`。它重新核对 XML 四计数、当前源码/语料/原控制向量 hash、原文范围、真实预算拒绝、raw排名与指标、截图及计划状态，不连接服务或重跑付费模型。复跑模型/检索实验须使用新 artifact 文件与唯一 test collection run-id，不能覆盖旧证据。

## 真实 embedding 的可复现范围

BGE `BAAI/bge-small-zh-v1.5` revision `7999e1d3359715c523056ef9478215996d62a620`，512 维、CPU、归一化，查询指令保留。固定 corpus SHA256 为 `ef522ef95f528a80067c046d64763d83d85f0e9f8c9d7a8e0f4de23296ee1e86`，82 文档、8 dev/18 holdout 查询，holdout 中 15 可回答、3 无答案。

Docker 指定历史 worker 镜像 digest，挂载当前 backend/experiment 源码、只读公开模型缓存和这些明确的测试资料，`--network none --read-only`。加载器显式从 pinned 本地 snapshot 读取，`token=False/local_files_only=True`；实际应用 embedder 构造、预算检查和编码方法运行。报告明确记录这个加载替换，不把它说成生产在线下载/factory 验收。Linux Python 3.11.16；本机 pytest 使用 Windows Python 3.14.3。

新实现生成的搜索输入逐项等于先前批准的章节方案；此次重新计算的 82 个 context vectors 与原方案最大绝对差为 0。baseline vectors 明确复用原实验作为不变控制组，query/context vectors 此次新算。真实 613-token 合成输入在 model.encode 前拒绝，监测到编码调用为 false；完整 JSON prompt 另外保存在报告中，未调用答案模型。

固定 holdout 上的 Recall@5 / MRR@5：原 weighted 为 0.8667 / 0.7333；新实现的章节 weighted 为 1.0000 / 0.9667。分母是 15 个可回答查询，空相关集合不混入该指标；无答案决策未评估。这是固定合成输入的回归结果，不能用作企业真实质量、幻觉率、引用准确率或目标环境 P95。

## 使用与剩余验收

管理员需先在批准环境执行迁移、备份并验证 v1 可回滚，再对待重解析资料按原文件逐篇 prepare→预览→核对正文/权限→发布。代码与迁移已准备，生产切换需按独立目标环境任务办理，不自动扫描目录、补身份或同步。

真实企业失败 PDF、私有 Markdown、真实模型答案/引用、企业身份、目标 Linux 全栈、容量、完整恢复与外部告警仍分别未验证；T06/T07 保持原状态。本次离线公开 BGE 证据也不将此前受控模型的 PDF 链路升级为真实答案质量证据。
