"""Verified against the supplied 2026 task PDF; page numbers include the cover."""
import json
from app.db import execute, rows, uid

REQUIREMENTS = [
    ("B01", "原料类型、规格及资源调研依据", 2, True),
    ("B02", "产品结构、技术规格和市场规划", 2, True),
    ("B03", "生产规模及资源、市场、政策依据", 2, True),
    ("F01", "可行性论证：建设意义与规模", 3, True),
    ("F02", "工艺路线比选与技术方案", 3, True),
    ("F03", "总厂或园区系统集成、公用工程", 3, True),
    ("F04", "厂址选择与区域资源调查", 3, True),
    ("F05", "本质安全、环保、废物资源化及非化石能源", 3, True),
    ("F06", "绿色低碳循环发展与技术经济分析", 3, True),
    ("P01", "安全生产保障措施", 3, True),
    ("P02", "先进单元过程、集成与节能技术", 3, True),
    ("P03", "流程仿真、优化结果及可运行模拟源文件", 3, True),
    ("P04", "PFD 工艺流程图", 3, True),
    ("P05", "P&ID 管道及仪表流程图", 3, True),
    ("P06", "物料及热量平衡计算书", 3, True),
    ("E01", "非标设备：反应器工艺设计计算书", 3, True),
    ("E02", "非标设备：塔器工艺设计计算书", 3, True),
    ("E03", "标准设备：换热器选型计算书", 3, True),
    ("E04", "其他重要设备工艺设计及选型", 3, True),
    ("E05", "设备一览表", 4, True),
    ("L01", "车间布置与主要工艺管道配管设计", 4, True),
    ("L02", "车间设备平面布置图", 4, True),
    ("L03", "车间设备立面布置图", 4, True),
    ("L04", "专业软件设备布置三维设计及源文件", 4, True),
    ("L05", "工厂总体布置说明与总平面布置图", 4, True),
    ("C01", "基于调研数据的经济分析与评价", 4, True),
    ("D01", "可行性研究报告（≤4万字，DOCX+PDF）", 5, True),
    ("D02", "初步设计说明书（≤15万字，DOCX+PDF）", 5, True),
    ("D03", "典型设备计算说明书（≤5万字，DOCX+PDF、模型源程序）", 5, True),
    ("D04", "设计图集（AutoCAD 2004兼容格式+PDF、gbcbig.shx）", 5, True),
    ("D05", "仿真与优化结果、模拟源程序和现场可运行验证", 5, True),
    ("S01", "HAZOP 文档及适用时的专业软件源文件", 5, False),
    ("S02", "能量集成与节能结果及软件源文件", 5, False),
    ("S03", "专业软件成本估算与经济评价源文件", 5, False),
    ("S04", "专业软件容器结构设计源文件", 5, False),
]

