import json
from typing import Literal
from pydantic import Field
from app.calculations import Inputs, REGISTRY, calculate
from app.research import SearchInput, PubChemInput, literature_search, pubchem, project_search, open_literature_search
from app.diagrams import DiagramInput, draw
from app.documents import export_report
from app.catalog import TOOLS
from app.competition import acceptance
from app.db import execute, uid, now
from app.design_data import EquipmentWriteInput, EquipmentQuery, save_equipment, equipment_list
from app.drawing_io import DrawingReadInput, read_drawing
from app.datasets import DatasetInspectInput, DatasetAnalysisInput, inspect_dataset, analyze_dataset
from app.fulltext import ExtractInput, DocumentReadInput, FulltextImportInput, extract_document, read_document, import_open_fulltext
from app.evidence import ClaimInput, EvidenceInput, EvidenceQuery, save_claim, add_evidence, check_evidence, export_evidence
from app.engineering import (
    ObjectWrite, DeliverableWrite, EngineeringQuery, EngineeringPlan, CalculationRead, RequirementBatch,
    save_object, save_deliverable, save_plan, save_requirements, calculation_read, engineering_status,
    project_context, engineering_bundle,
)
from app.public_sources import SourceFetch, import_source

class UncertainToolError(RuntimeError):
    """A mutating operation failed after entry; its effects require reconciliation."""

def with_effects(function,*args):
    try:return function(*args)
    except ValueError:
        # Application/domain preconditions use ValueError before committed writes.
        # Preserve ordinary input rejection and the model's explicit repair protocol.
        raise
    except Exception as exc:
        raise UncertainToolError('工具未返回确认结果，可能已有部分写入；请核对计算和文件。'+type(exc).__name__+': '+str(exc)[:1500]) from exc

class ReportInput(Inputs):
    title: str = Field(min_length=1, max_length=90)
    body: str = Field(min_length=1, max_length=80000)
    formats: list[Literal["docx", "pdf", "pptx", "md"]] = Field(default=["docx","pdf"],min_length=1,max_length=4)

class EmptyInput(Inputs):
    pass

class AskInput(Inputs):
    question: str = Field(min_length=1,max_length=2500)

SCHEMAS={name:entry[0] for name,entry in REGISTRY.items()}
SCHEMAS.update(literature_search=SearchInput,pubchem=PubChemInput,project_search=SearchInput,pfd=DiagramInput,export_report=ReportInput,acceptance=EmptyInput,ask_user=AskInput)
SCHEMAS.update(equipment_save=EquipmentWriteInput,equipment_query=EquipmentQuery)
SCHEMAS["drawing_read"]=DrawingReadInput
SCHEMAS.update(dataset_inspect=DatasetInspectInput,analyze_dataset=DatasetAnalysisInput)
SCHEMAS.update(document_extract=ExtractInput,document_read=DocumentReadInput,fulltext_import=FulltextImportInput,
               claim_save=ClaimInput,evidence_add=EvidenceInput,evidence_check=EvidenceQuery,evidence_export=EmptyInput)
SCHEMAS["open_literature_search"]=SearchInput
SCHEMAS.update(project_context=EmptyInput,engineering_object=ObjectWrite,engineering_deliverable=DeliverableWrite,
               engineering_status=EngineeringQuery,engineering_bundle=EmptyInput)
SCHEMAS.update(engineering_plan=EngineeringPlan, engineering_requirements=RequirementBatch,
               calculation_read=CalculationRead, source_fetch=SourceFetch)

