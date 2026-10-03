# 隔离技术对照

这是2026-10-02选型复审的实验目录，不由主应用导入。结果见
[选型复审](../../docs/architecture/technology-review.md)与
[实验报告](../../docs/experiments/technology-comparison.md)。

## 运行前

- 工作区 `backend/.venv` 需要项目基础与测试依赖；执行路径为仓库根目录。
- PostgreSQL/Redis/Qdrant只能使用 `compose.test.yml`。确认15932/16379/17333没有其他任务占用后启动三个测试服务。数据库/Qdrant为tmpfs，停止后数据可能丢失。
- BGE真实编码使用既有worker完整image ID、已准备的只读 `itops-engineering-model-cache`，不下载或调用答案模型。
- 模型资产准备与推理是分开的容器；新候选缓存仅在 `artifacts/technology-review/candidate-model-cache`。
- raw产物被仓库 `artifacts/` 规则忽略，生成器、固定fixture和简化证据索引保留在源码/文档中。不强制添加模型或向量到Git。
- 不运行 `down --volumes`、生产ops脚本、主知识导入/发布或任何外部通知。

## 固定语料、控制测试和解析

```powershell
$env:PYTHONPATH="$PWD/backend;$PWD"
$env:ENVIRONMENT='test'
$env:MODEL_MODE='mock'
backend/.venv/Scripts/python.exe -m pytest experiments/technology_review/test_controls.py -q
backend/.venv/Scripts/python.exe -m experiments.technology_review.corpus --output artifacts/technology-review/corpus.json
backend/.venv/Scripts/python.exe -m experiments.technology_review.parsing --output-directory artifacts/technology-review
```

`corpus.py`内容、标签、dev/holdout、k=60、分支40、top5和3次重复在运行前固定。
禁止为了结果好看改gold、删反例或同时修改多个因素。

## 真实BGE：断网/只读缓存

下面示例使用当前工作区绝对路径；复制到别处时修改bind的source，保持挂载只读与模型identity。

```powershell
docker run --rm --network none --read-only --tmpfs /tmp --cpus 4 --memory 6g --workdir /workspace `
  --cap-drop ALL --security-opt no-new-privileges `
  --mount type=volume,source=itops-engineering-model-cache,target=/app/models,readonly `
  --mount 'type=bind,source=D:\wendangjiexi\backend,target=/workspace/backend,readonly' `
  --mount 'type=bind,source=D:\wendangjiexi\experiments,target=/workspace/experiments,readonly' `
  --mount 'type=bind,source=D:\wendangjiexi\artifacts\technology-review,target=/experiment-output' `
  -e PYTHONPATH=/workspace/backend:/workspace -e HF_HOME=/app/models `
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 `
  -e TOKENIZERS_PARALLELISM=false -e OMP_NUM_THREADS=4 -e MKL_NUM_THREADS=4 `
  --entrypoint python sha256:11943d9ed8d4036b954523ee0ec4e53d476d43695605aa430af5a62c7ba26ade `
  -m experiments.technology_review.encode `
  --corpus /experiment-output/corpus.json --output /experiment-output/encoded.json `
  --required-app-root /workspace/backend
```

`token_audit.py`使用同一个缓存容器、`HF_HUB_OFFLINE=1`和`local_files_only=True`，
执行 `-m experiments.technology_review.token_audit --encoded /experiment-output/encoded.json --output /experiment-output/token-audit.json`。
它只记录tokenizer长度，不改vectors。

## 同池融合/章节对照

```powershell
docker compose -f compose.test.yml up -d --wait postgres redis qdrant
backend/.venv/Scripts/python.exe -m experiments.technology_review.retrieval `
  --corpus artifacts/technology-review/corpus.json `
  --encoded artifacts/technology-review/encoded.json `
  --output artifacts/technology-review/retrieval.json --run-id 20261002a
