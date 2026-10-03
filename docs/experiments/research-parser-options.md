# PDF 解析候选官方事实与隔离 CPU 实验（2026-10-02）

公开发布说明：原始运行证据留在本地；可公开的分类、计数与精选截图见[验收索引](../verification/README.md)。

核验日期为 2026-10-02。官方事实证据来自维护者发布页、版本化源码、官方文档和官方组织模型卡。已只读检查当前工作区 `backend/app/rag/pdf.py`、`backend/pyproject.toml`、`backend/uv.lock`。文末另列授权后的独立 Docling CPU 实验实际证据；只使用七个既有合成 PDF，公开依赖/指定权重在隔离环境准备，没有上传企业文档或访问数据库/索引。

当前结论：**保留 PyMuPDF，不迁移业务解析代码**。Docling 标准本地 PDF pipeline 已完成七个合成 PDF 的隔离 CPU 对照：规则表格文字、行列、真实 cell bbox 与注释保留，但所有 `column_header` 为 false；转换器对七个样本都返回 success，没有自动拒绝扫描、合并或无框布局。本轮证据不满足替换现有来源与失败合同的条件。候选最多两项，Docling 保持实验候选，MinerU 作为第二候选，先解决 4.0 输出适配与 cell 定位缺口；MinerU 尚未运行质量实验。PaddleOCR 仅在真实扫描件或复杂表格失败样本证明需要时做条件试验。

**版本与发布日期**

