"""Source-backed ideal-gas EB dehydrogenation screening model, not catalyst kinetics."""
import math
from pydantic import Field, model_validator
import numpy as np
from scipy.optimize import brentq
from thermo import Chemical
from app.schemas import Inputs

T0=298.15
R=8.31446261815324
P0=100000.0
# Small factual extracts transcribed from the cited NIST SRD 69 tables, inspected 2026-10-18.
# Cp: TRC Selected Values of Properties of Chemical Compounds (1997), J/(mol K).
TEMPERATURES=[50,100,150,200,273.15,298.15,300,400,500,600,700,800,900,1000,1100,1200,1300,1400,1500]
DATA={
    "ethylbenzene":{"CAS":"100-41-4","Hf_J_mol":29800.0,"Hf_uncertainty_J_mol":840.0,"S0_J_mol_K":360.6,"S0_uncertainty_J_mol_K":0.5,
        "Cp":[44.37,57.72,72.35,88.54,116.88,127.40,128.19,169.95,206.58,236.75,261.51,282.08,299.37,314.04,326.56,337.27,346.48,354.41,361.27],
        "Hf_reference":"Prosen, Gilmont et al. (1945), J. Res. NBS 34, 65–70; Hf by Prosen, Johnson et al. (1946)",
        "S0_reference":"Miller (1978), J. Chem. Phys. 68, 1317–1319",
        "url":"https://webbook.nist.gov/cgi/cbook.cgi?ID=C100414&Units=SI&Mask=1"},
    "styrene":{"CAS":"100-42-5","Hf_J_mol":146900.0,"Hf_uncertainty_J_mol":1000.0,"S0_J_mol_K":345.1,"S0_uncertainty_J_mol_K":2.1,
        "Cp":[45.76,54.19,65.81,81.77,110.03,120.19,120.94,159.79,192.59,219.0,240.4,258.0,272.8,285.2,295.8,304.9,312.7,319.4,325.2],
        "Hf_reference":"Prosen and Rossini (1945), J. Res. NBS 34, 59–63",
        "S0_reference":"Pitzer (1946), J. Am. Chem. Soc. 68, 2209–2212",
        "url":"https://webbook.nist.gov/cgi/cbook.cgi?ID=C100425&Units=SI&Mask=1"},
}

class ReactionInput(Inputs):
    ethylbenzene_kmol_h:float=Field(default=400,gt=0,le=1e7)
    steam_to_ethylbenzene:float=Field(default=8,ge=0,le=40)
    inlet_temperature_K:float=Field(default=823.15,ge=500,le=1200)
    reactor_temperature_K:float=Field(default=873.15,ge=500,le=1200)
    pressure_Pa:float=Field(default=100000,ge=1000,le=500000)
    conversion:float=Field(default=0.6,ge=0,lt=1)


class MixtureReactionInput(Inputs):
    feed_component_kmol_h:list[float]=Field(default=[3200.,400.,5.,.01],min_length=4,max_length=4,
        description="水/乙苯/苯乙烯/H2实际气相进料，保留回流产品和溶解气体")
    inlet_temperature_K:float=Field(default=823.15,ge=500,le=1200)
    reactor_temperature_K:float=Field(default=873.15,ge=500,le=1200)
    pressure_Pa:float=Field(default=100000,ge=1000,le=500000)
    conversion:float=Field(default=.6,ge=0,lt=1)

    @model_validator(mode="after")
    def feed_basis(self):
        if any(not math.isfinite(v) or v<0 for v in self.feed_component_kmol_h) or self.feed_component_kmol_h[1]<=0 or sum(self.feed_component_kmol_h)>1e7:
            raise ValueError("四元气相进料须非负、乙苯为正且总量不超过1e7 kmol/h")
        return self

def tabular_integrals(name,temperature):
    """Analytic integral of piecewise linear Cp and Cp/T, with zero extrapolation."""
    if not TEMPERATURES[0]<=temperature<=TEMPERATURES[-1]:
        raise ValueError("热容积分超出 NIST 表格温度范围")
    lower,upper=sorted([T0,temperature])
    h,s=0.0,0.0
    values=DATA[name]["Cp"]
    for left,right,cpl,cpr in zip(TEMPERATURES,TEMPERATURES[1:],values,values[1:]):
        a,b=max(left,lower),min(right,upper)
        if b<=a:
            continue
        slope=(cpr-cpl)/(right-left)
        intercept=cpl-slope*left
        h+=slope*(b*b-a*a)/2+intercept*(b-a)
        s+=slope*(b-a)+intercept*math.log(b/a)
    sign=1 if temperature>=T0 else -1
    return sign*h,sign*s

