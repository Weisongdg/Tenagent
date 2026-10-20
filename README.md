# Tenagent

当前阶段：基于 LangChain 的本地网页 Agent，支持新建、切换会话、上下文记忆、重启恢复，
以及问题追踪 Excel 读取、单条问题整理、本地问题库、查询统计和 Chroma 相似检索。

## 安装与启动

需要 Python 3.12 或以上版本。在项目目录的 PowerShell 中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

填写 `.env` 中的模型配置：

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_BASE_URL=
CHAT_DATA_DIR=data/chat
ISSUE_DB_PATH=data/issues/issues.sqlite
```

- `OPENAI_API_KEY`：服务密钥。
- `OPENAI_MODEL`：服务实际支持的模型名称。
- `OPENAI_BASE_URL`：可选；使用符合标准 OpenAI API 的代理或兼容服务时填写接口地址。
- `CHAT_DATA_DIR`：可选；相对路径基于项目目录，而不是启动时的工作目录。
- `ISSUE_DB_PATH`：可选；默认 `data/issues/issues.sqlite`，与聊天数据库分开，相对路径基于项目目录。

同名系统环境变量优先于 `.env`。密钥和本地会话数据已加入 `.gitignore`。

在编辑器中选择 `.venv` 的 Python 解释器，直接运行 `app.py`，浏览器打开
`http://127.0.0.1:8501` 即可使用，无需启动参数。
`app.py` 只调用功能启动入口，不包含参数解析或业务逻辑。

首次打开网页自动新建“默认会话”；侧栏可以新建、切换会话，刷新后从后端恢复历史。

## 前后端结构

```text
frontend/                 # 独立前端，只通过 HTTP API 获取数据
  index.html             # 页面结构
  styles.css             # 样式与响应式布局
  app.js                 # 页面交互、会话选择与 fetch 调用
  vendor/                # Marked 与 DOMPurify 原版文件及许可证
feature/web/
  api.py                 # FastAPI 接口适配，复用各 feature 服务
  runner.py              # Uvicorn 本地启动入口
app.py                   # 仅调用 runner
```

前端提供聊天助手、问题库和汇总报告三个页面：支持 Excel 路径入库、手动同步向量、
精确筛选与分页，以及报告预览和 Markdown 下载。现有会话数据库和问题库继续使用。
不使用 Streamlit，也不在 Python 中拼页面。前端文件由 FastAPI 的静态文件服务分发，
与 `/api` 接口处于同一来源；代码和业务职责分离，当前不额外启动前端开发服务器。
页面 Markdown 使用现成 Marked 解析、DOMPurify 清理，依赖存放在本地，无需 CDN。

接口说明可查看 `http://127.0.0.1:8501/docs`。聊天和向量同步会调用现有模型接口；
查看历史、筛选、入库和报告生成不调用模型。此版本为本机单用户应用，监听 `127.0.0.1`。
后文的 `/history` 等斜杠命令是旧终端界面的操作说明，网页中使用会话侧栏和聊天记录。

## Excel 读取器

直接运行 `app.py`，进入聊天后可以提问：

```text
请调用读取工具，读取当前 Process_Issue_Tracking.xlsx，告诉我有多少条记录。
```

模型需要支持工具调用。工具已通过根目录 `build_tools(model)` 注册；预期读取失败由 LangChain
以工具错误消息返回，供模型解释。离线测试只验证模拟工具选择后的执行，不证明真实模型一定会正确选工具。

读取结果包含文件绝对路径、工作表、字段对应列字母、来源行号和记录数量。
相对文件路径始终基于项目目录。需要在其他代码中独立读取时，调用公开函数
`feature.excel_reader.read_issues(file_path, sheet_name)` 即可，无需模型或 API Key。

### 读取范围

- 使用 `openpyxl.load_workbook(read_only=True, data_only=False)` 和 `iter_rows` 读取。
- 第 1 行是表头；支持约定的 12 列，列顺序可变化，匹配时忽略表头大小写和首尾空白。
- 保留单元格值类型、正文空白、换行、空字段和实际行号；整行无值时跳过。
- 原始文本日期、状态、疑似原因和数量描述不做清洗或推断。
- Excel 日期返回 Python 日期对象；JSON 序列化为 ISO 格式，不复制 Excel 显示样式。
- 公式返回公式文本，不执行、不计算，也不假定缓存值有效。
- 缺少、重复、未知表头或无表头列存在数据时明确报错，避免静默丢字段。
- 只读取 `.xlsx` 单元格，不提取图片、图表、格式或批注。原始文件不修改。

