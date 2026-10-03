# 来源型 Markdown 分块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将已批准的来源型Markdown合同接入实际解析、索引、检索上下文、预览和版本恢复路径，并用真实隔离依赖及固定BGE留下证据。

**Architecture:** CommonMark block/source-map提取作为共享结构来源；原文与检索前缀分开。新结构持久化于新revision，旧manifest独立读取，未复核旧来源只标注待重解析。

**Tech Stack:** Python 3.11、markdown-it-py 4.2.0、现有FastAPI/SQLAlchemy/PostgreSQL/Qdrant/BGE、React/Vitest/Playwright。

**Spec:** [设计合同](../specs/2026-10-02-markdown-structure-design.md)。

---

## 全局约束与接口

原文精确范围、原子块800上限、语义512-token上限、无猜测出处、管理员预览、ACL再验证、旧版本不重标身份均为硬合同。只执行本机测试迁移；不部署、不发表、不触碰生产数据。依据根AGENTS，本轮在当前含未提交正式实现的工作区做精确增量编辑，保留before字节；不提交混合用户改动。过程记录位于`artifacts/markdown-structure/ledger.md`，RED/GREEN命令日志和JUnit保留。

新接口：`KnowledgeChunk.structure: dict | None`；`parse_markdown(text)`返回真实heading/block/source spans；`markdown_sections(text)`生成同源预览；`search_text(title, content, section_path=())`仅生成搜索输入；`parser_version`区分已核对来源与未知历史身份；`pipeline_config`生成v2，已知v1/v2严格比较；真实embedder在`encode`前验证token数量。

## Task 1: 共享结构解析与分块

**Files:** 新建`backend/app/rag/markdown.py`、`backend/tests/unit/test_markdown_structure.py`；修改`backend/app/rag/ingest.py`、`backend/app/production/knowledge_parser.py`、`backend/pyproject.toml`、`backend/uv.lock`。

- [x] 写并运行失败测试：

```python
def test_fenced_command_stays_a_source_span():
    source = '# 指南\n\n## E-42\n\n```powershell\nGet-Service\n\nGet-Process\n```\n'
    chunks = chunk_markdown(source, source_path='guide.md')
    assert len(chunks) == 1
    assert chunks[0].content == source[chunks[0].char_start:chunks[0].char_end]
    assert chunks[0].structure['section_path'] == ['指南', 'E-42']
```

运行：`backend/.venv/Scripts/python.exe -m pytest backend/tests/unit/test_markdown_structure.py -q`（PYTHONPATH设backend）。Expected: command被切分或structure缺失导致实际assert失败。

- [x] 按设计接口实现block/source-map，保留旧PDF分支；普通段落800/120，原子单元超限抛ValueError。加入fence内伪标题、Setext、CRLF、嵌套、重复原文、inline code、引用定义、超限/未闭合与seed资料反例。
- [x] 安装仅新增直接依赖，更新lock且不得改变其他锁定版本；同命令GREEN，再运行`tests/unit/test_chunking.py`与`tests/unit/test_pdf_structure.py`。Expected: 全部实际执行、0失败。

## Task 2: 预览、版本存储、逐篇批准与兼容读取

**Files:** 修改`backend/app/production/knowledge.py`、`knowledge_models.py`、`scripts/ingest_knowledge.py`；新建Alembic后继迁移与`backend/tests/production/test_markdown_versions.py`。

- [x] 在共享knowledge_context写RED：旧Markdown source和v1 revision保留；新资料publish不自动给旧来源套新身份；同hashprepare返回would_enqueue/queued，preview后可逐篇新发布；v1 retriever、历史引用与activate回滚仍工作；改错manifest、model/dim与未经preview的job拒绝。
- [x] 增加可空结构/身份字段，实现v2 manifest和严格已知v1/v2兼容；持久化同源preview/chunk结构，dense/BM25/rerank使用同前缀；旧来源标记及新索引排除保持source/old revision完整。
- [x] 运行新版本测试加`tests/production/test_knowledge_snapshots.py`。Expected: 显式TEST URL、真实PG/Qdrant、0失败/错误/跳过；回滚/权限/旧引用保持。Alembic只对compose.test测试库upgrade head，不执行downgrade。

## Task 3: token预算与模型实际上下文

**Files:** 修改`backend/app/rag/ingest.py`、`retriever.py`、`backend/app/agent/graph.py`；补`test_markdown_structure.py`、`test_model_limits.py`及版本链测试。

- [x] RED证明含代码chunk经过_to_hit和_answer_prompt仍被归一化/240字截断；记录完整source/fence/章节预期。用真实接口形状的tokenizer测试512边界与513拒绝，模型encode在失败时未调用。
- [x] 新结构citation保存完整原文及章节/范围，prompt按JSON安全编码携带这些字段；真实SentenceTransformerEmbedder用实际tokenizer完整输入检查并拒绝超限，不静默truncate。
- [x] 固定旧corpus/holdout在独立artifact目录执行实际BGE的新版输入及token审计，并检查33篇seed；模型/缓存只读、断网、来源hash与真实token结果保存。Expected: 原文范围均匹配、原子块不拆、任何超限明确失败；私有质量保持未验证。

## Task 4: 员工与管理员原文预览

**Files:** 修改`frontend/src/production/KnowledgeSection.tsx`、`types.ts`、`AdminPage.tsx`、`production.css`；新建`KnowledgeSection.test.tsx`，补相关浏览器用例。

- [x] RED检查代码中的换行/缩进/空行与伪标题保留，使用`pre/code`；真实章节结构展示；PDF表格原显示回归；旧Markdown待重解析状态明确且不能误称已经进入新索引。
- [x] 按typed blocks渲染原文，代码不执行HTML，窄屏换行/容器宽度受控；旧profile继续显示历史原文。
- [x] 运行`npm test`、`npm run build`与适用browser项目，人工查看桌面/窄屏证据。Expected: 实际测试/构建通过，Mock浏览器标签明确，无横向溢出和代码截断。

## Task 5: 全范围验收与独立审查

**Files:** 更新`docs/project-audit.md`、`docs/improvement-backlog.md`；新建`docs/architecture/markdown-structure.md`及`artifacts/markdown-structure/implementation-evidence.json`。

- [x] 适用全量后端/前端/浏览器、锁与Ruff检查；读取JUnit四个计数。四业务路径只读探针复核，T02/T03/T04不混入此次实现。
- [x] 依据before-manifest/current工作区生成本轮review package，请一个独立reviewer检查实际diff、source-map、未知历史身份、token/profile、发布失败/回滚与ACL。重要发现补RED→GREEN，保持原gold和质量阈值。
- [x] 对本spec每项明确actual evidence/未验证范围；记录新缺陷的场景/实际预期/根因/位置/影响/优先级/最小修复/可执行验收。软件合同验证完成与私有文档、真实答案、目标Linux验收分别结论。

## Review Focus

特意检查fence内伪标题、tilde/缩进/CRLF/Setext、带代码的嵌套容器、参考定义和source map覆盖；title/section前缀是否渗入raw citation；旧manifest兼容是否放松任意model/dim/阈值；未preview旧来源是否被自动重标并迁移；旧引用当前权限/snapshot原权限；token拒绝能否在Qdrant或指针副作用前发生；真实tokenizer/合成与seed/Mock证据是否如实标记。