def gas_state(name,temperature):
    if name in DATA:
        dh,ds=tabular_integrals(name,temperature)
        d=DATA[name]
        return d["Hf_J_mol"]+dh,d["S0_J_mol_K"]+ds
    c=Chemical(name)
    cp=c.HeatCapacityGas
    cp.method="WEBBOOK_SHOMATE" if name=="hydrogen" else "TRCIG"
    lower,upper=cp.T_limits[cp.method]
    if not lower<=min(T0,temperature)<=max(T0,temperature)<=upper:
        raise ValueError("热容积分超出关联式温度范围")
    # H2 at standard elemental reference has zero formation enthalpy.
    hf=0 if name=="hydrogen" else c.Hfgm
    s0=130.68 if name=="hydrogen" else c.S0gm
    return hf+cp.T_dependent_property_integral(T0,temperature),s0+cp.T_dependent_property_integral_over_T(T0,temperature)

def thermodynamics(temperature):
    h_eb,s_eb=gas_state("ethylbenzene",temperature)
    h_sm,s_sm=gas_state("styrene",temperature)
    h_h,s_h=gas_state("hydrogen",temperature)
    dh=h_sm+h_h-h_eb
    ds=s_sm+s_h-s_eb
    dg=dh-temperature*ds
    return {"delta_H_J_mol":dh,"delta_S_J_mol_K":ds,"delta_G_J_mol":dg,"Kp_dimensionless":math.exp(-dg/(R*temperature))}

def equilibrium_conversion(temperature,pressure,steam_ratio):
    kp=thermodynamics(temperature)["Kp_dimensionless"]
    return brentq(lambda x: x*x/(1-x)/(1+steam_ratio+x)*pressure/P0-kp,0,1-1e-14,xtol=1e-14)

def reaction_thermo(p):
    thermo=thermodynamics(p.reactor_temperature_K)
    xeq=equilibrium_conversion(p.reactor_temperature_K,p.pressure_Pa,p.steam_to_ethylbenzene)
    n=p.ethylbenzene_kmol_h
    x=p.conversion
    flows={"ethylbenzene":n*(1-x),"styrene":n*x,"hydrogen":n*x,"water":n*p.steam_to_ethylbenzene}
    inlet={"ethylbenzene":n,"water":n*p.steam_to_ethylbenzene}
    # kmol/h times J/mol is kJ/h; divide by 3600 gives kW.
    hin=sum(flow*gas_state(name,p.inlet_temperature_K)[0] for name,flow in inlet.items())/3600
    hout=sum(flow*gas_state(name,p.reactor_temperature_K)[0] for name,flow in flows.items())/3600
    sensible=sum(flow*(gas_state(name,p.reactor_temperature_K)[0]-gas_state(name,p.inlet_temperature_K)[0]) for name,flow in inlet.items())/3600
    reaction=n*x*thermo["delta_H_J_mol"]/3600
    duty=hout-hin
    residual=abs(duty-sensible-reaction)/max(1,abs(duty))
    mass_in=sum(flow*Chemical(name).MW for name,flow in inlet.items())
    mass_out=sum(flow*Chemical(name).MW for name,flow in flows.items())
    feasible=x<=xeq+1e-9
    warnings=["理想气体单反应筛选模型；不含催化动力学、副反应、压降、非理想逸度和失活，不能用于确定反应器尺寸。",
              "所有进料均为气相；供热量不含原料蒸发、锅炉产汽、管路损失及分离热负荷。默认输入是示例。",
              "Cp 使用 NIST/TRC 推荐表格的分段线性插值；没有做超温外推。数据出处不等于已完成不确定度传播。"]
    if not feasible:
        warnings.append("指定转化率超过当前理想气体平衡上限；需要改变条件或使用经验证的模型，不能把该输入当作可行设计。")
    sources=[]
    for name,d in DATA.items():
        sources.append({"compound":name,**{k:v for k,v in d.items() if k!="Cp"},"Cp_reference":"TRC (1997), Selected Values of Properties of Chemical Compounds; NIST WebBook table","Cp_method":"piecewise-linear table integration","Cp_range_K":[50,1500]})
    return {"model":"ideal-gas C8H10 ⇌ C8H8 + H2, steam inert","standard_pressure_Pa":P0,
        "thermodynamics":thermo,"equilibrium_conversion":xeq,"specified_conversion":x,"equilibrium_feasible":feasible,
        "outlet_kmol_h":flows,"heat_duty_kW":duty,"sensible_heat_kW":sensible,"reaction_heat_kW":reaction,
        "validation":{"energy_relative_residual":residual,"mass_relative_residual":abs(mass_out-mass_in)/mass_in,"passed":residual<1e-8 and abs(mass_out-mass_in)/mass_in<1e-8 and feasible},
        "sources":sources+[{"compound":"hydrogen","Hf_J_mol":0,"S0_J_mol_K":130.68,"Cp_method":"NIST WEBBOOK_SHOMATE via thermo"},{"compound":"water","Cp_method":"TRCIG via thermo; gas phase sensible enthalpy only"}],"warnings":warnings}