### 功能内部结构

```text
feature/excel_reader/
├─ __init__.py        导出 read_issues、ExcelReadError 和结果模型
├─ schemas.py         Pydantic RawIssue、ExcelReadResult
├─ reader.py          openpyxl 读取、表头映射和来源记录
└─ tools.py           @tool 包装、工具错误处理、功能级 TOOLS
```

普通函数调用：`read_issues → openpyxl → ExcelReadResult`。
聊天调用：`Agent → read_excel_issues → 同一个 read_issues → 工具结果 → 模型回复`。

## 单条问题整理器

直接运行 `app.py`，在聊天中输入：

```text
请调用单条问题整理工具，整理当前问题追踪表中 No 为 16 的问题，
分别说明问题现象、原因、措施、验证情况、缺失信息和冲突，并给出原文依据。
```

工具 `analyze_excel_issue` 的 `issue_no` 指表内 `No`，不是 Excel 行号。
默认文件和工作表与读取工具一致。编号不存在或重复时报告错误，不由模型猜选。

流程：`Agent → 整理工具 → 复用 read_issues → 找到唯一记录 → analyze_issue`
`→ ChatPromptTemplate + with_structured_output → Pydantic 校验 → 原文引用校验`
`→ Jinja2 固定卡片 → 直接展示`。
仅把所选的一条记录发给整理模型，不附带其他记录和聊天历史。复用启动时创建的模型，
通过功能级工具工厂注入，不单独读取 `.env` 或初始化另一套模型配置。

### 整理结果

- `title`：中文标题，从摘要提炼。
- `summary`：中文问题现象及原文引用。
- `causes`：原因，状态为 `suspected`（疑似）、`reported_confirmed`（原记录明确为原因）
  或 `unknown`（未说明确定性）。
- `actions`：分别列出措施，状态为 `proposed`、`in_progress`、`completed` 或 `unknown`。
- `observations`：检查观察（`inspection`）、状态恢复（`recovery`）、性质待确认（`unspecified`）。
  检查无异常、运行正常或指标恢复，不作为措施有效性结论。
- `verifications`：只记录针对措施效果的验证，状态为 `pending`、`reported_effective`、
  `reported_ineffective` 或 `unknown`，即待验证、原记录报告有效、原记录报告无效、结果未说明。
- `missing_information`：缺失或无法确定的信息。
- `warnings`：有原文依据的状态冲突或含糊信息。

摘要、每项原因、措施、观察、验证及提示均包含 `text` 和 `evidence`。
引用记录原始字段名和连续原文片段，程序检查是否确实来自当前记录指定字段。
工具还附带完整原始记录、文件、工作表、列映射，以及程序计算的 `evidence_locations`
（结论路径、引用字段、原文和单元格地址，例如 `L17`）。不让模型生成来源地址。

无依据的列表留空，并说明缺失；原记录 `done` 不代表措施已验证有效。
`reported_confirmed`、`completed`、`reported_effective` 均表示原记录的表述，不表示系统做过现场验证。
结构和原文校验不能完全证明语义正确，实际模型仍可能遗漏、误译或错误分类，需人工核对。

### 措施关联与固定卡片

观察和验证的 `action_links` 可以为空，卡片固定显示“尚无法对应到具体措施”。
有关系时，`action_index` 引用当前 `actions` 列表的序号（从 0 开始），每个关联单独提供
明确支持关系的原文证据。仅同处一个单元格、先后出现或共用设备名不足以建立关联。
Pydantic `model_validator` 检查引用范围、重复关联，以及有效/无效结论必需的关联目标；
现有原文校验也覆盖关联证据。校验能确认结构和原文存在，不能证明因果关系成立。

