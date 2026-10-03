# 共享对话要求逐项验收

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../verification/README.md)。

验收日期：2026-10-02。任务来源：[用户指定的共享对话](https://chatgpt.com/share/6abf55eb-56f8-83e9-ad6c-4a295bdb43ec)。按对话限定范围，验收的是技术选型复审、可执行的隔离实验与模块决策；真实企业质量、目标容量及候选的正式迁移分别保持未验证或待实施。

| 对话要求 | 实际交付与核对 | 本轮验收状态 |
| --- | --- | --- |
| 1. 依据当前工作区审查规则、报告、核心业务路径、代码、锁文件与测试，区分实现/生效/Mock/未验证/缺失 | [现状表](../architecture/technology-review.md)覆盖解析、表示、分块、检索、模型、路由、typed query/write、确认事务、台账、交接、worker、缓存、前端、部署观测与评测；三条只读业务反例仍按实际行为记录。当前 PG 令牌存储与未接入 LangGraph checkpointer 等事实已更正。 | 审查完成；正式实现与生产部署证据没有混同 |
| 2. 先明确语言、文档、规模、更新、硬件、并发时延、费用、隐私与运维约束 | [约束表](../architecture/technology-review.md)分开记录仓库声明、Windows/Docker 实测环境与未知项；100员工/10并发/500篇、目标Linux资源与SLO属于待验收目标。实际答案模型费用、更新频率、版本保留和团队维护能力没有猜测数据。 | 约束审查完成；未知项明确保留 |
| 3. 基线不丢，每模块最多两个适用候选，不默认增加复杂框架 | 同报告模块决策表覆盖候选、问题、成本、保留/调整/暂缓、采用与回滚条件；解析只保留Docling/MinerU，Paddle是有真实扫描失败证据时的条件方案。Multi-Agent、GraphRAG、MCP、Temporal及大模型均给出具体重新考虑条件。 | 决策完成；候选没有自动成为正式配置 |
| 4. 核验当前官方版本、日期、代码与权重许可、接口、设备与维护负担 | [解析研究](research-parser-options.md)、[检索研究](research-retrieval-options.md)保存2026-10-02官方来源；分开记录最新模型卡和实际固定revision。Docling实际安装两次依赖缺口与修正成本、Qwen当前CrossEncoder适配和得分/截断限制均有证据。 | 官方事实核验完成；未运行候选没有借用公开榜单质量 |
| 5. 独立目录/索引、固定语料与权限、单变量、dev/holdout分离、真实与合成分开，保存重复与失败 | [实验代码与命令](../../experiments/technology_review/README.md)、[对照报告](technology-comparison.md)及[证据索引](technology-review-evidence.json)：82篇/26查询固定合成探针；两个新collection；真实固定BGE；加权/RRF/章节表示三臂；同池真实Qwen；七个同字节PDF的同Linux解析比较。热重复与标准差分别记录，环境失败原始产物保留。 | 可执行实验完成；BGE神经重排、Qwen embedding、MinerU未运行，OCR未开启 |
| 6. 解析、召回、答案引用、业务一致性、性能与维护分层评估；每模块形成可执行决策 | 解析原页/结构/坐标、逐查询候选/指标/权限、业务39项真实依赖测试、候选RSS/耗时/依赖修正各自留证。没有真实答案模型的拒答/答案/引用结果明确未验证。主应用缺陷、实验复现问题和候选合同差异按场景/实际预期/根因/影响/优先级/最小处理/验收列入对照报告。 | 分层审查完成；未把Mock或转换success当质量通过 |
| 7. 交付两个指定报告，给出主组合、条件增强、暂不采用与下一独立任务 | [technology-review.md](../architecture/technology-review.md)与[technology-comparison.md](technology-comparison.md)已保存；主组合保留，首个实施候选为来源型Markdown分块合同，业务T02/T03/T04与真实质量/目标部署验收继续独立跟踪。 | 指定交付完成 |

## 收尾核对

最终原始产物核对（本地证据：`artifacts/technology-review/final-verification.json`）是2026-10-02实际只读检查的结果，没有重新启动模型或测试服务。它独立从逐查询排名重算四组留出Recall/Precision/MRR/nDCG，复核全部候选静态权限及Qwen候选池，核对当前源码、82条chunk记录与190条真实向量完全一致，检查七个PDF的字节hash和两个比较文件都使用Linux基线。28份完整DoclingDocument、28份evidence JSON和7张原页PNG均在；规则表四轮均有九格真实bbox、九个表头标志均为false。

实际JUnit合计为6项实验控制测试和39项业务/worker/出处测试，分别为0 failures、0 errors、0 skipped。锁定环境的Ruff检查和`uv lock --check`本轮均成功；主依赖锁没有迁移。前次已留证的37个业务实现/测试/报告文件逐一核hash，差异为0。

独立检索与PDF审查均完成。TECH-REPRO-001的镜像cwd导入问题已用当前源码实际编码及输入/向量一致性关闭；TECH-PERF-001的Windows/Linux对照口径已修正并独立复核关闭。标题丢失、命令拆分与Docling表头/失败合同差异仍在主报告保留，不宣称主应用已经修复。

收尾观察时Docker API不可连接（`dockerDesktopLinuxEngine`管道不存在），因此不宣称当前测试服务或临时collection仍在线，也没有执行重启、down或清理。此前成功命令、退出状态及原始结果保留；PG/Qdrant测试存储为tmpfs，后续复现按README重新建立可丢弃环境并使用新的run-id。环境当前可用性与已完成实验的证据分别记录。

本轮验收结果：**共享对话要求的技术复审与可执行隔离实验交付完成**。真实企业PDF全链、真实身份/答案模型、阈值与截断质量、500篇/10并发、灾备及外部告警送达没有获得本轮验收结论。