def mixture_reaction(p):
    names=["water","ethylbenzene","styrene","hydrogen"]
    feed=np.array(p.feed_component_kmol_h)
    thermo=thermodynamics(p.reactor_temperature_K)
    kp=thermo["Kp_dimensionless"]
    water,eb,sm,h2=feed
    # Cross-multiplied mass action remains defined at zero SM/H2 and permits
    # a negative equilibrium extent for product-rich recycle feeds.
    extent_eq=brentq(lambda e:(sm+e)*(h2+e)*p.pressure_Pa-kp*P0*(eb-e)*(float(feed.sum())+e),
        -min(sm,h2),eb,xtol=1e-12)
    extent=eb*p.conversion
    out=feed+extent*np.array([0.,-1.,1.,1.])
    h_in=np.array([gas_state(name,p.inlet_temperature_K)[0] for name in names])
    h_out=np.array([gas_state(name,p.reactor_temperature_K)[0] for name in names])
    hin=float(feed@h_in/3600);hout=float(out@h_out/3600)
    sensible=float(feed@(h_out-h_in)/3600)
    reaction=extent*thermo["delta_H_J_mol"]/3600
    duty=hout-hin
    atom=np.array([[0.,8.,8.,0.],[2.,10.,8.,2.],[1.,0.,0.,0.]])@(out-feed)
    mw=np.array([Chemical(name).MW for name in names])
    mass_error=abs(float((out-feed)@mw))/float(feed@mw)
    energy_error=abs(duty-sensible-reaction)/max(1.,abs(duty))
    feasible=extent<=extent_eq+max(1e-10,eb*1e-10)
    quotient=float(out[2]*out[3]/out[1]/out.sum()*p.pressure_Pa/P0)
    return {"model":"ideal-gas dehydrogenation with arbitrary water/EB/SM/H2 inlet",
        "components":names,"inlet_kmol_h":dict(zip(names,feed.tolist())),"outlet_kmol_h":dict(zip(names,out.tolist())),
        "reaction_extent_kmol_h":extent,"equilibrium_extent_kmol_h":extent_eq,"equilibrium_conversion":extent_eq/eb,
        "specified_conversion":p.conversion,"equilibrium_feasible":bool(feasible),"thermodynamics":thermo,
        "outlet_reaction_quotient":quotient,"heat_duty_kW":duty,"sensible_heat_kW":sensible,"reaction_heat_kW":reaction,
        "inlet_enthalpy_flow_kW":hin,"outlet_enthalpy_flow_kW":hout,
        "validation":{"atom_balance_kmol_atoms_h":atom.tolist(),"mass_relative_residual":mass_error,
            "energy_relative_residual":energy_error,"passed":bool(feasible and mass_error<1e-8 and energy_error<1e-8 and max(abs(atom))<1e-6)},
        "sources":[{"compound":name,**{k:v for k,v in DATA[name].items() if k!="Cp"},"Cp_reference":"NIST/TRC 1997 table, piecewise-linear integration"} for name in DATA],
        "warnings":["实际进料中的苯乙烯和H2保留于物料、能量和质量作用式，可能降低或反转平衡反应程度。",
            "指定正向转化率须不超过该混合进料的理想气体平衡上限；未通过时validation.passed为false。",
            "单反应气相热力学模型，不含催化动力学、失活、副反应、聚合、非理想逸度或设备设计。",
            "进出口均为气相，热负荷未包含冷态液体汽化与预热；需要由流程另行连接实际焓。"]}