默认展示精简卡片：标题、日期、负责人、问题状态、问题、原因线索、处理措施、有效性、
待补充和来源；确有待核对信息时再加一行。日期保留原值，未发现四位年份时提示“年份未说明”，
不推断或修正日期。常见问题状态使用固定中文标签，原始值仍保留。
额外观察合入问题描述；未关联的状态恢复明确注明“尚无法对应到具体措施”，不变成有效性结论。
多项措施按原列表标为 `A1`、`A2` 等，单项措施不另编号；关联在单项时显示“本条措施”。
这些编号仅在当前结果内有效，中文标签和主要来源单元格由程序确定。

长引用和完整原文不再默认展开，但 `artifact` 中的 12 个原始字段、所有引用、来源和关联
完整保留；缩短的是展示内容，没有截断或删除结构化结果。历史中的旧卡片不会自动变更，
重启程序后重新生成即可使用精简格式。

原因分类要求正文有明确确认或根因认定依据才能使用 `reported_confirmed`，
仅位于 `Root cause & Possible reason` 字段不算确认。例如 No.6 的原因短语应保留为
确认程度未知，而不是当作已确认根因。原因和措施尽量简洁地译为中文，状态由模板展示。

正式卡片使用 Jinja2 `PackageLoader`、`StrictUndefined` 和固定 Markdown 模板。
整理工具设置 `return_direct=True` 及 `response_format="content_and_artifact"`：
`ToolMessage.content` 保存固定卡片，`artifact` 保存结构化结果，外层模型不再改写卡片。
当前 LangChain 版本在同轮所有工具都是 `return_direct` 时才自动结束，因此另用官方
`before_model(can_jump_to=["end"])` 中间件，在混合调用已产生卡片或整理错误后结束本轮，
不再调用外层模型；下一条用户消息正常进入聊天。
失败时直接显示工具错误，不生成成功卡片。普通追问仍由模型回答，不能替代正式卡片。
生成新卡片需调用整理工具，不能直接复用旧聊天文字仿造。

新版结构化结果带 `schema_version=2`。旧历史保留原样，不自动重新解释或迁移。
卡片和 artifact 使用现有 SQLite checkpointer 保存，查看历史和重启恢复不重新生成卡片。

整理使用现成的 `with_structured_output(IssueAnalysis, method="function_calling")`，
由 LangChain 解析、Pydantic 校验。不自建 JSON 解析或修复循环。服务需要支持相应能力；
调用、解析或引用失败时工具返回错误，不把未通过校验的结果当作成功输出，不自动重试。

```text
feature/issue_analysis/
├─ __init__.py        公开接口
├─ schemas.py         Pydantic 整理结果、状态和证据结构
├─ prompts.py         单条整理提示词
├─ analyzer.py        一次结构化调用和结果校验
├─ evidence.py        原文引用校验与单元格定位
├─ renderer.py        Jinja2 模板加载、固定标签和展示统计
├─ templates/
│  └─ issue_card.md.j2 固定卡片布局
└─ tools.py           注入模型，按 No 读取并整理一条记录
```

该工具返回的整理结果会作为工具消息保存在当前聊天会话中；正常启动时还会自动保存到独立问题库。
不批量调用模型整理、不修改 Excel、不增加风险评分。范围报告通过下方报告工具生成。

## 本地问题库

直接在聊天中输入，例如：

```text
把当前 Excel 的问题导入本地问题库。
列出问题库中的前 10 条问题。
查询 Summit 产品中原始状态为 under tracking 的问题。
生成 No.6 的卡片。
查看问题库中已保存的 No.6 卡片，不要重新生成。
查看 No.6 最近 5 次整理历史。
```

对应 `import_excel_issues`、`list_stored_issues`、`analyze_excel_issue`、`get_stored_issue`。
导入整张工作表只保存原始记录，不调用整理模型；聊天中的工具选择和普通回复仍调用聊天模型。
工具选择后，数据库操作本身不调用模型。查询已存卡片直接返回保存的固定文本，不经过外层模型改写。
尚无卡片时展示原始记录；生成成功后自动保存完整整理结果、证据定位和卡片。
历史聊天中的旧卡片不会自动迁入问题库，需重新生成后保存。