# Exact source fragments, normalized only for whitespace by the regression test.
# These are requirement provenance, not design answers or product assumptions.
SOURCE_EXCERPTS = {
    "B01": ["原料类型及原料规格由参赛队根据资源调研结果自行确定。"],
    "B02": ["产品结构及其技术规格由参赛队根据本队的市场规划自行拟订。"],
    "B03": ["生产规模由参赛队根据本队的资源规划和市场规划以及国家的有关政策自行确定。"],
    "F01": ["1) 建设意义", "2) 建设规模"],
    "F02": ["工艺技术方案"],
    "F03": ["与总厂或园区的系统集成方案"],
    "F04": ["5) 厂址选择"],
    "F05": ["与社会及环境的和谐发展", "本质安全技术", "环保和废物资源化技术", "非化石资源与能源利用技术"],
    "F06": ["技术经济分析", "落实绿色低碳循环发展要求的情况"],
    "P01": ["安全生产的保障措施"],
    "P02": ["先进单元过程技术的应用", "集成与节能技术的应用"],
    "P03": ["工艺流程计算机仿真设计", "工艺流程的模拟及流程优化计算结果和模拟源程序"],
    "P04": ["绘制工艺流程图"],
    "P05": ["工艺管道及仪表流程图"],
    "P06": ["编制物料及热量平衡计算书"],
    "E01": ["典型非标设备—反应器和塔器的工艺设计", "反应器"],
    "E02": ["典型非标设备—反应器和塔器的工艺设计", "塔器"],
    "E03": ["典型标准设备—换热器的工艺选型设计"],
    "E04": ["其他重要设备的工艺设计及选型说明"],
    "E05": ["编制设备一览表"],
    "L01": ["车间布置设计", "车间主要工艺管道配管设计"],
    "L02": ["绘制车间设备平面布置图"],
    "L03": ["绘制车间设备立面布置图"],
    "L04": ["运用专业软件进行设备布置三维设计", "能在所采用的三维设计工具软件平台上打开的设计源文件"],
    "L05": ["对主要工艺车间、辅助车间、原料及产品储存区", "绘制总平面布置图"],
    "C01": ["根据调研获得的经济数据", "对设计方案进行经济分析与评价"],
    "D01": ["项目可行性研究报告", "4万字以内"],
    "D02": ["初步设计说明书", "15万字以内"],
    "D03": ["典型设备", "标准设备和非标设备", "5万字以内", "必须同时提供计算结果和计算模型的源程序"],
    "D04": ["设计图集", "PFD和P&ID图", "AutoCAD 2004版兼容格式", "国标汉字大字体gbcbig.shx"],
    "D05": ["流程优化计算结果和模拟源程序", "可打开运行的相应软件存档文件"],
    "S01": ["若进行危险性和可操作性（HAZOP）分析", "设计源文件"],
    "S02": ["若进行能量集成与节能技术运用", "设计源文件"],
    "S03": ["若采用专业软件进行过程成本的估算和经济分析评价", "设计源文件"],
    "S04": ["若采用专业软件进行容器类设备的结构设计", "设计源文件"],
}

GLOBAL_SOURCE_EXCERPTS = [
    "说明书均要求保存为DOCX和PDF格式",
    "格式参考《化学反应工程与工艺》期刊的要求",
    "图纸保存为AutoCAD2004版兼容格式和PDF格式",
    "图纸的文字式样使用国标汉字大字体gbcbig.shx",
    "计算机模拟和计算结果需提供可打开运行的相应软件存档文件",
    "保证能在本队的便携计算机上正常运行",
]
PRICES = [
    {"name": name, "value": value, "unit": unit, "source": "2026竞赛任务书 PDF 第4页", "scope": scope}
    for name, value, unit, scope in [
        ("304不锈钢设备",48000,"CNY/t","设备重量基准；不能直接替代安装总投资"),
        ("中低压碳钢设备",18000,"CNY/t","压力≤4MPa"),
        ("高压碳钢设备",22000,"CNY/t","其他特殊不锈钢另行调研"),
        ("低压蒸汽",255,"CNY/t","1.8MPa"),
        ("中压蒸汽",265,"CNY/t","3.8MPa"),
        ("高压蒸汽",285,"CNY/t","9.8MPa"),
        ("电",0.70,"CNY/kWh","任务书参考价"),
        ("工艺软水",10,"CNY/t","任务书参考价"),
        ("冷却水",0.5,"CNY/t","任务书参考价"),
        ("污水处理",5,"CNY/t","COD<500；原文未给COD单位"),
        ("人工",12000,"CNY/person/month","包括五险一金"),
    ]
]

def seed_requirements(project_id):
    for code, title, page, mandatory in REQUIREMENTS:
        execute("INSERT OR IGNORE INTO requirements(id,project_id,code,title,page,mandatory) VALUES(?,?,?,?,?,?)", (uid(),project_id,code,title,page,int(mandatory)))

def acceptance(project_id):
    items = rows("SELECT * FROM requirements WHERE project_id=? ORDER BY code", (project_id,))
    for item in items:
        item["evidence"] = json.loads(item["evidence"])
    mandatory = [item for item in items if item["mandatory"]]
    return {"requirements": items, "total": len(items), "mandatory": len(mandatory),
            "verified": sum(i["status"] == "verified" for i in mandatory),
            "ready": bool(mandatory) and all(i["status"] == "verified" for i in mandatory),
            "note": "verified 为人工审核记录，不代表自动获得竞赛资格或工程设计批准。"}
