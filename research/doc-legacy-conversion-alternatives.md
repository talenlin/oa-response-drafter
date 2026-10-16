# 旧版 `.doc` 本地转换替代方案（Windows）

> 历史方案调研。v1.0.1 已采用 Word → office_oxide 0.1.15 → 可用 LibreOffice 的实际转换路径，使用说明见仓库 README。

## 结论

可以移除内置 LibreOffice。对于 OA 阅答当前的 Python 本地桥接器，建议采用 **两级方案**：

1. **优先调用用户已安装的 Microsoft Word**，使用 `SaveAs2` 导出 DOCX 或可检索 PDF；这不会增加安装包体积，且由 Word 自身完成版式、表格和图片转换。
2. 未检测到 Word 时，显示“安装文档兼容组件”的明确入口。该组件建议选 **Aspose.Words for Python via .NET（已购正式授权）**；它直接接入现有 Python 服务、无需 Word/LibreOffice，并能读取和保存 DOC、DOCX 与 PDF。

不能把无授权的 Aspose/GemBox/Syncfusion/Spire 作为默认兜底：它们的免费或试用模式会截断、加水印或插入试用标记，无法用于专利申请材料。也不能仅用 Pandoc、Antiword、wvWare 或 Apache POI 来替代：这些方向至多适合文本提取或低保真处理，不能可靠实现“旧 `.doc` → DOCX/PDF”。

## 候选对比

| 方案 | `.doc` → DOCX/PDF | 表格/图片与版式 | 用户侧依赖 / 安装包影响 | 许可与分发 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **Microsoft Word COM 自动化** | Word 的 `SaveAs2` 可指定 `wdFormatDocumentDefault = 16`（DOCX）或 `wdFormatPDF = 17`。 | 最接近用户本机 Word 的保存效果；应以用户实际文件抽样验收。 | 不打包转换器；但要求该用户已合法安装 Word。 | 不能随 OA 阅答分发 Word；仅调用已安装 Word。 | **零增量体积的首选路径**，必须保留“未安装 Word”提示和替代组件入口。 |
| **Aspose.Words for Python via .NET** | 官方列明 DOC 可加载/保存，DOCX 与 PDF 也可保存；不依赖 Word 或其他 Office。 | 官方称 DOC/RTF/DOCX 等方向转换为高保真；同时明确复杂 DOC 的 shape、text box、OLE、字段、修订等需用实际样本验收。 | 直接接入现有 Python 桥接器；不需要 Word/LibreOffice。实际打包体积需按目标 Python/Windows 构建测量，官方文档未给出固定包大小。 | 商业授权；未授权试用会加评估水印并限制文档长度。 | **完整兼容组件的首选**：接口最匹配当前项目，兼顾转换质量和用户零 Office 依赖。 |
| **GemBox.Document** | 可读 DOC、DOCX，输出 DOCX/PDF；独立 .NET 库，不依赖 Word。 | 具备表格、图片、shape、chart 等对象模型，且声明会保留不支持的内容元素；PDF 仍应以专利样本验收。 | 需增加一个 .NET 小型 sidecar/worker；实际增量须构建测量。 | 免费模式仅 20 段落；专业版为按开发者授权（官网当前单开发者价 $890），且 EULA 明确 DLL 可作为应用的一部分再分发。 | **可行备选**，但对 Python 项目新增跨运行时边界，优先级低于 Aspose。 |
| **Syncfusion DocIO + DocIORenderer** | DocIO 官方格式表列出 DOC 输入与 DOCX、PDF 输出。 | 支持图表、图片等转换选项；官方也列有“不支持元素”，需样本验收。 | 需 .NET sidecar；PDF 还需要 DocIORenderer，且从 v24.1 起另需 `Syncfusion.Pdf.Imaging`。 | 需有效许可证；NuGet/试用也要求注册许可证键以避免 trial watermark。 | **可行但依赖较多**，不适合只为一个转换功能压缩分发体积。 |
| **Free Spire.Doc** | 官方称可转换 DOC/DOCX 到 PDF 等。 | 有 Word 对象支持，但免费版限制 500 段落/25 表；PDF/XPS 仅前 3 页。 | 无 Word 依赖；真实体积须测量。 | 声称可商用免费，但上述硬限制使专利材料通常不可用。 | **不推荐**。 |

