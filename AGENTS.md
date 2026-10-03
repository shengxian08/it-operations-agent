# 企业 IT 助手的持续实施规则

用户明确要求依据共享对话自查与实现。项目证据、问题与下一步分别见
[项目审查](docs/project-audit.md)、[改进待办](docs/improvement-backlog.md)。

## 先确认业务路径，再改代码

- 业务对象是 IT **工单**。从知识问答、查单、确认建单、人工交接四条员工路径核对实际实现。
- 阅读当前工作区和相关测试；不要只读 Git HEAD。本项目存在尚未提交的正式环境实现，保留用户修改，不做 reset、checkout 覆盖、批量提交或无关重写。
- 缺陷必须记录场景、实际/预期、代码位置、复现、根因、影响、优先级、最小修复和可执行验收。区分已验证、实现未验证、Mock、仅文档、缺失、环境阻塞。
- 每次处理一个可独立验收的任务；先稳定复现，再修复，再逆向检查。不要降低质量阈值、修改预期答案或补猜测数据来获得通过。

## 证据与业务安全

- PDF 必须核查原页、单元格、chunk、PG 与向量、召回候选、模型实际上下文、答案与引用。关键词、HTTP 200、向量数量或固定 Mock 答案均不足以证明文档质量。
- 当前支持普通文字层与单页矩形线框表格、单层非空唯一表头。合并/多层表头、无框、多栏、扫描、跨页与未识别布局不在自动语义保真承诺内。保留页/表/行、表头、单位、条件与注释；无法可靠解析时明确失败并要求人工核对。
- 合成 PDF 与真实故障 PDF 分开标注；可控模型与真实模型、确定性 embedding 与真实语义 embedding 分开留证。没有真实样本或批准的模型环境，就保持相应质量未验证。
- 查单始终重新鉴权；禁止用历史模型文本授权、猜测多个候选、把超时/数据库故障报为查无结果。历史引用同时受当前来源权限/停用状态与引用快照原权限约束。
- 创建正式工单必须确认当前草稿版本。修改后令牌失效并重新签名；并发、回滚、未知响应重试要证明只创建一张工单。
- 人工交接只有获得持久化记录编号才能宣称“请求已记录”；待处理不等于接单。交接失败要显示未确认，不能只靠话术宣称成功。
- 不读取或输出真实 secrets，不上传企业文档，不调用真实付费模型，不对生产数据库、文件或索引执行重建、删除、种子或目录同步。部署、生产迁移与外部联系按用户的具体授权执行。
- 新索引排除无逐页出处的旧版平铺 PDF，保留原来源与历史版本，管理端标注待重解析。不得给历史内容补猜页码或冒认新版提取身份。相同文件 hash 允许重新准备解析，管理员核对后逐篇发布。

## 本机已验证的命令

仅使用可丢弃的 `compose.test.yml` 服务；先确认端口与项目没有被其他任务使用。
数据库目录为 tmpfs，重启会丢失测试数据。不要对共享或生产环境执行 down、重新创建、downgrade 或清理。

```powershell
docker compose -f compose.test.yml up -d --wait postgres redis qdrant
$env:ENVIRONMENT='test'
$env:MODEL_MODE='mock'
$env:DEMO_ENABLED='true'
$env:TEST_DATABASE_URL='postgresql+asyncpg://itops_test:isolated-test-only@127.0.0.1:15932/itops_testing'
$env:TEST_REDIS_URL='redis://127.0.0.1:16379/15'
$env:TEST_QDRANT_URL='http://127.0.0.1:17333'
$env:DATABASE_URL=$env:TEST_DATABASE_URL
$env:PYTHONPATH="$PWD/backend;$PWD"
Push-Location backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m pytest -q --junitxml=../artifacts/audit-final.xml
uv run --locked ruff check --config pyproject.toml app tests ../scripts
uv lock --check
Pop-Location
Push-Location frontend
npm test
npm run build
npm run test:e2e -- --project=demo --project=production-mock
Pop-Location
```

缺少 TEST URL 会跳过真实依赖测试；必须读取 passed/failures/errors/skipped。目标 Linux、真实企业身份与模型、容量、完整恢复和外部告警分别验收，不能用本机通过替代。

PDF 证据需保存时，后端设置 `PDF_EVIDENCE_DIRECTORY`；浏览器截图设置 `AUDIT_EVIDENCE_DIRECTORY`。
只读业务探针：设置上述 PYTHONPATH 后运行
`backend/.venv/Scripts/python.exe scripts/audit_business_paths.py --output artifacts/business-audit-probes.json`。
详细复现与最终报告见项目审查；历史报告仅表示其记录时间的证据。
