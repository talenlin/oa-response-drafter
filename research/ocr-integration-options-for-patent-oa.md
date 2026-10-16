# 专利 OA 工作台的 OCR/文档解析集成选项

调研日期：2026-08-09。本文只引用厂商官方文档、官方仓库或其许可证；结论应在用真实 CN/US OA、申请文件和对比文件的标注样本集验收后再定版。

## 结论

可以，而且不应把产品锁死在 MinerU。建议本地桥接器定义可插拔的 `DocumentParser` 接口，并以 **PaddleOCR PP-StructureV3 为默认本地 OCR/版面解析器，Docling 为解析与 OCR 回退层，MinerU 为经许可审查和样本验证后可选的高复杂度增强器**。三者均可在用户电脑或企业私有环境运行，不必将原始文件发送到 OCR 云。

云 OCR 应仅作为用户或企业显式开启的“质量兜底”：首选 Google Document AI 或 Azure Document Intelligence；不建议 AWS Textract 用于中国 OA，因为其官方支持语言列表不含中文。Mistral OCR 的结构化输出和图形框很强，但目前是云 API，和“本地优先”边界不符。

无论选哪一家，图纸的自动文字/图号识别只能作为定位线索；“技术特征存在于何处、差异为何不显而易见”的法律技术判断必须回到原页并由代理师复核。

## 对比

| 方案 | CN/EN 与扫描件 | 版面、表格、图纸 | 部署/API | 商业与适配判断 |
|---|---|---|---|---|
| **PaddleOCR PP-StructureV3** | 官方说明默认中英模型，亦可换专用英文模型；有方向矫正、文本去畸变。 | 复杂版面、阅读顺序、表格、公式、图表理解；输出 Markdown/JSON、文字/单元坐标。 | 本地 Python/CLI，且有多语言服务化部署路径。 | Apache-2.0；**默认首选**。需以专利样本特别测试英文小字、权利要求编号和附图标号。 [官方文档](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html) / [许可证](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/LICENSE) |
| **Docling** | 本地框架，OCR 引擎可替换；其 EasyOCR 路径支持简中+英文，亦能接 Tesseract、RapidOCR 等。 | 统一文档对象；版面、阅读顺序、表格、公式、图像分类及 bbox；可导出 Markdown、JSON、HTML。 | 本地库或 `docling-serve` REST/FastAPI；可预下载模型离线运行。 | 代码 MIT（但仍须逐项核对所选模型许可证）；**最佳回退/编排层**，也适合做统一输出格式。 [项目仓库](https://github.com/docling-project/docling) / [OCR 引擎](https://docling-project.github.io/docling/concepts/OCR/) / [服务部署](https://docling-project.github.io/docling/usage/api_server/deployment/) |
| **MinerU** | 自动识别扫描/乱码 PDF 并 OCR，官方称支持 109 种语言。 | 强项是多栏、图片/图描述、表格/表题、公式；产出按阅读顺序 JSON、Markdown 和可视化结果。 | 本地 CLI、FastAPI、WebUI；CPU/GPU/MPS 支持。 | 许可证为基于 Apache-2.0 的 **MinerU Open Source License**，在线服务有显著标识义务、超阈值另需商业许可；**可保留为高难文件增强器，不宜作为无替代依赖**。 [官方仓库](https://github.com/opendatalab/MinerU) / [许可证](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md) |
| **Google Document AI** | Enterprise OCR 官方称支持 200+ 语言，含中文和英文，并提供文档质量评估。 | Gemini Layout Parser 识别段落、表格、列表、标题/页眉页脚和图形，服务于检索/RAG。 | Google Cloud API；不是本地部署。在线 PDF 单文件最多 15 页/20 MB，批处理上限更高。 | 付费云服务；若用户已接受 Google 云处理，是最匹配 NotebookLM 生态的云兜底。注意部分新版布局模型有数据驻留限制。 [OCR 处理器列表](https://docs.cloud.google.com/document-ai/docs/processors-list) / [布局解析器](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk) |
| **Azure AI Document Intelligence** | Read/Layout 抽取文本、表格、结构和 bbox；返回语言示例含英文、繁体中文。 | Layout 抽取文本、表格、选择标记和结构；适合形成页码/坐标证据链。 | 默认 Azure API；也可在本地/私有环境运行 Docker 容器，v4.0 容器限 Read 与 Layout，仍需 Azure 账户、密钥和计费端点。 | 付费商业服务；适合有 Azure 合规/采购条件的企业版，而非普通用户本地桥接器。 [概览](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/overview?view=doc-intel-4.0.0) / [容器](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/containers/install-run?view=doc-intel-4.0.0) |
| **Mistral OCR / Document AI** | 官方称 40+ 语言，支持 PDF、DOCX、PPTX 与图像。 | JSON 内含页级 Markdown、图像 bbox、表格、置信度及段落级 block/bbox；可用 JSON Schema 做文档或图形标注。 | `POST /v1/ocr` 云 API；可批处理。 | 付费云 API；适合用户明确授权的“难件升级”，不符合文件仅在本机处理的默认策略。 [OCR 文档](https://docs.mistral.ai/studio-api/document-processing/basic_ocr) / [注释能力](https://docs.mistral.ai/studio-api/document-processing/annotations) |
| **AWS Textract** | 官方仅支持英/法/德/意/葡/西文本，不支持中文；查询只支持英文文档。 | 表、单元格、标题/脚注、表单和布局均可返回为 Blocks。 | AWS API（同步/异步），不是本地引擎。 | 对纯英文 US OA 可作为企业云备选，但**不应作为中美统一 OCR 引擎**。 [语言和限制](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html) / [表格](https://docs.aws.amazon.com/textract/latest/dg/how-it-works-tables.html) |

## 推荐的产品接法

1. **先做无 OCR 的原生文本通道。** 对可检索 PDF 和 DOCX，直接提取原生文字；OCR 仅处理扫描页、乱码页和用户强制重跑页，避免凭空改写可用证据。
2. **统一不可变的页级中间格式。** 每页至少保存：`page_number`、原始页图哈希、文字块/表格单元的 `bbox`、文本、置信度或质量标记、解析器与版本。生成给 NotebookLM 的 Markdown 仅是派生物；答复界面仍应链接回原件页/坐标。
3. **默认链路：PaddleOCR → Docling 回退/规范化 → 人工质检。** 低置信度页、权利要求/引证段落未定位页、附图标号密集页进入“需确认”；不要静默送入 NotebookLM。
4. **MinerU 作为可配置的高难件路径。** 先对约 100–300 份脱敏的 CN/US 真实材料做字段级评测（权利要求号、段落号、文献号、页码、附图标号、表格），并由法务确认当时版本及其依赖模型许可证和产品界面署名要求。
5. **云兜底必须逐页、显式同意、可审计。** 用户选择供应商后才上传低质量页；记录供应商、区域、版本、页数和结果哈希。云结果同样只作为可回查的辅助文本，不能覆盖原文件。

## 需要在 MVP 前冻结的验收标准

- 分别建立中国首次/后续 OA、美国 §102/§103 OA、申请文本、对比文件四类样本；中英文均覆盖扫描、多栏、表格、公式和附图。
- 以“关键字段准确率 + 页码/坐标可回跳率 + 人工修正时间”验收，而不是只比较整体 OCR 字符准确率。
- 把 `claim 1`、段落号、OA 结论段、引证文献号、引用页/段、附图标号设为必测字段；任一关键证据无法定位时，争点卡片不得标为“可用”。
