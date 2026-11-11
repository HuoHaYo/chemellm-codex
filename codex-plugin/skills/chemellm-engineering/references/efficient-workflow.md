# 新流程：探索、采用与交付

`chem_project_init(request)` 支持 `research / scene / simulation / design`。一次提交项目名、要求和交付项后即可试算；可以先只提交名称和任务类型，后续补计划。`chem_create_project` 新建也默认新流程，旧项目的默认完整响应和阶段门禁不变。

```json
{"name":"分离流程","task_kind":"simulation",
 "requirements":[{"code":"R1","title":"稳态结果与报告"}],
 "deliverables":[{"key":"result","title":"采用结果","requirementCodes":["R1"],"formats":["md"]}]}
```

新项目摘要含 `projectId/revision/counts/ready/gaps`；详情通过 `chem_records(project_id, section, key?, offset=0, limit=20)` 读取，section 支持 objects、source_groups、calculations、files、references、history，也可定向读 deliverables、requirements、review 和冻结请求 runs。历史保留版本和删除标记。计算完整结果通过 `chem_calculations(project_id, calculation_id)`；完整兼容导出通过 `chem_project(view="full")` 或项目资源获取。

探索调用 `chem_execute(project_id, tool, args, deliverable_keys=[...])`。使用现有依据时传 `basis_keys=["basis/feed"]` 固定版本；DWSIM 可在其 request 中关联。程序保存请求、计算/模型版本、结果、诊断、校核和成果哈希；不要另外抄写相同数值或重复注册插件已生成的成果。单位只从受支持字段获取；未知单位/来源保留待确认，实际输入不自动成为经验证的设计依据。

正式采用时，用 `chem_project_init` 更新交付项，给当前项目 `expected_revision`，明确 `calculation_ids`、设计项的 `basis_keys`、必要时 `artifact_ids`。没有显式 artifact_ids 时，文件按交付项关联；有计算版本的文件只选采用计算对应版本。混合任务可在各交付项设置 `task_kind` 和必要 `checks`。默认 research 无工艺数值要求，scene 检查 model/connections/spatial/consistency，simulation 检查 numeric，design 检查 numeric/basis。定制检查必须满足真实用户要求，不能为绕过失败而清空。

只有采用计算参加数值审核；失败记录继续可查，不必删除。变更依据或来源组会让相关结果过期；不能只改采用 ID 或重新审核来消除过期状态。DWSIM 正式发布仍要求已关联必要依据且 `solved=true`、`validation.passed=true`。自动校核不替代专业审核。

外部软件成果批量登记：

```json
{"root":"C:/tasks/plant","generator":"Blender 4.x / actual export command","run_id":"scene-v1",
 "files":[{"path":"scene.blend","deliverable_keys":["scene"]},
          {"path":"web/index.html","deliverable_keys":["scene"]},
          {"path":"web/assets/model.glb","deliverable_keys":["scene"]},
          {"path":"checks.json","deliverable_keys":["scene"]}],
 "checks":[{"name":"model","passed":true,"report_path":"checks.json","deliverable_keys":["scene"]}]}
```

`chem_artifacts_register(project_id, request)` 复制为固定版本并保存哈希，保留相对目录；网页依赖的脚本、模型、贴图都须在同批 files 中提交。报告文件必须同批登记；其余必要检查也须真实提供。文件可以绑定 `calculation_id`；插件只验证报告文件完整性，不把外部 passed 声明标成独立验证。同内容、同生成来源、同 run_id、同映射重复提交幂等；改变内容形成新版本。入包路径为 `deliverables/<registration_id>/<相对路径>`，浏览器相对资源路径保持有效。打包实际内容逐流校验。

任务目录来自客户端 MCP roots；不支持 roots 的客户端，用启动参数 `--allowed-root C:/tasks/plant`，可重复配置。开发配置脚本也接受该参数。任务路径授权不改变本仓库数据目录；不要仅为导入文件移动项目数据。

旧项目不会自动升级。需要时显式调用 `chem_project_init(project_id=..., upgrade=true, expected_revision=0, ...)`；迁移前保留 `legacy-backup.json` 和旧快照，事务导入后核对对象、计算、文件和资料关联。失败恢复旧后端；已有迁移备份时先核对，不覆盖备份。新流程 SQLite 为主存储，`.chem` 是变化记录的增量快照，`$text` 引用指向同项目 `texts/<digest>.json`；完整 JSON 在导出时生成，不直接编辑数据库快照。

阶段完成时审核、交付需要时打包即可。技能/源码在本次发布同步更新，健康信息的 `skillPairing` 应为 matched；unconfigured 表示启动配置尚无指纹，mismatch 需在本次会话完成后重新生成配置并刷新插件，新会话生效。不要热更新正在建模的会话。
