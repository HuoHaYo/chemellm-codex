"""Once-through reactor/condenser/wet-column connection, without a recycle."""
import numpy as np
from pydantic import Field, model_validator
from thermo import ChemicalConstantsPackage
from app.frontend_process import FrontendInput, frontend_process
from app.hydrogen import COMPONENTS
from app.wet_column import WetColumnInput, wet_column


class SeparationTrainInput(FrontendInput):
    column_stages:int=Field(default=12,ge=4,le=60)
    column_feed_stage:int=Field(default=6,ge=1,le=59)
    column_pressure_Pa:float=Field(default=20000,ge=10000,le=80000)
    column_reflux_ratio:float=Field(default=3,ge=.1,le=30)
    column_organic_distillate_fraction:float=Field(default=.4,gt=.01,lt=.95)

    @model_validator(mode="after")
    def column_basis(self):
        if self.column_feed_stage>=self.column_stages:raise ValueError("进料级必须在末级部分再沸器上方")
        if self.column_pressure_Pa>=self.pressure_Pa:raise ValueError("此连接仅支持上游冷凝器至塔进料的降压，不含增压泵")
        return self


def wet_separation_train(p,mixture_feed=None,initial_column=None):
    front=frontend_process(FrontendInput(**{k:getattr(p,k) for k in FrontendInput.model_fields}),mixture_feed=mixture_feed)
    organic=next((s for s in front["streams"] if s["phase"]=="organic_liquid"),None)
    if organic is None:raise ValueError("前端未得到有机液相，不能按此流程接入湿有机相塔")
    feed=organic["total_kmol_h"]
    column=wet_column(WetColumnInput(feed_kmol_h=feed,
        mole_fractions=[organic["component_flows_kmol_h"][name]/feed for name in COMPONENTS],
        feed_temperature_K=organic["temperature_K"],feed_molar_enthalpy_J_mol=organic["enthalpy_flow_kW"]*3600/feed,
        equilibrium_stages=p.column_stages,feed_stage=p.column_feed_stage,pressure_Pa=p.column_pressure_Pa,
        condenser_temperature_K=p.condenser_temperature_K,organic_distillate_fraction=p.column_organic_distillate_fraction,
        reflux_ratio=p.column_reflux_ratio,organic_henry_multiplier=p.organic_henry_multiplier),initial=initial_column)
    if not column["validation"]["passed"]:raise ValueError("湿有机相塔未通过校验，不能返回联立成功")
    streams=[dict(s) for s in front["streams"]]
    for s in streams:s["boundary"]="feed" if s["id"]=="S01" else "internal" if s["id"]=="S02" or s["phase"]=="organic_liquid" else "outlet"
    mw=np.array(ChemicalConstantsPackage.from_IDs(COMPONENTS)[0].MWs)
    names={"organic_distillate":"塔顶有机馏出（待回收处理）","aqueous_distillate":"塔顶水相（待处理）",
        "vent":"塔顶尾气（待真空及回收设计）","bottoms":"富苯乙烯塔底（未验收产品）"}
    for out in column["outlets"]:
        flows=out["component_flows_kmol_h"]
        streams.append({"id":"S"+str(len(streams)+1).zfill(2),"name":names[out["outlet"]],
            "temperature_K":out["temperature_K"],"pressure_Pa":out["pressure_Pa"],
            "phase":"vapor" if out["outlet"]=="vent" else "aqueous_liquid" if out["outlet"]=="aqueous_distillate" else "organic_liquid",
            "boundary":"outlet","component_flows_kmol_h":flows,"total_kmol_h":out["flow_kmol_h"],
            "mass_kg_h":float(np.array([flows[k] for k in COMPONENTS])@mw),"enthalpy_flow_kW":out["enthalpy_flow_kW"]})
    exits=[s for s in streams if s["boundary"]=="outlet"]
    entered=np.array([streams[0]["component_flows_kmol_h"][k] for k in COMPONENTS])
    recovered=sum((np.array([s["component_flows_kmol_h"][k] for k in COMPONENTS]) for s in exits),start=np.zeros(4))
    component=recovered-entered-np.array([0.,-1.,1.,1.])*front["reaction_extent_kmol_h"]
    atoms=np.array([[0,8,8,0],[2,10,8,2],[1,0,0,0]])@(recovered-entered)
    heat_in=front["reactor_heat_input_kW"]+column["reboiler_duty_kW"]
    heat_removed=front["condenser_heat_removed_kW"]+column["condenser_duty_removed_kW"]
    energy=streams[0]["enthalpy_flow_kW"]+heat_in-heat_removed-sum(s["enthalpy_flow_kW"] for s in exits)
    mass_error=abs(float((recovered-entered)@mw))/streams[0]["mass_kg_h"]
    passed=bool(front["validation"]["passed"] and column["validation"]["passed"] and max(abs(component))<1e-5
        and max(abs(atoms))<1e-4 and mass_error<1e-8 and abs(energy)<1e-3)
    return {"model":"once-through dehydrogenation + H2 condenser + wet four-component MESH column",
        "components":COMPONENTS,"streams":streams,"frontend":front,"column":column,
        "reaction_extent_kmol_h":front["reaction_extent_kmol_h"],"reactor_heat_input_kW":front["reactor_heat_input_kW"],
        "condenser_heat_removed_kW":front["condenser_heat_removed_kW"],"column_reboiler_duty_kW":column["reboiler_duty_kW"],
        "column_condenser_duty_removed_kW":column["condenser_duty_removed_kW"],"total_heat_input_kW":heat_in,"total_heat_removed_kW":heat_removed,
        "pressure_connection":{"model":"adiabatic isenthalpic pressure reduction, no shaft work, negligible liquid pressure enthalpy",
            "inlet_pressure_Pa":p.pressure_Pa,"outlet_pressure_Pa":p.column_pressure_Pa,"feed_enthalpy_flow_kW":organic["enthalpy_flow_kW"],
            "note":"上游温度仅为进料描述；不声称降压后仍为该温度的单液相，进料级由实际焓接纳闪蒸效应。"},
        "validation":{"component_balance_including_reaction_kmol_h":dict(zip(COMPONENTS,map(float,component))),
            "atom_balance_kmol_atoms_h":dict(zip(["carbon","hydrogen","oxygen"],map(float,atoms))),
            "mass_relative_residual":mass_error,"global_energy_residual_kW":energy,"passed":passed},
        "warnings":["联立范围为一次通过的反应、冷凝和湿塔，无乙苯循环、全厂优化或公用工程设计。所有默认输入是研究假设。",
            "水及H2完整传递。两个冷凝模型的水中H2分别用Sander低温参数和IAPWS宽温关联式；显式传递上游实际焓，不重置进料焓。",
            "共同生成焓参考和守恒检查不等同物性模型一致或实验验证；有机H2仍是PR预测。见docs/WET_COLUMN_MODEL.md。",
            "全部出口均为待进一步处理物流，未满足产品纯度、排放、回收率或赛题工程验收。"]}
