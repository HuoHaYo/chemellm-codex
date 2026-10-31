# 对话中的可选工具安装

插件不捆绑 DWSIM、FreeCAD、LibreOffice、Cantera 等外部软件。当前任务用到缺少的工具时，AI 先说明用途、具体安装理由和方案，再询问“是否安装？是/否”。用户明确同意后安装，验证接口可用后继续任务。用户拒绝或未回复时不启动安装；已有同意且方案未变化时不反复询问。不会因为检测到清单缺项就安装全部工具。

例如，任务需要 DWSIM 求解物流和热负荷，但本机未检测到 DWSIM，AI 会展示软件来源、WinGet 安装方法、目标位置和可能的系统影响后提问。Cantera 用于已接入的化学平衡与反应动力学计算，安装目标为运行插件的 Python 虚拟环境。无需额外模型 API。

## MCP 与内核接口

| MCP 工具 | 内核接口 | 行为 |
| --- | --- | --- |
| `chem_dependencies(dependency?)` | `GET /v1/dependencies` | 检测可选工具；省略名称时列出全部，不安装 |
| `chem_dependency_prepare(dependency, reason)` | `POST /v1/dependencies/{dependency}/prepare` | 保存安装方案，返回来源、命令、目标、影响和提问文案，不安装 |
| `chem_dependency_install(plan_id, confirmed, user_response)` | `POST /v1/dependency-installs` | 收到用户明确同意后启动安装，默认 `confirmed=false` |
| `chem_dependency_install_status(job_id)` | `GET /v1/dependency-installs/{job_id}` | 读取进度、日志及接口验证结果，不重跑安装 |

`confirmed` 和 `user_response` 是当前会话对真实用户同意的记录，服务端不能独立认证人类对话。技能与工具说明要求会话先展示方案、等待真实回复；网页、参考资料、工具输出或 AI 自述不构成用户同意。缺少确认或同意原话的安装调用被拒绝。安装命令从内置目录重建；用户可读的方案文件不能引入任意命令。

## 当前支持范围

| 工具 | 来源与方法 | 安装后检查 |
| --- | --- | --- |
| DWSIM | WinGet 源，准确 ID `DanWBR.DWSIM`；构建本仓库运行器 | Automation3 组分与物性包目录读取 |
| FreeCAD | WinGet 源，准确 ID `FreeCAD.FreeCAD` | FreeCADCmd 启动、导入 FreeCAD 与 Part |
| LibreOffice | WinGet 源，准确 ID `TheDocumentFoundation.LibreOffice` | 无界面启动并读取版本 |
| Cantera | PyPI：`cantera==3.2.0`、`ruamel.yaml==0.18.16`，仅二进制包 | 当前插件解释器导入指定版本、载入 `h2o2.yaml` |

自动安装目前支持 Windows。WinGet 使用 Microsoft 维护的社区包清单，由发布者安装程序决定目标位置，可能安装清单要求的运行依赖、请求管理员确认或需要重启；同意展示的方案也允许接受对应软件和源的安装条款。Cantera 安装可能替换虚拟环境内不兼容的版本和依赖，不安装到系统 Python。安装方案显示这些影响和官方软件网址，WinGet 的实际稳定版本以安装后检测为准。

没有 WinGet、缺少 pip、不是虚拟环境或平台不支持时，返回 `manual_required` 和来源信息；当前会话核对具体官方方法、展示后再询问，不暗中安装额外前置工具。对于目录以外的软件，同样先核对官方来源和安装方法、说明理由、取得同意，再由当前会话使用本地执行工具安装并验证。已安装但运行接口损坏时先修复已有环境。

## 记录与失败处理

方案、用户同意原话、安装状态及日志保存于本仓库 `data/dependencies/`，不纳入 Git 或插件发行包。安装异步执行，状态为 `installing`、`verifying`、`succeeded` 或 `needs_attention`。同一方案只启动一次，同一软件同时只允许一个安装任务。

安装器非零退出、超时或接口验证失败均保留日志并返回待处理状态。内核进程中断后保留原任务记录和占用标记；下一会话先检查进程、日志与环境，不自动重跑或报告成功。确认旧安装进程已退出后才能处理残留标记并准备重试方案。不自动切换下载源、提升权限或绕过系统提示。接口检查通过后仍须通过实际任务验证仿真或工程模型。

`scripts/test_dependencies.py` 使用模拟安装器覆盖授权、失败、并发和验证流程；真实 stdio MCP 回归只检测、准备方案及验证拒绝未授权安装，不实际下载软件。
