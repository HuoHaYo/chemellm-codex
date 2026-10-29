"""Versioned equipment parameters. Source references stay within the owning project."""
import json
import math
from typing import Literal
from pydantic import Field, model_validator
from app.schemas import Inputs
from app.db import uid, now, one, rows, connection
import pint

units=pint.UnitRegistry(autoconvert_offset_to_baseunit=True)

class Parameter(Inputs):
    name:str=Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,59}$")
    label:str=Field(min_length=1,max_length=100)
    value:float
    unit:str=Field(min_length=1,max_length=60)
    provenance:Literal["assumption","calculated","datasheet","literature"]="assumption"
    artifact_id:str|None=Field(default=None,max_length=40)
    source_page:int|None=Field(default=None,ge=1,le=10000)
    calculation_id:str|None=Field(default=None,max_length=40)
    note:str=Field(default="",max_length=1500)

    @model_validator(mode="after")
    def source_rules(self):
        if self.provenance in {"datasheet","literature"} and not self.artifact_id:
            raise ValueError("手册/文献参数必须关联本项目原始文件")
        if self.provenance=="calculated" and not self.calculation_id:
            raise ValueError("计算参数必须关联本项目计算记录")
        if self.source_page and not self.artifact_id:
            raise ValueError("指定页码必须同时关联原始文件")
        return self

class EquipmentInput(Inputs):
    tag:str=Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,29}$")
    name:str=Field(min_length=1,max_length=100)
    equipment_type:Literal["reactor","column","exchanger","pump","tank","pipe","other"]
    material:str=Field(default="",max_length=100)
    description:str=Field(default="",max_length=2500)
    parameters:list[Parameter]=Field(default_factory=list,max_length=80)
    @model_validator(mode="after")
    def unique_parameters(self):
        if len({p.name for p in self.parameters})!=len(self.parameters):
            raise ValueError("参数名称必须唯一")
        return self

class EquipmentWriteInput(Inputs):
    record:EquipmentInput
    expected_revision:int|None=Field(default=None,ge=1)

class EquipmentReview(Inputs):
    expected_revision:int=Field(ge=1)
    status:Literal["draft","reviewed"]
    notes:str=Field(min_length=10,max_length=2500)

class EquipmentQuery(Inputs):
    query:str=Field(default="",max_length=200)

def serialized(row):
    return {**{k:v for k,v in row.items() if k!="data"},"data":json.loads(row["data"])}

def equipment_list(project_id,query=""):
    result=[serialized(row) for row in rows("SELECT * FROM equipment_records WHERE project_id=? ORDER BY tag",(project_id,))]
    if query:
        query=query.casefold()
        result=[r for r in result if query in r["tag"].casefold() or query in r["data"]["name"].casefold()]
    return result

def validated_data(project_id,p,db):
    result=p.model_dump()
    for item in result["parameters"]:
        try:
            q=units.Quantity(item["value"],item["unit"])
            base=q.to_base_units()
        except (pint.PintError,ValueError,TypeError) as exc:
            raise ValueError("参数 "+item["name"]+" 的单位无法解析；请使用明确单位（如 kg/h、m、Pa、degC、dimensionless）") from exc
        if not math.isfinite(base.magnitude):
            raise ValueError("参数换算后不是有限数值")
        item["base_value"]=float(base.magnitude)
        item["base_unit"]=str(base.units)
        item["dimensionality"]=str(q.dimensionality)
        if item["artifact_id"]:
            artifact=db.execute("SELECT id,metadata FROM artifacts WHERE id=? AND project_id=?",(item["artifact_id"],project_id)).fetchone()
            if not artifact:
                raise ValueError("参数来源文件不属于当前项目或不存在")
            metadata=json.loads(artifact["metadata"])
            pages=metadata.get("pages",[])
            if item["source_page"] and (not pages or item["source_page"]>len(pages)):
                raise ValueError("参数来源页码超出可验证范围；请使用已有逐页解析的 PDF/PPTX")
            item["source_sha256"]=metadata.get("sha256")
        if item["calculation_id"] and not db.execute("SELECT id FROM calculations WHERE id=? AND project_id=?",(item["calculation_id"],project_id)).fetchone():
            raise ValueError("参数来源计算不属于当前项目或不存在")
    return result

def save_equipment(project_id,p):
    timestamp=now()
    with connection() as db:
        # Serialize updates and revision checks in the same write transaction.
        db.execute("BEGIN IMMEDIATE")
        old=db.execute("SELECT * FROM equipment_records WHERE project_id=? AND tag=?",(project_id,p.record.tag)).fetchone()
        if old and p.expected_revision!=old["revision"]:
            raise ValueError("设备记录已存在或版本已变化；请先读取当前版本后再保存")
        if not old and p.expected_revision is not None:
            raise ValueError("设备记录不存在，不能指定旧版本")
        ident=old["id"] if old else uid()
        revision=old["revision"]+1 if old else 1
        data=validated_data(project_id,p.record,db)
        encoded=json.dumps(data,ensure_ascii=False,allow_nan=False)
        if old:
            db.execute("UPDATE equipment_records SET revision=?,data=?,status='draft',review_notes='',updated_at=? WHERE id=?",(revision,encoded,timestamp,ident))
        else:
            db.execute("INSERT INTO equipment_records VALUES(?,?,?,?,?,?,?,?,?)",(ident,project_id,p.record.tag,revision,encoded,"draft","",timestamp,timestamp))
        db.execute("INSERT INTO equipment_revisions VALUES(?,?,?,?)",(ident,revision,encoded,timestamp))
        row=db.execute("SELECT * FROM equipment_records WHERE id=?",(ident,)).fetchone()
    return {"record":serialized(dict(row)),"warnings":["参数单位已检查；来源关联并不验证参数抄录、适用条件或工程正确性，仍需人工审核。"]}

def review_equipment(project_id,ident,p):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM equipment_records WHERE id=? AND project_id=?",(ident,project_id)).fetchone()
        if not row:
            raise ValueError("设备记录不存在")
        if row["revision"]!=p.expected_revision:
            raise ValueError("设备记录版本已变化，请重新读取")
        data=json.loads(row["data"])
        if p.status=="reviewed":
            if not data["parameters"] or any(i["provenance"]=="assumption" for i in data["parameters"]):
                raise ValueError("审核前需要参数数据与依据；含假设参数的记录只能保留为草稿")
        db.execute("UPDATE equipment_records SET status=?,review_notes=?,updated_at=? WHERE id=?",(p.status,p.notes,now(),ident))
        db.execute("INSERT INTO equipment_reviews(record_id,revision,status,notes,created_at) VALUES(?,?,?,?,?)",(ident,p.expected_revision,p.status,p.notes,now()))
    return serialized(one("SELECT * FROM equipment_records WHERE id=?",(ident,)))

def equipment_history(project_id,ident):
    if not one("SELECT id FROM equipment_records WHERE id=? AND project_id=?",(ident,project_id)):
        raise ValueError("设备记录不存在")
    result=rows("SELECT * FROM equipment_revisions WHERE record_id=? ORDER BY revision DESC",(ident,))
    for item in result:
        item["data"]=json.loads(item["data"])
        item["reviews"]=rows("SELECT status,notes,created_at FROM equipment_reviews WHERE record_id=? AND revision=? ORDER BY id",(ident,item["revision"]))
    return result