存储使用 **SQLite + SQLModel**，依赖其 SQLAlchemy 引擎、Session、事务、JSON 列和唯一约束。
两张业务表：`issues` 保存当前原始记录，`analyses` 追加每次成功整理的完整快照和卡片。
原始日期等单元格使用 Pydantic 序列化，并附带类型信息恢复，避免把日期和普通字符串混为一谈。

- 同一问题以“文件绝对路径 + 工作表 + No”识别；复制或改名后的文件视为不同来源。
- No 必须是正整数或正整数文本；整批检查缺失、非法或重复编号，失败不导入。
- 重复导入相同记录跳过；原文、原值类型、行号或列映射变化则更新，并使不匹配的历史整理标记过期。
- 过期是与**当前入库版本**比较，不会自动监视磁盘文件；修改 Excel 后请重新导入。
- 整理时若库中版本已被其他操作更新，拒绝覆盖并提示重新整理。
- 整批导入与单次整理保存均使用事务；失败回滚，未入库不会显示为保存成功。
- 原文状态和措施执行状态不由数据库重新分类；模型整理统一标注未经人工审核。
- 整理历史保留，默认读取最新一次，`history_limit` 可指定 1～100 次；导入中缺少的旧记录不会自动删除。
- 基础列表支持原始 `product`、`status` 精确筛选和分页；更多筛选和统计使用下方问题问答工具。

```text
feature/issue_store/
├─ __init__.py         公开 IssueStore、IssueStoreError
├─ models.py           SQLModel 表声明
├─ schemas.py          输入输出与原始值类型恢复
├─ repository.py       引擎、Session、查询和事务
├─ service.py          导入、来源版本判断和整理历史保存
└─ tools.py            功能内部的三个 LangChain 工具
```

根目录 `tools.py` 汇总工具；`feature/chat/runner.py` 管理问题库生命周期并注入已有 Agent。
`app.py` 继续只作启动器。数据库初次启动自动创建，导入通过聊天明确触发。
初版使用 `create_all()` 创建表；将来修改已有表结构时需采用迁移工具，`create_all()` 不负责升级旧表。

## 问题问答助手

重启 `app.py` 后直接提问：

```text
L136 有哪些问题？
哪些问题涉及螺丝安装？
查询 L136 中涉及 screw 的问题，每页 3 条。
下一页。
L136 的问题状态分布是怎样的？
各区域分别有多少问题？
```

现有 LangChain Agent 将自然语言转换为筛选参数，调用 `search_issues` 或 `summarize_issues`。
底层复用现有 SQLModel Session、SQLAlchemy JSON 字段访问、`icontains(autoescape=True)`、
`COUNT` 和 `GROUP BY`；没有新增依赖，没有修改数据库表结构，不让模型生成任意 SQL。
查询读取当前已入库的原始问题，既不重读 Excel，也不依赖是否已生成整理卡片。

- 精确条件：`file_path`、`sheet_name`、`product`、`area`、`station`、原始 `status`。
  省略来源表示查询全部已入库来源，相同 No 在不同来源中分别计数；相对路径仍基于项目目录。
- 关键词：在问题描述、原因、措施三个字段中进行字面子串匹配，英文字母忽略大小写。
  `keyword_mode=any` 表示任一词命中，`all` 表示全部词命中（可分布在不同字段）。
  `%`、`_` 等按字面字符匹配；不自行实现分词、索引或搜索引擎。
- 模型可以把“螺丝”展开为“螺丝、螺钉、screw”，但这只是关键词检索；展示实际检索词和规则，
  不把全部命中记录宣称为已确认的“螺丝安装”分类，也不保证找全语义相关记录。
- 查询总数由数据库计算，与当前页条数分开。`offset` 默认 0，`limit` 默认 10，最大 100；
  工具返回 `next_offset`，下一页保留原筛选条件。无匹配时明确说明当前条件未检索到。
- 每条明细带原始问题文本、No、文件、工作表和实际 Excel 行号；关键词命中字段还附完整原文和单元格。
- 统计支持按产品、区域、工位、原始状态分组；按原值分别统计，例如 `Close`、`close` 不自动合并。
  空值单独分组，不能据此推断措施执行和有效性。整理历史不参与计数。
