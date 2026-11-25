# ChemELLM Codex v0.7.2

- Windows 安装包新增可选外部 MCP 配置入口：Blender Lab、FreeCAD MCP Bridge、Vega Charts、QElectroTech 和 DWSIM 稳态支持。第三方可执行软件不随包分发，原有项目工具仍可按原方式使用。
- 科研图表默认使用 Vega-Lite 6.4；可继续选择 5.21。离线重绘以成果清单记录的版本为准。
- DWSIM Windows MCP 新增组分和物性目录、预检、建模、工况修改及稳态求解。可连接另行部署的上游原生 stdio 服务；本包不提供动态引擎。
- QElectroTech 的基础模式支持两类图纸格式的读取与检查。新版原生程序须单独启用并先通过能力检测。

参见 [外部 MCP 接入说明](https://github.com/HuoHaYo/chemellm-codex/blob/v0.7.2/docs/external-mcp.md)。
