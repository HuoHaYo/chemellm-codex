"""Connected reaction and condenser, with every component and a common H basis."""
import numpy as np
from pydantic import Field, model_validator
from thermo import Chemical
from app.schemas import Inputs
from app.reaction import ReactionInput, reaction_thermo, gas_state, MixtureReactionInput, mixture_reaction
from app.hydrogen import COMPONENTS, CondenserInput, condenser_flash

class FrontendInput(Inputs):
    ethylbenzene_kmol_h:float=Field(default=400,gt=0,le=1e6)
    steam_to_ethylbenzene:float=Field(default=8,ge=.1,le=40)
    inlet_temperature_K:float=Field(default=823.15,ge=500,le=1200)
    reactor_temperature_K:float=Field(default=873.15,ge=500,le=1200)
    pressure_Pa:float=Field(default=100000,ge=50000,le=200000)
    conversion:float=Field(default=.6,ge=.01,le=.99)
    condenser_temperature_K:float=Field(default=310,ge=303,le=313.15)
    organic_henry_multiplier:float=Field(default=1,ge=.25,le=4)

    @model_validator(mode="after")
    def boundaries(self):
        h2_fraction=self.conversion/(1+self.steam_to_ethylbenzene+self.conversion)
        if h2_fraction<.005:
            raise ValueError("反应出口氢气摩尔分数小于当前冷凝模型下限0.005；请使用经验证的其他模型")
        return self

def frontend_process(p,mixture_feed=None):
    if mixture_feed is None:
        reactor=reaction_thermo(ReactionInput(**{key:getattr(p,key) for key in ReactionInput.model_fields}))
        feed=np.array([p.ethylbenzene_kmol_h*p.steam_to_ethylbenzene,p.ethylbenzene_kmol_h,0.,0.])
    else:
        reactor=mixture_reaction(MixtureReactionInput(feed_component_kmol_h=list(mixture_feed),
            **{k:getattr(p,k) for k in ("inlet_temperature_K","reactor_temperature_K","pressure_Pa","conversion")}))
        feed=np.array(mixture_feed)
    if not reactor["validation"]["passed"]:
        raise ValueError("反应器未通过平衡可行性或物热检查，不能继续连接冷凝器")
    extent=float(feed[1]*p.conversion)
    out=np.array([reactor["outlet_kmol_h"][name] for name in COMPONENTS])
    condensed=condenser_flash(CondenserInput(feed_kmol_h=float(out.sum()),mole_fractions=(out/out.sum()).tolist(),
        temperature_K=p.condenser_temperature_K,pressure_Pa=p.pressure_Pa,organic_henry_multiplier=p.organic_henry_multiplier))
    def hflow(flows,t):return float(sum(n*gas_state(name,t)[0] for name,n in zip(COMPONENTS,flows))/3600)
    hin=hflow(feed,p.inlet_temperature_K)
    hreactor=hflow(out,p.reactor_temperature_K)
    hcondenser=condensed["outlet_enthalpy_flow_kW"]
    qr=reactor["heat_duty_kW"]
    qc=hreactor-hcondenser
    recovered=sum((np.array(phase["component_flows_kmol_h"]) for phase in condensed["phases"]),start=np.zeros(4))
    atoms=np.array([[0,8,8,0],[2,10,8,2],[1,0,0,0]])
    atom_error=atoms@(recovered-feed)
    stoich=np.array([0,-1,1,1])*extent
    component_error=recovered-feed-stoich
    molecular_weights=np.array([Chemical(name).MW for name in COMPONENTS])
    mass_in=float(feed@molecular_weights);mass_out=float(recovered@molecular_weights)
    energy=hin+qr-qc-hcondenser
    passed=bool(condensed["validation"]["passed"] and max(abs(atom_error))<1e-6 and max(abs(component_error))<1e-6
        and abs(mass_out-mass_in)/mass_in<1e-8 and abs(energy)/max(1,abs(hin),abs(hcondenser),qc)<1e-8 and qc>0)
    def stream(tag,name,t,flows,h,phase):
        return {"id":tag,"name":name,"temperature_K":t,"pressure_Pa":p.pressure_Pa,"phase":phase,
            "component_flows_kmol_h":dict(zip(COMPONENTS,map(float,flows))),"total_kmol_h":float(sum(flows)),
            "mass_kg_h":float(np.array(flows)@molecular_weights),"enthalpy_flow_kW":h}
    streams=[stream("S01","气相乙苯与水蒸汽进料",p.inlet_temperature_K,feed,hin,"vapor"),
        stream("S02","反应器出口",p.reactor_temperature_K,out,hreactor,"vapor")]
    if mixture_feed is not None:streams[0]["name"]="含回流杂质的气相反应进料"
    for i,phase in enumerate(condensed["phases"],3):
        flows=phase["component_flows_kmol_h"]
        streams.append(stream("S"+str(i).zfill(2),{"vapor":"含氢尾气","aqueous_liquid":"含溶解有机物的水相","organic_liquid":"含水和氢气的有机相"}[phase["phase"]],
            p.condenser_temperature_K,flows,phase["flow_kmol_h"]*phase["molar_enthalpy_J_mol"]/3600,phase["phase"]))
    return {"model":"connected ideal dehydrogenation reactor + low-pressure H2/aqueous/organic condenser",
        "components":COMPONENTS,"streams":streams,"reaction_extent_kmol_h":extent,
        "reactor_heat_input_kW":qr,"condenser_heat_removed_kW":qc,
        "enthalpy_reference":"component ideal-gas Hf(298.15 K) + gas Cp integral; liquid H from the condenser Gibbs-model temperature derivative",
        "reactor":reactor,"condenser":condensed,
        "validation":{"component_balance_including_reaction_kmol_h":dict(zip(COMPONENTS,map(float,component_error))),
            "atom_balance_kmol_atoms_h":dict(zip(["carbon","hydrogen","oxygen"],map(float,atom_error))),
            "mass_relative_residual":abs(mass_out-mass_in)/mass_in,"global_energy_residual_kW":energy,"passed":passed},
        "warnings":["仅联立反应器与冷凝分相，尚未联立精馏、循环与全厂公用工程；所有默认操作条件是研究假设。",
            "反应采用单反应指定转化率/理想平衡上限；非催化动力学。冷凝采用UNIFAC与稀溶Henry模型，有机溶剂H2常数为PR预测；均待独立基准验证。",
            "所有水、乙苯、苯乙烯、氢气均传递至出口；湿有机相不能直接作为干燥二元塔进料。尾气和水相含芳烃，不能当作已满足排放或回收要求。",
            "冷凝移热包括气相降温、冷凝和模型预测溶解热；未设计换热器、真空系统、放空处理和脱水/脱气。气液温度导数焓无Poynting或气相残余焓。"]}