```

脚本仅连接显式loopback测试Qdrant端口17333，校验服务版本1.15.4，只创建自己的collection。
重跑需换一个短字母数字run-id；已有collection不会覆盖/删除。BM25在应用内，RRF不是服务原生接口。
语料与encoded hash不符时直接失败。production PG snapshot/API、权限中途变化与模型答案未包含在此微基准。
初次BGE运行未指定workdir；收尾复核以明确workdir和source guard生成 `current-code-encoded.json`，
保存 `current-source-verification.json` 比较原始chunks、inputs、query/document vectors。原产物不覆盖。
唯有当前源码复核与原检索输入等价时保留原候选池结果；不一致时撤回原结果并独立重跑。

## 真实Qwen重排：公开资产准备后断网

`candidate_reranker.py download`只允许固定Qwen0.6B repo/revision，显式`token=False`，
不读真实HF/API凭据，不向外发送文档。资产准备容器需要公开网络且独立HF_HOME；正式推理容器断网。

```powershell
docker run --rm --read-only --tmpfs /tmp --cpus 2 --memory 3g `
  --mount 'type=bind,source=D:\wendangjiexi\experiments,target=/workspace/experiments,readonly' `
  --mount 'type=bind,source=D:\wendangjiexi\artifacts\technology-review,target=/experiment-output' `
  -e PYTHONPATH=/workspace -e HF_HOME=/experiment-output/candidate-model-cache `
  -e HF_HUB_DISABLE_TELEMETRY=1 -e HF_HUB_DOWNLOAD_TIMEOUT=30 -e HF_HUB_ETAG_TIMEOUT=10 `
  --entrypoint python sha256:11943d9ed8d4036b954523ee0ec4e53d476d43695605aa430af5a62c7ba26ade `
  -m experiments.technology_review.candidate_reranker download --output /experiment-output/qwen-assets.json
```

推理使用相同挂载、加 `--network none --cpus 4 --memory 6g`、
`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4 MKL_NUM_THREADS=4`，
执行 `-m experiments.technology_review.candidate_reranker infer --encoded /experiment-output/encoded.json --retrieval /experiment-output/retrieval.json --output /experiment-output/qwen-reranker.json`。
缓存与输入最好进一步单独只读挂载，输出目录可写；本轮read-only根文件系统和离线加载不触发下载。

每个query使用已记录baseline weighted的完整候选池，固定batch8/max_length512/CPU F32。
输出yes/no原始logit差与sigmoid，一pair一有限标量，仍不校准拒答或复用lexical阈值。
三次热重复固定第一个holdout query，质量指标来自全26查询一次确定性评分；两者分别报告。

## 业务/出处验收

依据根AGENTS设置显式TEST URL、在backend目录alembic upgrade head后：

```powershell
.venv/Scripts/python.exe -m pytest tests/production/test_business_transactions.py `
  tests/production/test_runs_worker.py tests/production/test_knowledge_snapshots.py -q `
  --junitxml=../artifacts/technology-review/business-and-provenance.xml
```

只有读取JUnit的tests/failures/errors/skipped后才能写通过。
只读graph探针：`scripts/audit_business_paths.py --output artifacts/technology-review/business-probes.json`。
这些测试的受控答案模型/embedding不属于真实答案质量证据。

Docling独立环境、模型资产和正式断网运行命令由 `Dockerfile.docling` / `docling_candidate.py`
及解析研究文件记录。资源有限时模型推理串行。依赖/下载/模型/结构失效必须保留错误，不能以native
文本路径、模拟cell或官方字段存在替代实际TableFormer结构输出。

同Linux基线以相同worker image、`--workdir /workspace --cpus 2 --memory 4g --network none`，
当前backend/experiments源码挂载只读，既有synthetic-pdfs挂载`/inputs:ro`，
执行 `-m experiments.technology_review.parsing --fixtures-directory /inputs --output-directory /experiment-output/pymupdf-linux`。
脚本复用已有PDF，不重建文件ID；输出记录源码hash、Linux cgroup限额和进程RSS。

```powershell
backend/.venv/Scripts/python.exe -m experiments.technology_review.compare_parsers `
  --baseline artifacts/technology-review/pymupdf-linux/parsing.json `
  --candidate-directory artifacts/technology-review/docling/run-with-native-libs `
  --output artifacts/technology-review/docling/comparison.json
backend/.venv/Scripts/python.exe -m experiments.technology_review.evidence `
  --output docs/experiments/technology-review-evidence.json
```

对照生成器逐case核输入hash。Docling的`requires_review`来自已知合成标签人工合同判断，
不是生产自动检测；所有raw document JSON、转换状态与geometry都保留。证据索引只证明相应范围，
真实文档/答案/目标Linux容量保持未验证。
