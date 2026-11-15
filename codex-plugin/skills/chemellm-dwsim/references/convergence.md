# 收敛诊断与独立校核


`solved` 是原生引擎状态。`validation.checks` 每项有 status=passed/failed/not_checked、passed=true/false/null、原因和位号；守恒项有实际残差、单位与容差。总 passed 仅在适用必需检查全部完成且通过时为true，失败false，信息不足null。当前 schema_version=1；旧记录不追溯修改，没有该校核版本的旧结果需要重新运行后才能发布。

按单元和全流程检查质量、非反应组分质量、能量；内部物流不重复进入外边界，热/轴功从单元原生字段独立读取，不用焓残差反推。反应使用含生成焓的总焓及元素守恒。缺少必要数据/不支持单元显示未检查。零流量出口不要求未定义的组成归一化。容差随结果保存：relative=1e-5；mass_absolute_kg_s=1e-8；component_absolute_kg_s=1e-8；energy_absolute_kw=1e-4；element_absolute_mol_s=1e-7。判据 `abs(in-out)<=absolute+relative*max(abs(in),abs(out))`，可通过 validation_options 明确修改。


失败先用计算 ID 定向读 /solved、/validation、/errors、/unit_operations 对应字段，不先取全部历史结果。按位号检查连接、自由度、物性、初值与规格；只修改能够解释原因的输入。run.initialization 分步应用并求解，记录真实状态。求解控制参数与重要工艺假设分开；不暗改容差、删除装置或降为黑箱换取通过。修正后新结果明确替代旧失败，旧记录保留。