- 分组同样分页；每组附最多 2 条示例及编号、行号和来源，明确示例不是该组完整列表。
- 两个工具均使用 `content_and_artifact`，固定 Jinja2 模板直接输出结果，完整结构保存在 artifact；
  沿用现有中间件防止外层模型改写总数和引用，下一轮仍可正常聊天。

```text
feature/issue_qa/
├─ __init__.py         公开查询服务和筛选模型
├─ schemas.py          Pydantic 筛选、命中原文、分页和统计结果
├─ repository.py       SQLAlchemy 参数化筛选、命中字段、COUNT、GROUP BY
├─ service.py          复用问题库存储，组合结果及原始来源
├─ renderer.py         Jinja2 PackageLoader 和 StrictUndefined
├─ templates/
│  ├─ scope.md.j2      筛选范围和关键词规则
│  ├─ search.md.j2     查询明细
│  └─ statistics.md.j2 分组数量和示例
└─ tools.py            search_issues、summarize_issues
```

## Chroma 相似问题检索

嵌入模型为 `ynu-rzko/Qwen3-Embedding-4B`，通过 `OpenAIEmbeddings` 复用现有接口和密钥。
可在 `.env` 设置 `EMBEDDING_MODEL`；本地向量保存在 `data/vectors/`。

```text
同步问题库的向量索引。
找一下 L136 有没有类似 UVI 钢珠脱落的问题，返回3条候选。
哪些问题涉及螺丝安装？
```

功能仅有 `feature/issue_vectors/service.py`、`tools.py` 两个文件。
直接使用 Chroma `add_documents()` 写入，按 `issue-{id}` 覆盖同号记录；用 `similarity_search()` 检索。
结果展示原文、问题编号和原始行号，候选数不是相关问题总数。准确统计仍使用现有 SQL 工具。
没有独立索引管理、同步状态文件、自动版本检查或新展示框架。
更新原始问题后需明确调用同步；同步会重新嵌入当前记录，不缓存未变化记录。
向量结果是最近写入的快照。当前基础版不自动清理已删除的源记录；切换问题库或嵌入模型需重新建库。

## 问题汇总报告

在聊天中输入“生成整体问题汇总报告”或“生成 L136 的问题汇总报告”。
`generate_issue_report` 复用现有 `QueryFilters` 和 SQL 分组函数，使用完整匹配记录，
由 Jinja2 模板生成 Markdown，同时保存到 `data/reports/`，不调用额外模型、不增加依赖。

报告仅包含：报告范围与来源、按产品/区域/工位/原始状态的分布、明确未关闭问题、待确认事项。
关闭汇总将 Close/Closed（忽略大小写和首尾空白）算作原记录已关闭，Open/under tracking 算作明确未关闭，
done、空值及其他状态算作待确认；三者之和等于范围内问题总数。分布仍保留字段原值，不合并不同状态写法。
待确认项包含日期不明确、负责人或分布字段缺失、关闭状态不明确，以及已有未过期整理的待补充/待核对内容。
过期整理仅标为需要重新整理，不引用旧结论；未生成整理的问题不会自动调用模型分析。
每条明细附 No、来源文件、工作表和原始行号。财年不换算为日历日期，缺少年份不补造，不计算超期。
第一版不支持按时间范围筛选，不生成措施汇总、风险评级或分类分布。

```text
feature/issue_report/
├─ service.py           读取完整范围、统计、汇总并保存
├─ tools.py             generate_issue_report 工具
└─ templates/
   └─ report.md.j2      固定报告模板
```

报告数据和数字由程序生成，沿用已有直接返回机制，外层模型不改写。空范围也会如实生成空报告。
新增 9 项测试，覆盖范围/数字、105 条完整记录、日期、空范围、旧整理排除和 Agent 固定输出；
报告阶段共 116 项测试通过。已用当前实际数据生成全库 21 条及 L136 的 8 条报告，并验证真实模型的工具调用。

## 聊天命令

| 命令 | 功能 |
| --- | --- |
| 直接输入文字 | 向当前会话发送消息 |
| `/new 项目讨论` | 新建并切换到“项目讨论”；省略标题时使用“新会话” |
| `/list` | 按创建顺序列出会话，星号表示当前会话 |
| `/use 2` | 切换到列表中的第 2 个会话 |
| `/history` | 显示当前会话已保存的消息 |
| `/help` | 显示帮助 |
| `/exit` | 退出 |