### 应排除的轻量工具

- **Pandoc**：官方输入格式列表包含 `docx`，不包含二进制旧 `.doc`；不应作为旧 DOC 转换器。
- **Apache POI/HWPF**：可访问旧 DOC 的结构并写回 DOC，但其官方 Word 组件说明将 DOC 视作长文本缓冲区；没有 DOC→PDF 渲染或可靠 DOC→DOCX 转换能力。适合有限数据处理，不适合专利文档保真转换。
- **纯文本提取器**：可以作为“仅提取文字以供 NotebookLM 分析”的降级路径，但不能产出用户要求的 DOCX/PDF，且图、表、批注、页眉页脚、位置关系都会丢失。

## 设计建议

1. 把现有约 662 MB 的 LibreOffice 从默认安装包移除；对 `.doc` 的默认流程改为“先检测 Word → 使用 Word 转换；否则提示安装兼容组件”。
2. 转换默认输出 **DOCX**，并将其存到原材料同目录的 `notebook-upload`：DOCX 保留可编辑性，也能作为 NotebookLM 单一来源文件。只有用户选择“固定版式存档”时再另存 PDF。
3. 每次转换保留：原 `.doc`、生成 `.docx`/`.pdf`、转换日志（转换器、版本、时间、失败原因）。上传时只发送生成文件，避免 `.doc` + 转换件重复。
4. 对 Word、Aspose、GemBox 各准备一组真实 OA 样本（含流程图、复杂表格、页眉页脚、修订痕迹、中文字体），做页数、图片数、表格数和人工视觉核对后再选定引擎；“高保真”不能替代本案样本验收。

## 一期推荐实施顺序

1. **立即减包**：移除默认 LibreOffice，先实现“已有 Word 时本机 COM 转 DOCX”。
2. **无 Word 的用户**：提供可选的“文档兼容组件”。如采购商业授权，优先评估 Aspose.Words for Python via .NET；先用 10–20 份真实专利材料做对比测试和最终打包体积测量。
3. 不把免费 GemBox、免费 Spire 或文本工具当正式的 DOC 兼容路径；它们可以作为开发测试或只读文字降级，不能静默用于 OA 关键材料。

## 一手资料

- [Microsoft Word `SaveAs2`](https://learn.microsoft.com/en-us/dotnet/api/microsoft.office.interop.word._document.saveas2?view=word-pia)；[`WdSaveFormat`：16 = DOCX，17 = PDF](https://learn.microsoft.com/en-us/dotnet/api/microsoft.office.interop.word.wdsaveformat?view=word-pia)
- [Aspose.Words Python via .NET：支持格式](https://docs.aspose.com/words/python-net/supported-document-formats/)；[无需 Word/Office 的系统要求](https://docs.aspose.com/words/python-net/system-requirements/)；[复杂文档转换保真说明与风险点](https://docs.aspose.com/words/net/file-formats-and-conversions/)；[试用限制与授权](https://docs.aspose.com/words/python-net/licensing/)
- [GemBox.Document 功能与无 Word 依赖](https://www.gemboxsoftware.com/document)；[免费模式 20 段限制](https://www.gemboxsoftware.com/document/examples/free-trial-professional/1301)；[再分发条款](https://www.gemboxsoftware.com/EULA.pdf)；[专业授权价格](https://www.gemboxsoftware.com/document/pricing)
- [Syncfusion DocIO 格式支持](https://help.syncfusion.com/document-processing/word/word-library/net/support-file-formats)；[PDF 转换与输出选项](https://help.syncfusion.com/document-processing/word/word-library/net/conversion)；[PDF 所需程序集](https://help.syncfusion.com/document-processing/word/word-library/net/assemblies-required)
- [Free Spire.Doc 限制](https://cdn.e-iceblue.com/Introduce/free-doc-component.html)
- [Pandoc 官方输入格式列表](https://github.com/jgm/pandoc/blob/main/MANUAL.txt)；[Apache POI 的 HWPF/XWPF 说明](https://poi.apache.org/components/document/index.html)
