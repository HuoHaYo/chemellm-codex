# ChemELLM Codex v0.7.0

此版本加入科研绘图与仪表接线，并改进厂区管道模型和工艺图纸。

- **科研图表**：从 CSV、Excel、DWSIM 工况扫描或实验拟合记录绘制图表，导出 SVG、PDF 和 PNG。成果包可附带入图数据与 Vega-Lite 规格，便于复核和离线重绘。工况曲线在求解或校核失败处断开。
- **仪表与电气接线**：根据给定端子和导线生成可编辑 QElectroTech 工程、连接表与 PDF/SVG 图纸，并检查端子引用和接线冲突。自动检查用于核对给定连接，电气选型与功能安全仍需专业审核。
- **管道与 CAD**：Blender 支持沿中心线生成连续圆弧弯头和封闭管道外形，可给定弯曲半径。DXF 保留虚线、点划线和线宽，便于区分物料线与仪表信号线。
- **技能与成果**：按 DWSIM、三维、资料和经济等任务调用专项技能。可直接交付单份文件，也可按需打包选定成果及所需资源。

Windows x64 用户可下载 `ChemELLM-Codex-0.7.0-windows-x64.zip`，完整解压后运行 `INSTALL.cmd`。升级前备份原安装目录中的 `data` 和 `.chem`，安装完成后开启新的 Codex 会话。DWSIM、Blender 和 QElectroTech 等专业软件按任务需要另行准备。

详细用法见仓库中的 [科研图表](https://github.com/HuoHaYo/chemellm-codex/blob/v0.7.0/docs/charts.md)、[仪表接线](https://github.com/HuoHaYo/chemellm-codex/blob/v0.7.0/docs/qelectrotech.md) 和 [管道与图纸改进](https://github.com/HuoHaYo/chemellm-codex/blob/v0.7.0/docs/visual-standardization.md)。