序号只用于界面选择；每个会话内部使用稳定的 UUID 作为 `thread_id`。
新建、切换和查看历史都在本地执行，不调用模型。

## 代码从哪里看

```text
app.py                       仅调用聊天功能启动入口
config.py                    加载项目 .env 和环境变量
tools.py                     build_tools(model, store) 汇总读取、整理、问题库和问答工具
feature/chat/agent.py        创建 ChatOpenAI 和 LangChain Agent
feature/chat/runner.py       配置 → 打开存储 → 构建 Agent → 进入交互
feature/chat/service.py      每轮提交新消息；通过 get_state 读取历史
feature/chat/middleware.py   已有正式卡片时结束本轮，覆盖混合工具调用
feature/chat/sessions.py     会话目录（ID、标题、创建时间）
feature/chat/cli.py          会话选择、命令解析和显示
feature/chat/prompts.py      系统提示词
tests/test_chat.py           真实 Agent + SQLite + 假模型的离线验证
tests/test_excel_reader.py   Excel 保真、模板错误、工作目录独立性和实际工具执行
tests/test_issue_analysis.py 结构化解析、证据校验、选取记录和 Agent 工具循环
tests/test_issue_card.py     精简展示、状态保留、关联约束、原始结果保留及 No.6 回归
tests/test_issue_store.py    SQLModel 事务、幂等导入、完整保存、历史过期与工具集成
tests/test_issue_qa.py       筛选、关键词、计数、分组、来源和固定回复的 Agent 集成
```

一轮聊天：`cli.py → ChatService.reply → agent.invoke → 模型 → 显示回复`。
每轮只传新消息和 `thread_id`，由框架恢复、追加并保存该会话的历史。
所有会话共用一个 Agent 对象，使用不同的 `thread_id` 隔离上下文。

`data/chat/checkpoints.sqlite` 由官方 `SqliteSaver` 管理，保存消息和执行状态。
`data/chat/sessions.sqlite` 只保存我们自己的会话目录，不复制消息，不操作 checkpointer 内部表。
退出程序时连接自动关闭。更换 `CHAT_DATA_DIR` 会使用另一套会话数据。

后续添加功能时，在对应 `feature/<功能>/tools.py` 定义工具，再由根目录
`tools.py` 的 `build_tools(model, store)` 汇总；工具工厂接收已有模型或存储服务。

## 当前边界与失败行为

- 单用户、本地、同步聊天；回复生成完成后一次性显示。
- 只使用当前会话上下文，不做跨会话的长期记忆。
- 不自动压缩或删除历史；长会话仍受模型上下文窗口限制，可新建会话继续。
- 模型调用失败时不追加虚假助手回答、不自动重试；用户输入可能已经保存，可用
  `/history` 检查。继续发送消息时保留这条失败输入，不会把它当成成功回复。
