# 共享工程资料库

资料库位于当前仓库的 `data/references/<资料ID>/`，供本仓库各设计项目复用；不访问相邻 DSH 仓库。插件工具保存和校验数据，当前 Codex 会话负责图片识读、内容精简和适用性判断，不另外调用模型 API，也不启动后台监控。

## 归档及文件

`chem_reference_import` 接受以下三种输入之一，并要求提供 `source`：

- `path`：本仓库或当前数据目录内的文件。沿用现有格式：PDF、DOCX、PPTX、XLSX、文本/Markdown、CSV/JSON、JATS XML/HTML、PNG/JPEG 和 SVG/DXF。
- `text`：用户粘贴的原文，按 UTF-8 原样保存，默认文件名为 `用户提供的原始文本.txt`。
- `url`：公开 HTTPS 的原始 PDF、HTML 或 UTF-8 文本，复用已有下载器，保留网址、获取时间、重定向及响应来源信息。

单个原件最多 25 MiB，PDF 原生提取沿用最多 600 页的范围。原生提取失败也保留已归档原件，并返回 `extraction_status=unavailable` 及原因。图片返回 `awaiting_session`，由当前会话读取。文件超限或输入路径无效时直接报错，不宣称完成归档。

`source` 的必填字段是 `title`（名称）和 `source`（具体来源说明）；可填写 `standard_number`（标准号）、`edition`（版本）、`provider`（提供者）、`url` 和 `notes`。未知信息注明待补；截图、节选或二手摘录在备注中说明资料不完整，不自行判断标准是否现行。

资料 ID 为原件 SHA256。同一原件重复导入复用已有资料；新增提供者或出处另存获取记录，不覆盖已有备注。不同原件内容生成不同 ID，包括同一标准的不同版次。来源信息可通过后续保存进行修订，历史仍保留。

每份资料包含：

| 文件 | 用途 |
| --- | --- |
| `original/` | 原始字节，保持不变 |
| `manifest.json`、`native.json` | 原件信息、哈希、带页码/段落/行号的原生文本与图像锚点；系统维护 |
| `source.json` | 当前来源与自由备注，用户可编辑 |
| `organized.json` | 当前机器可读整理稿，包含 `overview` 和 `entries`，用户可编辑 |
| `brief.md` | Markdown 精简稿，用户可编辑 |
| `revisions/`、`head.json` | 各次完整修订、时间、操作者和理由；系统维护 |
| `previews/` | 按需生成的页面图像缓存 |

直接编辑来源、整理稿或 Markdown 后，下次读取或检索自动保存修改版本。格式无效时保留编辑文件并报错；可指定旧 `revision` 读取历史，修复当前文件后继续使用。两次读取之间的多次手工保存只能记录最终可见差异；需要逐次留痕时，每次编辑后读取一次。

缺失的可编辑副本可从历史恢复；原件和原文层被修改会触发哈希错误。`.pending.json` 是中断恢复日志，正常发布后自动移除，请先读取恢复，不要手工删除。共享库使用进程锁和原子发布保护多个会话的写入；同项目的工程状态仍遵循仓库已有的单进程写入约定。

## 阅读与整理

`chem_reference_read(reference_id, revision?, view, page?, block_ids?, offset=0, limit=10)`：

- `metadata` 返回来源、获取记录、状态、文件路径及整理进度。
- `source` 返回原文块及定位，长块分段保留。`next_offset` 非空时继续读取。
- `organized` 返回概览、整理条目与 Markdown 摘要；完整 Markdown 在返回的文件路径中。大型表格的完整行列保存在 JSON 文件，工具响应会明确说明行列未展开。
- `image` 返回指定页 PNG，并通过 MCP 的原生图片内容交给当前 Codex 会话。支持 PNG/JPEG、PDF；Office 版面预览需要本机可用的 LibreOffice。PDF 即使已有原生文字，也可以读取整页以识别混合页面上的图表。

`progress` 记录总块数、已关联整理的块数及尚未整理的块 ID。页面图像也有独立锚点，因此已有文字摘要不表示图表已经识读。长资料先保留全文及页码索引，保存总体概览，整理当前任务相关部分；以后再补其他章节，不要求首次逐页精简。

`chem_reference_save(reference_id, expected_revision, reason, source?, overview?, entries?, remove_keys?, brief?)` 按稳定条目 `key` 合并保存。每条包含：

| 字段 | 用途 |
| --- | --- |
| `key` | 稳定条目标识，支持字母、数字、点、下划线、横线，最多 48 字符 |
| `kind` | `summary`、`transcription`、`table`、`formula` 或 `figure` |
| `text` | 精简文字、公式或逐字转录，不超过 16000 字符 |
| `source_block_ids` | 原文或图像锚点，至少一个，须属于本资料 |
| `cells` | 可选的表格行列数组 |
| `conditions`、`notes` | 适用条件、例外及待核对说明 |

图像中的实际可见文字使用 `transcription`，绑定图像/页面锚点，不能夹入解释或推测；不清楚处标 `[不清]`。整理说明使用其他类型，保留重要数字、单位、公式变量、适用条件、例外和条款位置。所有图像转录均标为待核对。

默认从结构化条目生成 Markdown；用户直接编辑过的 Markdown，或显式传入 `brief` 后，后续更新结构化条目会保留该稿。需要同步更新时显式提供新的 `brief`。修改来源、内容或摘要均不改变原件和原文层。版本冲突返回 409，需重新读取合并，不能反复提交旧版本。

## 检索及项目引用

`chem_reference_search(query="", offset=0, limit=10)` 检索整个共享库，包含来源备注、概览、Markdown、原文及整理条目，返回版本与定位。空查询列出资料。文件校验失败的资料在 `errors` 中列出，其他有效资料仍可检索。相关性用于查找，不代表适用性或标准效力；无固定来源优先级。

使用时按“检索 → 整理稿 → 回看原文/图像 → 固定引用”的顺序。

`chem_reference_use(project_id, reference_id, revision, block_ids, purpose)` 固定选定版本，将原件、来源、整理稿和引用片段复制到项目。原文 ID 为 `b...`，图像逐字转录为 `t-<key>`；`s-<key>` 整理稿及空图像锚点不能冒充原文证据。

返回 `artifact_id`、`snapshot_id` 及带定位的 `citations`，可以直接交给现有 `document_read`、`evidence_add` 或设计依据对象使用。引句核对仍要求来自原文块；图像转录保留 `ocr=true` 和待核对标记。来源备注及共享资料 ID/版本一并进入证据记录。

相同项目、资料版本、引用段落集合和用途的重复调用复用项目副本。共享库之后的编辑不改变旧引用；删除项目只删除项目副本，不删除共享库。`chem_bundle` 包含 `references.json` 与实际采用的原件、来源、整理稿和片段，文件逐项列入 SHA256 清单。项目副本中的 `citation.json` 同时固定原件哈希、首次获取时间及该版本的获取来源记录，离开共享库后仍可追溯。

HTTP 对应接口为 `POST /v1/references/import`、`GET /v1/references`、`GET/POST /v1/references/{reference_id}` 和 `POST /v1/projects/{project_id}/references`。HTTP 的 `block_ids` 查询参数使用逗号分隔，MCP 使用字符串数组。

资料库为运行数据，已由 `data/` 排除在 Git 和发行包外；旧项目资料按需导入，不自动迁移。更新仓库代码后，需要重载 MCP，并刷新已安装插件、在新会话加载技能，才能使用新增工具及流程。
