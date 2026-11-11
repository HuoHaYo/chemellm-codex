# ChemELLM 0.6：自动采集与关键决策记录

新建项目默认 `workflow=efficient`。AI 负责依据选择、冲突解释、重要假设和诊断；程序保存真实请求、计算结果、原生版本、检查、文件哈希和版本关系。旧 JSON 项目保持原流程和默认完整响应，升级必须显式触发。

## 可用接口

| 入口 | 行为 |
| --- | --- |
| `chem_project_init(request)` | 一次建立/更新任务类型、要求和交付项；支持 research/scene/simulation/design |
| `chem_execute(..., basis_keys?, deliverable_keys?)` | 探索不要求事先填完计划/依据；采用现有依据时固定对象版本；DWSIM 也支持其 request.basis_keys |
| `chem_basis_select` | 来源组、批量简写、例外覆盖；保持原候选优先级、单位和假设传递 |
| `chem_artifacts_register` | 从允许的任务目录批量复制固定版本，保存目录、哈希、生成来源、交付和计算映射 |
| `chem_project` / `chem_records` | 新项目默认摘要；分页读对象、来源组、计算、文件、资料、历史和冻结请求 runs |
| `chem_reference_use(images=...)` | 原件版本、页码、区域、图像哈希；图形引用不冒充逐字引文 |
| `chem_review` / `chem_bundle` | 共同使用交付项、采用文件和计算版本映射；校核状态与专业审核分开 |

初始化、登记请求示例与升级参数见[技能参考](../codex-plugin/skills/chemellm-engineering/references/efficient-workflow.md)。来源组示例见[数值选取](../codex-plugin/skills/chemellm-engineering/references/numerical-selection.md)。

同来源参数通过来源组继承说明，每个量固定组版本；特殊条件和候选单独覆盖。组更新使依赖结果过期，历史仍可读。仅重要 process 假设要求敏感性任务；solver 和 geometry 保留假设属性。未知单位或来源明确待确认，不由参数命名猜测或升级为已验证依据。

试算失败保留记录。正式交付显式设置 deliverable 的 `calculation_ids`、设计项的 `basis_keys`，必要时用 `artifact_ids` 选定文件；只审核采用计算。新版成功替代旧版后，不因历史失败单独阻塞。资料任务不要求工艺记录；三维任务默认要求 model/connections/spatial/consistency 报告。外部报告 passed 只是来源方声明，登记仅固定其内容与定位，不宣称插件独立验证报告结论。

独立任务目录优先使用客户端 MCP roots；无 roots 时通过 `--allowed-root` 配置，可重复指定。`scripts/configure-codex.py --allowed-root C:/tasks/plant` 可生成对应启动参数，不改变本仓库 data 位置。外部成果不受旧材料导入的 25 MiB 限制；网页脚本、GLB、贴图需同批登记，入包保留相对目录。选中注册版本时同批资源与报告一同入包。打包校验实际写入字节，失败清理临时包。

## 存储、并发与迁移

新状态保存在现有 `data/kernel/chemellm.sqlite3` 的 `codex_project_*` 表：按项目、对象、来源组和记录版本写入；重复长文本采用内容哈希引用。计算和成果继续复用既有表及目录。普通摘要不读取或重算所有成果的哈希。SQL 查询与增量快照只更新变化记录，完整 JSON 在兼容读取或打包时生成。

`.chem/<id>/` 是只读增量快照；对象文件使用稳定哈希名，`$dict` 是字典封装，`$text` 指向 `texts/<digest>.json`。不要直接编辑这些快照作为主数据。价格、共享资料的既有用户编辑入口与真实价格保护继续保留。

同项目写入使用进程间项目锁，冲突返回 409；SQLite 事务覆盖输入冻结或结果提交，求解期间不占数据库写事务。`runs` 保存求解前请求和项目/依据版本；正常结束关联 calculationId，中断保留状态与错误。进程异常退出留下的 running 记录可查询核对，不静默自动重跑。MCP 桥接不再用一把锁包住所有请求，状态/取消能并发执行。协议传输持有原始 stdout，Python 日志包括后台线程统一进入 stderr。

旧项目默认读写 JSON。显式 `chem_project_init(upgrade=true)` 前复制 `legacy-backup.json` 和旧快照，导入后核对对象、计算、文件、资料及其版本关系；失败删除新状态并恢复旧后端。已有备份时拒绝覆盖，需先核对上次迁移。历史计算不自动变成当前采用计算。

## 实测与验证

固定测试使用 2,500 条参数、两个共同来源组。完整逐项请求 15,156,664 字节，简写 181,306 字节，减少 **98.80%**。对象含历史的展开 JSON 为 27,771,133 字节；SQL 记录连同共享文本及引用开销为 4,945,543 字节，逻辑存储减少 **82.19%**，不是 SQLite 物理文件压缩率。共同检索说明在 SQLite 和增量文本快照各保存一份。一次初始化即可取得可复算单位换算结果；项目摘要实测 731 字节，低于 32 KiB。测试同时断言单对象更新不改其他对象快照、摘要不哈希成果。

典型本机批量依据请求约 1.2 秒，受机器与并发负载影响；不据此承诺总建模时间倍数。`data/kernel/metrics/efficiency.jsonl` 分别保存 registration_ms、hash_ms、serialization_ms、storage_ms、db_lock_wait_ms、lock_wait_ms、tool_ms、native_runner_ms；后者包含原生程序启动、加载和求解，不等同纯求解器 CPU 时间。仅记录时间与计数，不写输入和秘密。

回归入口：`test_efficiency.py`、`test_kernel.py`、真实 stdio 的 `test_codex_mcp.py`，以及依据、资料、Blender、DWSIM 稳态/布局和工况状态测试。外部登记测试使用明确标注的合成文件/报告检验版本与打包；真实 Blender 和 DWSIM 另行执行，合成报告不能代表工程验证。

## 发布与会话

Python 包、插件和内核版本统一为 0.6.0。配置生成器固定技能 SHA256，健康信息给出版本、能力、指纹和 skillPairing；安装检查与发行清单验证配对。源文件变化后旧配置可显示 mismatch，需在当前建模会话结束后重新生成配置并刷新插件，在新会话生效。本次实现不安装到正在使用的插件缓存、不重启其他会话。

2026-11-09 尝试官方 Codex 技能/MCP 文档，网络返回 403；保留现有清单和技能 frontmatter 结构，仅同步版本，未声称完成新的在线格式确认。技能依据本地 skill-creator 的渐进加载规范拆分；价格、资料、Blender 与 DWSIM 仅按任务读取。原油表征和严格塔算法属于独立工作，本次不降低模型深度或放宽校核判据。
