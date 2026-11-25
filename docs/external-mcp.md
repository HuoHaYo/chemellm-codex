# 外部软件与独立 MCP

ChemELLM 默认提供项目计算、成果归档和校核工具。安装包也可以按需接入五个独立 MCP：先完成 `INSTALL.cmd`，再运行 `EXTERNAL-MCP.cmd` 选择要启用的工具，最后重跑 `INSTALL.cmd` 并新开 Codex 会话。设置写入本机插件启动配置，缓存和扩展放在当前安装目录的 `runtime/external-mcp/`。安装包没有开发者路径、令牌、工程文件或可选软件二进制。

| MCP | 接入版本与本机前置条件 | 何时使用 |
| --- | --- | --- |
| `chemellm-blender` | Blender Lab MCP 1.0.3；需要 Blender 5.1+、Git 和安装器提供的 uv | 从生成的 `.blend` 继续编辑、做镜头与动画、检查或渲染。运行 `runtime/external-mcp/Open-Blender-MCP.cmd`，打开演示或工程副本，在扩展首选项中按需启动本机桥接。 |
| `chemellm-freecad` | MCP Bridge 0.1.30；需要 FreeCAD 1.1+ 和 Node.js 18+ | 运行 `runtime/external-mcp/Open-FreeCAD-MCP.cmd`，打开工程副本，在状态栏点击 MCP Off 启动。保留 Ask，逐次确认。配对凭据仅在当前 Windows 用户下可解密。 |
| `chemellm-vega` | Vega MCP 上游发布包 0.4.0；独立环境按上游锁文件安装 | 用内联数据生成交互图。客户端需显示 MCP Apps 才能看到交互界面；项目出图和可复现交付仍用 `export_chart`。 |
| `chemellm-qet` | 上游 QET MCP，固定到 0.200.1 预览版提交；基础读取不需要 QET 本体 | 读取工程、元素、导线、比较和检查。把副本放到 `runtime/external-mcp/exchange/qet/`。可按下文另选经验证的原生程序。 |
| `chemellm-dwsim` | ChemELLM Windows Automation3 桥接；需要本机支持的 DWSIM | 查看组分与物性目录、读取、预检、创建、修改工况并稳态求解。把可信模型副本放在 `runtime/external-mcp/exchange/dwsim/`，用文件名调用；新成果写入 `runs/`。落项目台账、批量扫描和工程发布仍使用原有 `chem_dwsim*`。 |

也可以从安装目录直接运行：

~~~powershell
.\EXTERNAL-MCP.cmd status
.\EXTERNAL-MCP.cmd vega
.\EXTERNAL-MCP.cmd freecad --executable "D:\Apps\FreeCAD\bin\freecad.exe"
.\EXTERNAL-MCP.cmd blender --executable "D:\Apps\Blender\blender.exe"
.\EXTERNAL-MCP.cmd qet --disable
~~~

已有原生 QElectroTech 新版时，可用 `.\EXTERNAL-MCP.cmd qet --qet-native --executable <程序路径>`。也可传 `--download-qet-native` 并行安装固定预览版，或用 `--qet-native-archive <官方便携 ZIP>` 提供本地包，两种方式都校验发布哈希。原生程序须先通过命令能力检测；原生模式开放导出、连续性检查、创建和编辑等工具，脚本仅由这些固定操作生成。任意脚本和实时脚本调用不开放。启用后先用工程副本验证接线数据。

DWSIM 上游 10.3.0 的独立 MCP 主要以 Linux 包分发。本包的 Windows 稳态桥接不提供上游动态仿真。如已在本机另外部署官方服务，可使用 `.\EXTERNAL-MCP.cmd dwsim --dwsim-server <本机原生程序或 stdio 可执行启动器>`；该程序须能无参数从 stdio 启动。这个选项不会切换 ChemELLM 的原有工程引擎。

可选源版本与哈希见 `release/external-mcp.json`。可为 Vega、FreeCAD 和 QET MCP 脚本提供 `--archive <已下载文件>`，仍执行相同哈希校验。更换机器时重新运行配置，不复制旧机器生成的启动文件。卸载某个 MCP 用 `--disable` 后重跑安装器；已保存工程和配套软件保留。