| 项目 | 核验到的版本/发布日期 | 版本定位与官方证据 |
| --- | --- | --- |
| PyMuPDF | **1.28.2 / 2026-08-06** | 工作区 `uv.lock:1852` 锁定此版本；声明为 `pymupdf>=1.24,<2`。[版本发布页](https://github.com/pymupdf/PyMuPDF/releases/tag/1.28.2)、[PyPI 版本与日期](https://pypi.org/project/pymupdf/1.28.2/) 相互印证。锁文件证据不等于所有部署实例已安装该版本。 |
| Docling | **2.132.0 / 2026-10-01** | 核验时官方最新 release，tag `v2.132.0`，commit 前缀 `5c349dd`。[GitHub release](https://github.com/docling-project/docling/releases/tag/v2.132.0)、[PyPI 版本与日期](https://pypi.org/project/docling/2.132.0/)。 |
| MinerU | **4.0.10 / 2026-09-29** | 核验时官方最新 release，tag `mineru-4.0.10-released`，commit 前缀 `c221cc4`。版本来自[正式 release](https://github.com/opendatalab/MinerU/releases/tag/mineru-4.0.10-released)与[PyPI](https://pypi.org/project/mineru/4.0.10/)，不是沿用共享对话的版本描述。 |
| PaddleOCR（条件方案） | **3.7.0 / 2026-06-11** | [正式 release](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0)；框架包版本不等于具体识别模型版本。以下模型许可证据按模型单独列出。 |

**代码许可与权重许可分开核验**

| 项目 | 代码 | 已核验的权重/尚未确定的权重 |
| --- | --- | --- |
| PyMuPDF | PyMuPDF 与 MuPDF 提供 AGPL 与商业许可两条路径；当前企业项目采用哪条许可及是否满足条款，本轮没有证据。[官方许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) | 当前 `pdf.py` 不使用推理模型，也不使用 PyMuPDF OCR；不存在本解析路径的权重许可问题。 |
| Docling | **MIT**，以 [v2.132.0 LICENSE](https://raw.githubusercontent.com/docling-project/docling/v2.132.0/LICENSE) 为据。 | [Heron 布局模型](https://huggingface.co/docling-project/docling-layout-heron)标为 **Apache-2.0**；含 TableFormer 的 [docling-models 卡](https://huggingface.co/docling-project/docling-models)标为 **CDLA-Permissive-2.0 与 Apache-2.0 双许可**，官方组织在[合并记录](https://huggingface.co/docling-project/docling-models/discussions/22)确认双许可。另选 OCR/VLM 权重时须核查各自模型卡与版本，不能用 MIT 代码许可代替。 |
| MinerU | **LicenseRef-MinerU-Open-Source-License**：Apache-2.0 基础加附加条款。若使用者及关联方合并 MAU 超过 1 亿，或月总收入超过 2000 万美元，需要另行商业许可；向第三方提供在线服务还有显著标识 MinerU 的义务。不能写成纯 Apache-2.0，也不能沿用旧版本 AGPL。[4.0.10 完整许可](https://raw.githubusercontent.com/opendatalab/MinerU/mineru-4.0.10-released/LICENSE.md) | [MinerU-4_models_torch](https://huggingface.co/opendatalab/MinerU-4_models_torch)卡标为 **Apache-2.0**，但说明正文为空；[MinerU-4_models_onnx](https://huggingface.co/opendatalab/MinerU-4_models_onnx)卡在核验时提示 YAML metadata 缺失，未见充分的整包许可说明，不能据代码或 Torch 卡推定其许可。[MinerU2.5-Pro-2604-1.2B](https://huggingface.co/opendatalab/MinerU2.5-Pro-2604-1.2B)卡标为 **Apache-2.0**；是否为所选 4.0 engine 的实际权重，还须记录 resolver 结果与 revision。GGUF、projector 与小模型各有独立文件身份。 |
| PaddleOCR（条件方案） | [v3.7.0 LICENSE](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/v3.7.0/LICENSE) 为 **Apache-2.0**。 | 示例核验：[PP-OCRv6_small_rec](https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec)与[PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6)卡均标 **Apache-2.0**。这不等于 PP-StructureV3 所需布局、检测、单元格模型的完整许可清单已核验。 |

**结构化输出与当前接口兼容性**

当前实现的边界来自工作区代码：`pdf.py:134` 返回 `content`、`sections`；`pdf.py:30` 保存表 ID、1-based 页码、表 bbox、表头、数据行及 cell bbox。使用 `lines_strict`，只接受单层非空唯一表头的规则矩形网格。扫描/大面积图像、疑似多栏/无框表格、合并网格与贴近页边的连续结构会明确失败。原旋转角另存，解析在内存中归零；表格内容与坐标在页对象有效时复制。不得把这个保守合同扩写为任意 PDF 语义保真。

| 路径 | 页/表/单元格证据 | 对接本项目的含义 |
| --- | --- | --- |
| PyMuPDF 现状 | 官方 `Table` 提供 bbox、cells、rows、header、extract；table/finder 寿命依赖 page，须及时复制。[官方 API](https://pymupdf.readthedocs.io/en/latest/page.html#Page.find_tables) | 现有实现已以页、表、行组织证据，但其支持边界由项目代码限定；库存在更多能力不表示项目已经支持。 |
| Docling 标准 PDF pipeline | `DoclingDocument` 的 `ProvenanceItem` 有 page_no/bbox；`TableCell` 有 text、row/col offsets、row/col spans、header 标志、bbox。**cell bbox 明确可为 None**。[来源与表格数据 API](https://docling-project.github.io/docling/reference/docling_document/) | 本轮规则表格实产物为 9/9 非空 cell bbox，但 9/9 `column_header=false`，table provenance 与 cell bbox 使用不同坐标原点，详见文末。旋转、跨页与真实企业样本仍未验收。保留 JSON；仅导出 Markdown 会丢失部分定位信息。`do_cell_matching` 不能据字段存在认定匹配正确。[版本化配置说明](https://raw.githubusercontent.com/docling-project/docling/v2.132.0/docs/usage/advanced_options.md) |
| MinerU 4.0 | 当前合同为 `docvortex.model`/`docvortex.middle`、schema_version `2.0`；页的 page_idx 为 **0-based**，页面几何与 block 结构、producer 身份可保留。`mineru parse --json` 的命令响应不是 MiddleJson。[4.0.10 输出合同](https://raw.githubusercontent.com/opendatalab/MinerU/mineru-4.0.10-released/docs/en/reference/output_files.md) | 不兼容项目 dict，也不能照搬 3.x `pdf_info/_backend` 模板。**本轮未核到逐 cell bbox 的明确公开合同或实际产物，保持未知**；表格 HTML/整表 bbox 不能冒认为真实 cell 坐标。要求候选试验提交实际表头、span、cell、page、bbox 产物后再判定。 |
| PaddleOCR PP-StructureV3（条件方案） | 官方文档示例含 page_index、layout boxes、table `cell_box_list`、`pred_html` 与表格 OCR 结果。[PP-StructureV3 文档](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html) | 只在扫描/复杂表格样本触发时评估；图像像素坐标与 PDF point、页旋转需显式转换，单元格结构与 OCR 文本对应需人工标注验收。文档示例不是企业样本的正确性证据。 |

**CPU/GPU、模型下载、离线与部署**

| 项目 | 运行与下载要求 | 离线/数据流与部署负担 |
| --- | --- | --- |
| PyMuPDF 现状 | 本路径为本地 CPU 原生解析，无推理权重、OCR 或模型下载。官方支持预编译 wheels 且无强制外部依赖；OCR 是另需 Tesseract 的可选能力。[安装文档](https://pymupdf.readthedocs.io/en/latest/installation.html) | 当前模块未引入网络/模型/数据库客户端，现有部署最少新增负担。仍须以本机和目标 Linux 的实测吞吐/RSS 判断容量，不能把原生解析直接等同于快或正确。 |
| Docling 标准本地 PDF pipeline | Python >=3.10,<4.0；模型路径引入 PyTorch/docling-ibm-models，PDF backend 引入 docling-parse。TableFormer 可用 CPU/CUDA/XPU，官方称当前禁用其 MPS。无需必配 GPU，但布局与表格结构权重仍需准备。[版本化依赖](https://raw.githubusercontent.com/docling-project/docling/v2.132.0/pyproject.toml)、[模型设备目录](https://raw.githubusercontent.com/docling-project/docling/v2.132.0/docs/usage/model_catalog.md) | 默认首次使用可下载模型。预取后指定 artifacts_path/DOCLING_ARTIFACTS_PATH；远程处理需 enable_remote_services=True，禁止远程处理与禁止下载是两个控制面。[离线与远程说明](https://docling-project.github.io/docling/usage/advanced_options/) 无模型 native pipeline 可提取原生文本，但不执行布局/OCR/表格模型，不能替代标准表格对照组。 |
| MinerU 4.0 | Python >=3.10,<3.15。base 包已含 WebUI、ONNX runtime、llama.cpp 及 DocVortex；本版本声明 docvortex>=0.5.4,<1。`flash --ocr-mode txt` 原生文字层无需模型；Basic 小模型可跑 CPU；Standard/Advanced 使用小模型+VLM。高吞吐文档建议至少16GB RAM，8GB VRAM 仅计划起点，不是保证。[依赖](https://raw.githubusercontent.com/opendatalab/MinerU/mineru-4.0.10-released/pyproject.toml)、[档位与资源](https://raw.githubusercontent.com/opendatalab/MinerU/mineru-4.0.10-released/docs/en/usage/tiers.md) | 首次模型路径可能下载。预取并 verify，`model.source=local` 使用已准备资产且缺失时失败；auto 会探测 Hugging Face/ModelScope。文档 API 上传、远程 VLM、LLM 后处理是三条不同外传路径，不能只禁其中一项。[模型与数据连接说明](https://raw.githubusercontent.com/opendatalab/MinerU/mineru-4.0.10-released/docs/en/usage/model_source.md) CLI/SDK/API、配置与缓存有明显 3.x→4.0 变更，需隔离适配。[迁移说明](https://raw.githubusercontent.com/opendatalab/MinerU/mineru-4.0.10-released/docs/en/reference/migration_4.md) |

后续候选环境必须独立 pin 包版本、完整依赖、模型 repo/revision/hash、runtime 与实际设备；禁止直接使用主业务 venv 或继承现有配置。即使禁用了远程模型，也需在断网环境验证缺失权重明确失败与解析不外传。上述官方“可离线”描述并未替代本项目的网络行为验收。

PaddleOCR 条件试验也需单独准备 PaddlePaddle/runtime 与指定模型文件；CPU/GPU 能力取决于所选 OCR、结构或 VLM 路径，不能把普通 OCR 的 CPU 能力扩写为所有复杂文档 VLM 都具有相同成本。[PP-StructureV3 使用文档](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html)、[PaddleOCR-VL-1.6 官方卡的 runtime 说明](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6)提供不同路径的要求；本轮不增加第三个通用候选。

**进入试验和采用的条件**

1. 对当前文字层/规则线框样本，以 PyMuPDF 作为基线。Docling 标准本地 PDF pipeline 首先验证来源 JSON、真实非空 cell bbox、表头与值关系；关闭不需要的 OCR/VLM/图片描述功能。MinerU 以固定 4.0.10 档位和独立配置验证输出；若所选路径没有真实 cell 坐标，按证据缺口记录，不能通过等分整表 bbox 补猜坐标。
2. native/Flash 组与模型结构组分开记录；无模型路径缺少结构识别不能算 OCR 或复杂表格方案的失败，也不能算完整表格质量通过。只对已授权且已预备的模型执行；环境未准备则标记环境阻塞。
3. 真实样本与合成边界样本分开；原页→cell→chunk→PG/向量→召回→模型实际上下文→答案/引用分别保存证据。许可、性能、识别字段完整度、正确失败和反例均为独立条件。候选官方 benchmark 排名只可作为试验动机。
4. PaddleOCR 只在已确认扫描/复杂表格真实失败场景时触发；先定义需要修复的单元格关系、文字、页与坐标验收。没有场景证据则保持条件方案。

仍未验证：真实企业 PDF 与完整 RAG 引用链、目标生产 Linux 的安装/恢复/容量、跨页与旋转语义、OCR/VLM 路径及其实际权重 revision、GPU/VRAM、MinerU 全部运行质量与资源成本、ONNX 模型整包许可，以及企业既有 PyMuPDF 许可路径。Docling 本轮限定 Linux CPU 环境、指定资产、七个合成页的结构字段与耗时/RSS 见下文；不能把这些结果扩写为真实文档质量、生产吞吐或全布局支持。

**授权后的独立 Docling CPU 实验：环境与资产实际证据**

本实验只读取 `artifacts/technology-review/synthetic-pdfs` 的既有七个合成 PDF。性能对照采用同为 Linux、network none、2 CPU/4 GiB 的 PyMuPDF 基线产物（本地证据：`artifacts/technology-review/pymupdf-linux/parsing.json`），当前业务代码未迁移。Docling 在基础 worker 完整 ID `sha256:11943d9ed8d4036b954523ee0ec4e53d476d43695605aa430af5a62c7ba26ade` 的独立派生镜像安装；基础 venv 复制到 `/opt/docling/.venv`，安装只写新路径。实验 tag 为 `itops-technology-review-docling:2.132.0`，模型与产物均在 `artifacts/technology-review/docling/`。正式 Docling 容器只挂载合成输入、独立模型/模型 manifest、实验脚本和独立输出目录。

第一次安装退出 0，但断网导入预检退出 1：PyPI `torchvision 0.29.1` 报 `RuntimeError: operator torchvision::nms does not exist`。在隔离镜像按官方 CPU 索引仅替换 `torchvision==0.29.1+cpu`，`--no-deps` 保留 `torch 2.14.1+cpu`；第二次预检退出 0。首失败留在 `preflight.log`，修正留在 `build-cpu-wheel-fix.log`，核验留在 `preflight-cpu.log`。这是实际安装负担，不是 PDF 质量失败。[官方 CPU wheel 索引](https://download.pytorch.org/whl/cpu/torchvision/)与[0.29.1 对应 PyTorch 2.14.1 的发布说明](https://github.com/pytorch/vision/releases/tag/v0.29.1)支持该根因修正；没有用 native pipeline 替代模型路径。

CPU wheel 修正后的中间镜像 ID 为 `sha256:515b63791f93dda9955500b8c64329edd7f9265fbcea4cecb8ed352f5a4e5798`。首次正式断网运行在 TableFormer 导入 `tf_predictor → cv2` 时报 `ImportError: libxcb.so.1`，setup 耗时 5.572939 s，首失败目录 `run/` 原样保留。`ldd` 确认还缺 libGL、GLib/gthread；仅在独立镜像补齐 `libxcb1=1.17.0-2+b1`、`libgl1=1.7.0-1+b2`、`libglib2.0-0t64=2.84.4-3~deb13u5`。安装实际新增 40 个系统包，下载 archives 50.1 MB、增加磁盘空间 218 MB，OpenCV 导入核验通过；版本与完整依赖解析证据为 `native-libs-fixed.txt`、`native-libs-ldd.txt`、`build-native-libs.log`。[Debian libxcb1](https://packages.debian.org/trixie/libxcb1)、[libgl1](https://packages.debian.org/trixie/libgl1)、[GLib](https://packages.debian.org/trixie/libglib2.0-0t64)为包身份的官方来源。

**最终成功镜像 ID 为 `sha256:b7cbe6cd953f4e07948a6dc932924b3cfef749506c44be3e32a8dd5689fafa5e`**，正式产物目录为 `run-with-native-libs/`。实际依赖：Python 3.11.16、Docling 2.132.0、docling-core 2.99.0、docling-ibm-models 4.0.3、docling-parse 7.22.1、Torch 2.14.1+cpu、torchvision 0.29.1+cpu、Transformers 5.18.0、huggingface-hub 1.33.0、PyMuPDF 1.28.2。完整包清单在 `run-with-native-libs/environment.json`；模型预取 manifest 保留修正前的包版本，不能用它替代最终环境。以上是本次 resolver 的实产物，未来仅 pin 顶层包不会自动保证相同传递依赖。

| 实际准备的资产 | immutable revision | 文件总 bytes / 主要权重 SHA256 | 固定 revision 的许可证据 |
| --- | --- | --- | --- |
| Heron Torch 布局 | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | 171,665,927；权重 `00333a43451945aaf89db8ca9c0a17e75d1537c17db60fdb91aa95f4c7929e0c` | 固定 README metadata 为 Apache-2.0。[revision 模型卡](https://huggingface.co/docling-project/docling-layout-heron/blob/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8/README.md) |
| TableFormer V1 accurate | `v2.3.0` resolved `fc0f2d45e2218ea24bce5045f58a389aed16dc23` | 212,768,861；权重 `2a7d6c924b3cd12fb99a09280ca9c33a89c5d60b93253617d2e088c1a40374d9` | 此旧 revision README metadata **仅写 CDLA-Permissive-2.0**；最新卡的双许可声明另列于前文，固定 revision 的文件原样保存，不能把当前卡内容冒认为该旧 README。[revision 模型卡](https://huggingface.co/docling-project/docling-models/blob/fc0f2d45e2218ea24bce5045f58a389aed16dc23/README.md) |

共准备 384,434,788 bytes，包含必要 config 与 README；没有预取 OCR、VLM、ONNX 变体、图片分类或 code/formula 权重。Docling 默认安装包仍带 OCR 等可选功能的依赖，关闭运行功能不等于最小包依赖。准备容器没有挂载 PDF，只进行匿名公开资产下载，退出 0；每个文件的大小和 SHA256 见 `model-manifest.json`。

Docker 实际总可用内存 `8,218,816,512 bytes`（约 7.654 GiB）。下载、CPU wheel 与 native libs 修正阶段限制 2 CPU/2 GiB；成功的正式推理限制 **2 CPU/4 GiB、无 swap、network none**，在重排模型容器退出后执行。输入 `/inputs`、模型 `/models`、准备好的模型 manifest 与冻结执行脚本均以只读方式挂载，输出另挂 `run-with-native-libs/`。调用与实际 mount/cgroup 证据保存在 `run-native-invocation.json`、`run-native-mounts.json`、`run-native-container-settings.txt`、`run-native-state-final.json`。每个模型文件在运行前重新核对 SHA256；禁用 OCR、远程服务、外部插件、图片分类/描述与 code/formula enrichment，使用 CPU `StandardPdfPipeline`、Heron Torch、TableFormer V1 accurate、`do_cell_matching=True`，未使用 native pipeline。

**同字节、同 Linux 限额的实际结果**

成功容器运行时间为 2026-10-02 08:01:45–08:02:32 UTC，退出 0、`OOMKilled=false`。七个输入各保存 1 次 setup + 3 次 warm，共 **28 个完整 DoclingDocument JSON 与 28 个 evidence JSON**；每个样本的四份完整 JSON hash 相同。七张原页 PNG 已逐张核对，明确区分合成边界样本与真实企业文档。

同输入比较记录（本地证据：`artifacts/technology-review/docling/same-input-comparison.json`）和 `same-input-linux-comparison.json` 已统一使用 `D:/wendangjiexi/artifacts/technology-review/pymupdf-linux/parsing.json`，该文件 SHA256 为 `cbc51d563bc68777932253fea10ea772cad635848699581de97ca073d680816f`；Docling `results.json` SHA256 为 `51780f763c4e85d04e6f8b87aa9f929faf63e88d1ba441c4a56a4f154d2fc5ed`。每个 case 的 Linux 基线、Docling 输入与当前 fixture SHA256 **7/7 相同**。此前 Windows 时间比较保存在 `same-input-comparison.windows-superseded.json`，明确标为 superseded，不进入下表或结论。

| 合成样本 | Linux PyMuPDF 实际状态 / 3 次中位秒 | Docling converter 实际状态 / 3 次热中位秒 | Docling 实际结构 | 依据真值的评审判定 |
| --- | --- | --- | --- | --- |
| ruled-table | accepted / 0.106033 | success / 1.524949 | 1 个 3×3 table；9 cells、9 bbox；9 个文字与行列 offsets 正确；column_header 全 false | 已声明支持边界内，表头角色合同失败；其余已列字段保留。 |
| prose | accepted / 0.034098 | success / 0.838789 | 0 table；5 句原文合成 1 个 text item，页/bbox 存在 | 本合成样本的文字与来源检查通过；不是完整 RAG 验收。 |
| merged | rejected / 0.108466 | success / 1.545136 | 1 个 3×3 table、9 cells/bbox；所有 row_span/col_span=1，未表达原页首两列合并的表头；header 全 false | 超出现有合同；没有实际自动拒绝，不能计为成功扩展支持。 |
| borderless | rejected / 0.039935 | success / 0.839055 | 0 table/cell；表头和值成为 1 个平铺 text item，文字中的“分钟”仍在 | 无框行列关系未结构化，需人工复核。 |
| mixed-supported-and-borderless | rejected / 0.135888 | success / 1.549157 | 只结构化中文线框表的 9 cells；英文 Team/Time/Unit、Low/240/min、Critical/999/min 成为普通 text items | 无框部分没有 table/cell 合同，不能因线框部分成功而接受整页。 |
| scan-with-footer | rejected / 0.009461 | success / 0.823163 | 0 table/cell；仅 text“第2页”，实际 provenance page_no=1；图片正文未恢复 | OCR 关闭的扫描路径内容缺失；直接以 success 发布将误接受。 |
| tiled-scan | rejected / 0.011724 | success / 0.835316 | 0 table/cell；仅 page_footer“Page 1”，四幅图片中的 Critical response: 15 min 未恢复 | OCR 关闭的扫描路径内容缺失；直接以 success 发布将误接受。 |

Docling **converter 自动拒绝 0/7、实际 success 7/7**。表中的“需复核”“合同失败”以及脚本 `boundary_disposition` 来自评审的已知合成真值，不是模型自动拒绝功能；业务接入/拒绝门禁尚未实现。五个越界样本不纳入质量胜出统计，两类扫描结果也不能用于评价尚未运行的 OCR 路径。

规则表格的真实值示例：page_no=1，首行 cell texts 为“服务等级 / 响应时间（分钟） / 适用范围”，数据行是“普通 / 240 / 一般问题”“紧急 / 15 / 业务中断”；第一行三个 `column_header` 与其余六格均为 false。`响应时间（分钟）` cell bbox 为 `[180,119,252,129.8]` **TOPLEFT**；`15` cell bbox 为 `[180,173,198,183.8]` TOPLEFT。这些是文字位置 bbox，均落在相应已知原页网格内，不等于覆盖整格的边框矩形。table provenance bbox 为 `[43.8462944,732.9256287,550.8744507,650.1722717]` **BOTTOMLEFT**，适配需保留并显式处理坐标原点。

“适用条件：工作日。注：响应时间不等于解决时间。”原样保留在 `section_header` text 中，bbox `[45,797,345,785]` BOTTOMLEFT、page_no=1；本样本没有独立 `footnote` item，也没有实现条件/注释向业务 table/chunk 的绑定。不能将 literal 存在扩写为最终答案保真。

首次规则表格 `convert()` 含 lazy pipeline/model 初始化，耗时 **7.783741 s**；每 case 的三次热耗时与 RSS 均保存在 `runs.json`。最终进程 RSS 1,309,048,832 bytes，`ru_maxrss` 峰值 1,508,581,376 bytes；cgroup `memory.peak` 1,356,242,944 bytes，`oom/oom_kill=0`。两个内存指标采用不同记账口径，分别保留。计时只涵盖抽取/转换函数，排除进程 import、原页渲染与 JSON 序列化；这些小型合成页的单页延迟不是生产 ingest/RAG 吞吐。

实际执行来源为 冻结脚本（本地证据：`artifacts/technology-review/docling/run-script.py`），其 SHA256 `e3b3b20415de375696415aac5d354028e1310a0f7b35ca05547b585d87174a8d` 与调用 manifest 相符；可执行入口为 [实验脚本](../../experiments/technology_review/docling_candidate.py) 的 `prepare/run` 与 [独立 Dockerfile](../../experiments/technology_review/Dockerfile.docling)。完整结果见 results.json（本地证据：`artifacts/technology-review/docling/run-with-native-libs/results.json`），逐页、cell、header、单位、注释和 bbox 保留在各 case 的 `document-0..3.json` 与 `evidence-0..3.json`。本轮没有补猜表头或 cell 坐标，没有写入业务数据库/索引，也没有迁移业务解析；正式采用仍需表头/失败门禁、真实样本及完整引用链的独立验收。
