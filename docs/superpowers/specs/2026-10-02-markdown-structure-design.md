# 来源型 Markdown 分块合同

任务依据：[技术复审的下一独立任务](../../architecture/technology-review.md)。用户已明确要求“按你的方案继续执行”，沿用已提出的来源、原子块、800字符、512-token审计、独立版本、管理员预览和回滚方案；本文件细化实现接口，不增加业务路径改造、正式迁移或生产发布。

## 选择

采用当前锁文件已有版本的`markdown-it-py==4.2.0`，将其声明为直接依赖。使用CommonMark block token及原始行映射识别结构，启用pipe table，不从渲染HTML反推原文。正则用于frontmatter；fence闭合记录实际CommonMark规则消费的closer，不用原行标记猜测容器语义。继续按空行打补丁会遗漏嵌套、缩进、Setext和代码内伪标题；Docling替换超出这个Markdown故障的必要范围。官方接口依据：[Token Stream及规则](https://markdown-it-py.readthedocs.io/en/latest/using.html)、[4.2.0 API](https://markdown-it-py.readthedocs.io/en/v4.2.0/api/markdown_it.html)。

## 原文与结构

- `KnowledgeChunk.content`继续是原文片段。Markdown新块满足`source[char_start:char_end] == content`，保留CRLF、缩进、空行、反引号和大小写。搜索前缀单独生成，不能混进原文或引用摘录。
- 新纯解析模块`app/rag/markdown.py`识别真实ATX/Setext标题层级、段落、列表、引用、fenced/indented code、pipe table与引用定义。标题保存真实文本、等级及原文范围；frontmatter不作为正文或检索上下文。代码内标题不改变章节路径。
- 每块保存parser version、block type、完整块原文范围、当前chunk范围、真实section path及对应heading ranges、是否含代码。页面/cell字段不为Markdown补造。
- 普通长段落沿用800/120窗口；含inline code的段落、code、list、blockquote和table保持完整。原子块超800字符、未闭合fence、无法建立可靠source map时明确失败并要求人工整理，不能扩大预算或切断命令/配置来通过。
- 支持界限明确：HTML block不自动解释成可靠的Markdown结构；解析失败不能呈现可发布状态。新增解析在独立子进程完成，保留现有大小、时间和资源上限。

## 预览、持久化与知识版本

- 解析、chunk与预览共用同一结构结果。预览按原始块展示完整代码/config，并显示真实章节，不再由另一份正则重建章节。
- 在`RevisionChunk`增加可空JSONB `structure`；在`KnowledgeSource`和`KnowledgeSnapshot`增加可空`parser_version`。历史记录为空，表示原身份未知，禁止回填新版解析身份。只生成本地Alembic升级代码并在可丢弃环境验收，不执行生产迁移。
- 新pipeline manifest为version 2，固定Markdown parser/chunker/search-context及语义embedding 512-token不截断政策；PDF原提取/分块合同、模型、权重、融合、重排和权限阈值不变。
- 未按新合同解析并由管理员预览的旧Markdown来源，不进入新pipeline版本；原来源及旧索引保留，管理端标注待重解析。相同hash允许prepare，管理员逐篇预览后发布。发布仍建独立collection/完整snapshot，失败不切active pointer。
- 读取与回滚只接受严格匹配的已知v1/v2 manifest和原embedding身份。旧v1使用自己的原chunks和title+raw搜索表示，不补新metadata。旧引用原文来自其snapshot，同时重新检查当前权限和snapshot原权限。

## 搜索、模型输入与预算

- 同一来源section path用于dense输入、BM25和lexical重排；真实原文仍单独保存。查询模型和融合参数不改。
- 真实语义embedder在编码前用实际tokenizer检查完整输入，包括标题/章节前缀；超过模型512-token上限即失败，不能静默截断。确定性embedding测试明确不作为此tokenizer质量证据。
- 新Markdown citation保留完整原文块，不经240字符摘要或空白归一化；模型实际上下文另带章节路径及原文范围。代码块语义完整性验收到实际prompt，答案正确性仍需独立真实模型质量验收。
- 任何检索元数据不能扩大来源权限或让历史模型文本授权。保持员工/支持/管理员过滤与返回前当前权限复查。

## 验收

1. 两个既有code反例和标题承载型号/错误码先RED后GREEN；覆盖伪标题、缩进、Setext、CRLF、重复段落、tilde fence、inline code、列表/表格/引用、frontmatter和不闭合/超限失败。
2. 隔离真实PG/Qdrant验证prepare→preview→publish、结构与搜索输入、失败不切指针、同hash重解析、旧v1读取/引用/回滚及三角色权限。
3. 真实固定BGE在获准只读缓存、离线环境审计原实验固定语料及仓库现有33篇Markdown的token长度/出处；不修改旧金标、留出集或旧证据。前者合成，后者仓库资料，两者均不冒认为企业私有质量。
4. 前端单测与浏览器核对完整代码/config预览、旧来源待解析提示、桌面及窄屏无横向溢出；Mock HTTP与实际依赖证据分别标注。
5. 适用的后端/前端、锁、静态检查完成后，独立审查本轮diff与需求映射；真实企业样本/答案、目标容量与部署保持独立未验证。

## 工作区约束

保留`codex/production-engineering`的已有未提交实现。变更前已在`artifacts/markdown-structure/before/`留本轮相关文件原字节及hash；只编辑任务相关文件，不reset、不替换为Git HEAD、不批量提交。Docker仅用于明确loopback TEST URL和可丢弃compose服务；不对生产库、来源、索引或主模型缓存执行重建、清理、目录同步或自动迁移。