- 上游错误只展示类型和操作提示，不打印服务响应正文或密钥。
- 当前支持入库、单条整理、查询统计、Chroma 相似检索和范围报告；不做批量聊天模型整理或风险评级。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pip check
```

离线测试验证实际传给假模型的历史、会话隔离、重建连接后的恢复、失败后的历史保留，
以及命令操作和配置优先级。离线通过不代表真实模型服务已经可用。
Excel 验证还包含当前 21 条真实记录及原文件哈希、空行行号、列重排、原值类型、公式、
异常输入，以及 Agent 调用真实工具后收到的工具消息。
单条整理验证使用真实 LangChain 结构化解析器及假模型，覆盖状态字段保留、无效引用、
错误结构、模型失败、编号不存在或重复、来源定位，以及 Agent 调用整理工具的完整循环。
假模型验证不证明真实模型会正确理解 suspected、pending 等表述。
固定卡片测试还验证 unknown 保留为执行情况未说明、无关联不伪造对应措施、
工具返回后不再调用外层模型、混合工具调用不丢卡片，以及卡片和 artifact 的重启恢复。
问题库测试使用临时 SQLite 文件，覆盖 21 条实际记录的重复导入、原始值类型恢复、历史保存、
来源变化后过期、重复/非法 No 拒绝、事务中途失败回滚、版本冲突拒绝覆盖、筛选分页，
以及 Agent 读取已存卡片时不再调用整理模型。
问题库已用当前配置的真实模型抽查导入、分页列表和读取 No.6 三个工具，均成功；
这验证这次工具调用，不代表所有自然语言表达均能选对工具。生成后自动保存由离线集成测试覆盖。
问题问答新增 29 项测试，覆盖实际 L136 的 8 条记录、中英文关键词、any/all、特殊字符、
分页总数、来源隔离、原始列行号、空状态和不同状态原值、分组分页、历史不重复计数、错误脱敏，
以及混合工具调用后固定输出和重启恢复；范围报告新增验证见上文。
网页接口新增测试覆盖会话与聊天恢复、Excel 入库、筛选分页、范围报告、静态文件和错误脱敏，
当前完整离线测试为 121 项。网页聊天使用假模型验证，未重复调用真实模型或嵌入接口。
已用当前真实模型抽查中文提问“L136 有哪些问题”“哪些问题涉及螺丝安装”及“L136 的问题状态分布”，
分别正确调用查询/统计工具；L136 查询和统计均返回 8 条，关键词展开为螺丝、螺钉、screw 后命中 6 条。
关键词结果不等于已确认的语义分类，这些样本也不代表所有自然语言表达均会正确选取条件。

上一版使用当时配置的真实服务抽查了 No=1、7、16：No=1 保留疑似原因和措施执行未知；
No=7 未补造缺失根因及验证结果；No=16 保留待检测状态并提示它与 `done` 的冲突。
No=1 首次因一处原文引用不匹配被拒绝，收紧短原文引用提示后人工复核通过；
没有放宽引用校验或增加自动重试。这些是少量样本检查，不代表所有记录或每次生成都正确。

新版固定卡片已用 No=1、13 做真实抽查：No=1 的两项措施保留为执行情况未说明；
No=13 的两项检查观察与一项状态恢复单独保留，未输出措施有效性验证，
检查观察仅关联原文同句明确对应的检查措施，DP 恢复未擅自关联到 AHU 调整。
No=13 首次调用服务超时，人工再次复核后通过；程序仍不自动重试。
该抽查验证本次样本结果，不构成所有记录的语义正确性保证。

精简版已用 No=6 做真实复核：显示年份未说明、负责人 ME、跟踪中状态，
软件 Bug 保留为确认程度未说明，措施译为“切换回旧版软件”且不推断执行完成，
未补造有效性结论。初始现象与额外追踪信息各保留一次，完整原文和引用仍在 artifact 中。

配置真实服务后，建议手动验证：在 A 会话介绍姓名并追问，在 B 会话检查上下文隔离，
切回 A 后继续追问，退出重启后再查看历史和继续聊天。

## 官方参考

- [LangChain create_agent](https://reference.langchain.com/python/langchain/agents/factory/create_agent)
- [短期记忆与 thread_id](https://docs.langchain.com/oss/python/langchain/short-term-memory)
- [SQLite checkpointer](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite)
- [ChatOpenAI 集成](https://docs.langchain.com/oss/python/integrations/chat/openai)
- [openpyxl 读取与逐行遍历](https://openpyxl.readthedocs.io/en/stable/tutorial.html)
- [LangChain 模型结构化输出](https://docs.langchain.com/oss/python/langchain/models#structured-output)
- [LangChain 工具直接返回和 artifact](https://github.com/langchain-ai/langchain/blob/master/openwiki/tools.md)
- [Pydantic 校验器](https://docs.pydantic.dev/latest/concepts/validators/)
- [Jinja2 模板加载和 StrictUndefined](https://jinja.palletsprojects.com/en/stable/api/)
- [SQLModel 官方入门](https://sqlmodel.tiangolo.com/)
- [SQLModel Session 与事务](https://sqlmodel.tiangolo.com/tutorial/insert/)
- [SQLAlchemy 查询运算符](https://docs.sqlalchemy.org/en/21/core/operators.html)
- [SQLAlchemy JSON 字段](https://docs.sqlalchemy.org/en/21/core/type_basics.html#sqlalchemy.types.JSON)
