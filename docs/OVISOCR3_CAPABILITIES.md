# OvisOCR3 模型卡调研与轻量 MCP 工具设计

核对日期：2026-11-10。调研对象为官方 `ATH-MaaS/OvisOCR3` 模型卡、任务 prompt 和 Halogen NPU 文档，而非仅依据模型名称推测能力。

## 证据与结论

- [官方模型卡（固定版本）](https://huggingface.co/ATH-MaaS/OvisOCR3/blob/5b252386ba615bd464a5a26597560eaba87dae5c/README.md)
- [官方任务 prompt](https://huggingface.co/ATH-MaaS/OvisOCR3/tree/5b252386ba615bd464a5a26597560eaba87dae5c/prompts)
- [跨页表格 prompt](https://huggingface.co/ATH-MaaS/OvisOCR3/blob/5b252386ba615bd464a5a26597560eaba87dae5c/prompts/crosspage_table.txt)
- [官方文档级递归实现](https://huggingface.co/ATH-MaaS/OvisOCR3/blob/5b252386ba615bd464a5a26597560eaba87dae5c/document_crosspage.py)
- [Halogen：Your own OvisOCR3](https://github.com/peonist-ai/halogen-flash-server/blob/main/docs/NPU.md#your-own-ovisocr3)（可变文档，按上述日期核对）

模型卡明确列出五个能力域。它们共享 0.8B 模型，通过专用 prompt 选择任务。当前轻量服务原先只发送通用文字/Markdown prompt，不能因此宣称已经覆盖全部能力。

| 模型能力域 | 官方输入与输出约定 | 本次暴露方式 |
|---|---|---|
| 文档解析 | 单页图片；Markdown、含 bbox/category/content 的结构化 JSON 或仅版面 JSON | `analyze_document` 的三种 document 任务；原 `process_document` 保持兼容 |
| 通用 OCR | 纯文字、词级定位、引用定位、区域 OCR、公式、图表、翻译、分类、VQA、带定位 VQA | `analyze_document.task`，条件参数显式传递 |
| 关键信息提取 | 开放键值、指定字段、按输出示例提取 | 独立 `extract_information`，区分 fields 与 json_template |
| 文档图像描述 | 完整页面；含定位的图像/图表描述，流程图可附 Mermaid | `analyze_document(task="figure_captioning")` |
| 跨页表格重建 | 两张相邻页面图片及各页解析 Markdown；返回 HTML 表格与 input-local 来源页 | `reconstruct_table` 处理双页；可选 `reconstruct_document_tables` 递归处理长表格，均保留实际 PDF 页码 |

### 重要的模型接口细节

1. bbox 为归一化 `[left, top, right, bottom]`，整数范围 `[0,1000)`。不能与原工具像素单位的 `[x,y,width,height]` 裁剪混用。裁剪后坐标属于裁剪图，渲染 PDF 后属于渲染图。
2. Markdown 表格为 HTML，公式为 LaTeX；不要转换成普通 Markdown 管道表格后丢失 rowspan/colspan。
3. 结构化内容/版面 JSON 有自然阅读顺序。图像引用如 `images/bbox_...jpg` 描述需要从输入图创建的裁剪，模型并不生成文件。
4. 图像描述是对视觉区域内容的生成式描述，不等于提取印刷图注；其中的事实需要核对。VQA 亦不能当作可验证的 OCR 原文。
5. KIE 的 `schema_json` 实际是期望输出对象的示例，不是正式 JSON Schema，也不保证约束解码。字段/模板输出应由调用方进一步做业务校验。
6. 图表数值可以是字符串，缺失元数据可能是字符串 `"None"`；不要擅自把所有值转换为浮点数/null。Position-aware VQA 的 bbox 是字符串，与其他任务的数组形式不同。
7. 模型卡示例采用 `temperature=0`、`max_tokens=16384`，每次 1–2 张图。Halogen 文档也确认支持 1–2 张图，默认思考关闭，密集页可能需要 9000 以上输出 token。
8. 供应商没有提供真实 OCR 置信度；继续使用 `confidence=null`，不将启发式分数冒充置信度。

## 为什么新增三个工具，而非恢复旧版工具组

默认 `basic` 仍只暴露 `process_document`、`get_status`。`document` 档位增加 `analyze_document`、`extract_information`、`reconstruct_table`，也可用 `OCR_MCP_TOOLS` 显式选择其中任意工具。

这样按调用契约拆分：普通识别、单页任务、字段提取、跨页重建。不会把每个 prompt 都包装成一个独立工具，也不会把二阶段双图片请求藏在普通单页 OCR 中。

| 旧工具 | 本次是否恢复 | 原因 |
|---|---|---|
| manage_image | 否 | 解码、归一化、PDF 渲染已经在服务内完成，坐标需求通过任务参数表达 |
| manage_workflow | 否 | 远程调用与双页两阶段编排在实现内完成，无须暴露本地模型/流水线管理 |
| manage_corpus | 否 | 索引、检索、持久化与模型 OCR 能力无直接依赖 |
| ingest_book | 否 | 章节、EPUB 等属于书籍业务层 |
| execute_agentic_workflow | 否 | DSH 可编排调用；OCR 请求始终不传 tools 给 OvisOCR3 |

`get_status` 返回实际暴露工具清单，避免调用方仅凭 profile 名称猜测能力。使用自定义名单时未知工具名导致启动失败，防止拼写错误静默扩大暴露范围。

## 跨页表格：实际实施范围

模型卡明确规定两阶段：

1. 分别对两页使用官方 document_markdown prompt；即使 PDF 有数字文字，也要完整渲染，因为纯文本提取会丢失表格结构。
2. 两页都得到 HTML 表格后，同时发送两张图和 `<INPUT_PAGE index="1">...` / `<INPUT_PAGE index="2">...` 包裹的各页 Markdown，使用 crosspage_table prompt。
3. 校验返回 JSON、来源页和 HTML 表格包装。保留原始阶段输出，把 `[1,2]` 的局部来源映射到调用方指定的实际页码。
4. 无续表不是异常：官方负例为 `[{"source_pages":[],"html":"No cross-page table exists between these pages."}]`。第一阶段未在两页同时识别出 HTML 表格时，也不会启动重建；这不证明原件一定没有跨页表格。
5. 上游超时、JSON 错误或输出 token 截断时不输出“成功合并”。保留原始输出便于检查，不伪造遗漏内容。

**`reconstruct_table` 仅支持一个相邻双页边界。** 页码必须如 `[2,3]`，不能 `[1,3]`、倒序或直接传三页。文档级扩展使用另一个工具，不混淆双页工具的契约。

三页以上的官方算法不是简单地独立合并 `[1,2]` 和 `[2,3]`。它从连续表格页末尾向前递归：先合并 2/3 页，把合并表格写回较早页、重新渲染更新后的 Markdown，再与第 1 页配对，持续维护来源。官方参考实现额外安装 BeautifulSoup、markdown-it-py、Playwright/Chromium。

基础安装仍不引入浏览器。显式安装 `crosspage` extra、启用同名 profile 后，新增 `reconstruct_document_tables`：使用固定版本的官方递归核心和渲染器，注入 HTTP 推理后端。支持 Markdown/结构化 JSON 两种第一阶段、倒序合并、更新页重新渲染、图片别名恢复、实际 PDF 页码映射与裁剪导出。

官方完整流水线保守地将较早页最后一个表格与较晚页第一个表格绑定；同一对页面出现多个待合并表格时，缺少显式片段绑定会停止，不能宣称具备通用匹配算法。所有原始阶段结果和中间诊断保存在独立服务端目录。阶段一截断/完整前缀恢复、字段异常、公式降级以及阶段二失败均返回部分状态。不会把可恢复内容或清理后的文本宣称为完整识别。

## 已验证与尚未验证

已实现：固定版本 prompt 随 wheel 打包，Apache-2.0 原始许可证和来源一同保留；不下载权重，不添加基础依赖。针对格式、参数、工具名单、JSON 错误、数字 PDF 强制视觉任务、双图请求、真实页码映射、负例与失败路径提供自动化测试。基本/文档两档均验证真实 TCP 上的 MCP 工具发现及调用流程，识别上游为模拟 Halogen。可选扩展另以实际 Chromium 验证渲染与递归图片更新。

这些测试验证接口和编排，不能验证真实 NPU 设备放置、识别精度、表格单元格恢复、字段正确性、生成描述真实性或长表格质量。JSON 校验覆盖语法、顶层类型及部分字段/bbox，不是完整业务契约或 HTML 语义验证。

下一步真实联调应至少包含：中英文混排、合并单元格、跨页重复表头、无续表负例、不同表格恰好位于相邻页、两张连续表格图片、三页以上边界范围说明，以及密集表格的超时/截断。返回 HTML、Mermaid 及图像引用应按不可信内容处理，渲染/裁剪由消费方另外实现。未经原图确认，不应用于关键财务/合同决策。

## 官方脚本与 18 个模板的逐项核对（2026-11-10）

本次固定版本仍为 `5b252386ba615bd464a5a26597560eaba87dae5c`。全部 18 个 prompt 与官方逐字一致，因此没有重新覆盖模板。增加 SHA256 清单随 wheel 打包以便离线核对。

针对实现完成的修正：

- 使用官方重复尾部清理规则与 Markdown fence 适配，保留原始输出；不是凭空修复缺失内容。
- 使用官方结构化 JSON → Markdown 投影，保留所有类别及阅读顺序。无效字段与完整前缀恢复均带诊断，不静默丢弃可用内容，也不返回虚假完整状态。
- 根据模板核对类别/bbox、图表字符串数值与双轴标题、带定位 VQA 字符串 bbox、引用标签、分类候选以及 KIE 字段/输出示例结构。不把模板检查当作识别准确率。
- 双页返回 HTML 内的图片引用现在还原为实际 PDF 页码，并拒绝未指明来源、越界来源、嵌套/多个表格冒充单表等情况。
- 使用官方 `_run_document`、Renderer 与本地 MathJax 支持可选文档级递归流程。源文件保持原字节并记录哈希与 Apache-2.0 来源；所有模型调用通过显式注入的 HTTP 后端执行，不会实例化 VllmBackend。
- 真实 Chromium 验证涵盖 rowspan、LaTeX、图片别名、脚本/事件移除，以及三页倒序合并时图片真实更新、最终图片来源还原和裁剪文件导出。推理服务依旧模拟，未宣称已完成真实 Halogen/NPU 精度验收。

基本安装、文档任务和可选浏览器测试分开运行。部署前仍应使用实际复杂报表核对表格边界、重复表头、合并单元格和来源。渲染器的资源/可读性阈值是工程限制，不是已验证的 OCR 准确率保证。
