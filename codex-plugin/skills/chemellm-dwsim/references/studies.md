# 多工况、扫描及取消续算


`chem_dwsim_study(project_id, request)` 对应 POST /v1/projects/{id}/dwsim/study：

```json
{"action":"start","request_key":"load-01","flowsheet_path":"<原生模型路径>",
 "cases":[{"key":"normal"},{"key":"high","updates":[{"tag":"F","mass_flow_kg_s":1.2}]},
          {"key":"low","updates":[{"tag":"F","mass_flow_kg_s":0.8}]}],
 "metrics":[{"key":"duty","path":"/unit_operations/@P-101/net_energy_input_kw","unit":"kW"}]}
```

cases 与 scan 二选一。扫描示例 `scan={tag:"F",field:"temperature_k",values:[300,310,320]}`，也支持pressure_pa、mass_flow_kg_s。reflux_ratio 扫描提供完整严格塔 object_template，冷凝器是Stream_Ratio，每次仅改该数值。每任务最多50工况，默认串行，单次超时120秒、范围5–900秒。

Metric.path为JSON Pointer，数组可用@位号；原生单位验证/换算，不能任意贴单位。denominator_path可计算同量纲比值（回收率等），unit为dimensionless或%。未知单位位置拒绝。

返回job_id后，用 action=status/cancel/resume＋job_id。取消在当前单次求解结束后停止；续算只做未完成工况。失败和failed_validation终态保留，不自动重跑；修正后新建任务。进程中断后关联已提交的计算，避免重复执行。相同request_key与相同请求复用任务，改变方案须新key。

基准副本及哈希、每工况独立原件/原生模型/计算/校核持久化；任务在data/projects/<id>/dwsim-studies/。启动或续算才运行，无后台监控。同项目写入互斥；状态和取消仍可调用。部分失败保留其他结果，只有校核通过且指标完整的点进入有效包络。输出CSV、JSON、每指标PNG曲线，表中保留失败点。