def definitions(allowed=None):
    descriptions={t["id"]:t["description"] for t in TOOLS}
    descriptions.update(acceptance="读取项目赛题要求和证据状态。不能修改人工审核结论。",
                        ask_user="缺少设计依据或必须由用户决定的参数时提出追问并暂停。")
    descriptions.update(project_context="读取当前项目附件ID、要求清单、工程数据及成果缺口。任务开始时先调用。",
                        engineering_object="保存版本化通用工程对象：设计依据、物流、设备、公用工程、安全、经济等。数值必须有单位/条件/来源；计算值校验实际结果字段，依赖锁定上游版本。",
                        engineering_deliverable="规划或更新任务/成果及其要求编码、上游成果、工程对象、文件格式和实际章节定位。files空数组表示尚未生成；更新须提供当前版本。不能写入审核通过。",
                        engineering_plan="原子保存完整工程对象/成果计划及依赖关系；所有原题要求均须映射。先只建立计划，文件可为空；对象和成果的依赖在同批内按拓扑顺序保存。",
                        engineering_requirements="从已提取的任务原文快照或本轮冻结的用户原始请求建立或绑定要求。文档来源须提供snapshot_id/block_id并核对逐字quote；无文件的自由任务须source_kind=user_request并引用本轮用户原句，不能引用旧对话或模型总结。已有要求只能以同标题和必需属性绑定原文，不能覆盖或删除。",
                        calculation_read="按计算记录ID和JSON Pointer读取完整结果的指定字段或分页，避免从截断工具回复抄数。空result_path读取根节点。",
                        document_extract="优先从PDF/DOCX/XML/HTML原生文本提取并留存不可变快照。遇到扫描页或图片文字时，仅在image_mode=ai时调用当前配置模型的视觉转录；AI模式的PDF一次一页，使每次视觉模型请求计入本轮累计预算。AI结果标记为未核验且必须对照原图；接口不支持图像时保留失败，不静默降级或使用传统OCR。",
                        source_fetch="安全下载公开HTTPS原始HTML/PDF/UTF-8文本并保存哈希、获取时间、重定向和可定位快照；不支持登录、内网、付费绕过或把检索题录当原文。",
                        engineering_status="检查要求覆盖、工程数据版本、来源数值、成果原件/格式/占位及缺口。自动检查不能替代专业审核及原生运行验证。",
                        engineering_bundle="打包当前项目原件、工程数据及历版、要求索引、模型工具轨迹、冻结输入/平台及逐项缺口。未通过必须保留失败状态。")
    result=[]
    for name,schema in SCHEMAS.items():
        if allowed is not None and name not in allowed:
            continue
        parameters=schema.model_json_schema()
        parameters['properties']['_execution']={"type":"object","additionalProperties":False,
            "properties":{"task_key":{"type":"string","maxLength":100,"description":"当前子任务的稳定标识"},
                          "repairs":{"type":"array","maxItems":20,"items":{"type":"string"},"description":"本调用修复的失败ID；须与同一工具/子任务对应，成功后才关闭"}}}
        result.append({"type":"function","function":{"name":name,"description":descriptions.get(name,name),"parameters":parameters}})
    return result

def run_tool(project_id,name,args,*,enforce_engineering=False):
    if name not in SCHEMAS or name=="ask_user":
        raise ValueError("此工具不能直接执行")
    if enforce_engineering:
        from app.engineering import require_engineering_tool
        require_engineering_tool(project_id,name)
    p=SCHEMAS[name].model_validate(args)
    if name in REGISTRY:
        result=calculate(name,args)
        if name in {"cantera_equilibrium","cantera_reactor"}:
            from app.simulation_files import save_simulation
            result=with_effects(save_simulation,project_id,name,p,result)
    elif name=="literature_search":
        result=literature_search(p)
    elif name=="pubchem":
        result=pubchem(p)
    elif name=="open_literature_search":
        result=open_literature_search(p)
    elif name=="project_search":
        result=project_search(project_id,p)
    elif name=="pfd":
        result=with_effects(draw,project_id,p)
    elif name=="export_report":
        result=with_effects(export_report,project_id,p.title,p.body,p.formats)
    elif name=="equipment_save":
        result=with_effects(save_equipment,project_id,p)
    elif name=="equipment_query":
        result={"records":equipment_list(project_id,p.query)}
    elif name=="drawing_read":
        result=read_drawing(project_id,p)
    elif name=="dataset_inspect":
        result=inspect_dataset(project_id,p)
    elif name=="analyze_dataset":
        result=with_effects(analyze_dataset,project_id,p)
    elif name=="document_extract":
        result=with_effects(extract_document,project_id,p)
    elif name=="document_read":
        result=read_document(project_id,p)
    elif name=="fulltext_import":
        result=with_effects(import_open_fulltext,project_id,p)
    elif name=="source_fetch":
        result=with_effects(import_source,project_id,p)
    elif name=="claim_save":
        result=with_effects(save_claim,project_id,p)
    elif name=="evidence_add":
        result=with_effects(add_evidence,project_id,p)
    elif name=="evidence_check":
        result=check_evidence(project_id,p)
    elif name=="evidence_export":
        result=with_effects(export_evidence,project_id)
    elif name=="project_context":
        result=project_context(project_id)
    elif name=="engineering_object":
        result=with_effects(save_object,project_id,p)
    elif name=="engineering_deliverable":
        result=with_effects(save_deliverable,project_id,p)
    elif name=="engineering_plan":
        result=with_effects(save_plan,project_id,p)
    elif name=="engineering_requirements":
        result=with_effects(save_requirements,project_id,p)
    elif name=="calculation_read":
        result=calculation_read(project_id,p)
    elif name=="engineering_status":
        result=engineering_status(project_id,p)
    elif name=="engineering_bundle":
        result=with_effects(engineering_bundle,project_id)
    else:
        result=acceptance(project_id)
    encoded=json.dumps(result,ensure_ascii=False,allow_nan=False)
    ident=uid()
    with_effects(execute,"INSERT INTO calculations VALUES(?,?,?,?,?,?)",(ident,project_id,name,p.model_dump_json(),encoded,now()))
    return {"calculation_id":ident,**result}
