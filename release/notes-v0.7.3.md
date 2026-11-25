# ChemELLM Codex v0.7.3

此版本明确了本地 ChemELLM Codex 插件的名称和调用方式，帮助 Codex 在会话中区分同名应用或服务。

- MCP 初始化说明和五个技能明确指向本地 `chemellm` 服务及其 `chem_*` 工具。连接失败时，提示检查本地插件状态。
- 安装器、使用说明和官网示例更新：在新会话中先调用本地插件的 `chem_health`，再开始计算或设计任务。

Windows x64 用户可下载 `ChemELLM-Codex-0.7.3-windows-x64.zip`，用 `SHA256SUMS.txt` 校验，完整解压后运行 `INSTALL.cmd`。安装完成后请开启新的 Codex 会话。
