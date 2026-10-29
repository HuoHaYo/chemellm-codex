"""Four-component EB recycle with pump work and actual cold-feed enthalpy."""
import time
import logging
import numpy as np
from pydantic import Field
from app.separation_train import SeparationTrainInput, wet_separation_train
from app.hydrogen import COMPONENTS
from app.wet_column import wet_thermo
from app.reaction import gas_state, R


class RecycleInput(SeparationTrainInput):
    ethylbenzene_kmol_h:float=Field(default=400,gt=0,le=1e6,description="新鲜液态乙苯进料；循环乙苯由闭合计算得到")
    steam_to_ethylbenzene:float=Field(default=8,ge=.1,le=40,description="反应器入口总水/总乙苯摩尔比；扣除循环水后确定新鲜蒸汽量")
    column_stages:int=Field(default=50,ge=4,le=60)
    column_feed_stage:int=Field(default=25,ge=1,le=59)
    column_reflux_ratio:float=Field(default=10,ge=.1,le=30)
    recycle_purge_fraction:float=Field(default=.01,ge=.0001,le=1)
    recycle_pump_efficiency:float=Field(default=.7,gt=.1,le=1)
    recycle_molar_volume_m3_kmol:float=Field(default=.12,gt=.01,le=1,
        description="循环液体平均摩尔体积，用于初步泵功率；默认值是待验证的设计假设")
    fresh_temperature_K:float=Field(default=310,ge=303,le=320)
    target_styrene_mass_fraction:float=Field(default=.998,gt=.9,lt=1,
        description="用户指定的塔底苯乙烯质量纯度目标；默认值仅为研究假设，不含阻聚剂/完整产品标准")


def recycle_process(p):
    started=time.monotonic()
    fresh=p.ethylbenzene_kmol_h;purge=p.recycle_purge_fraction
    # This approximate EB-only recycle is an initial iterate, never a final
    # complete-separation assumption. All four returned components are solved.
    recovery=(1-purge)*(1-p.conversion)*.97
    recycle=np.array([0.,fresh*recovery/(1-recovery),0.,0.])
    history=[];xs=[];gs=[];fs=[];column=None
    for iteration in range(24):
        if time.monotonic()-started>240:raise ValueError("循环流程超过240秒，未返回成功结果")
        total_eb=fresh+recycle[1]
        steam=p.steam_to_ethylbenzene*total_eb-recycle[0]
        if steam<=0:raise ValueError("循环水已超过给定蒸汽比，不支持负蒸汽补充")
        feed=recycle+np.array([steam,fresh,0.,0.])
        args={k:getattr(p,k) for k in SeparationTrainInput.model_fields}
        args["ethylbenzene_kmol_h"]=total_eb
        train=wet_separation_train(SeparationTrainInput(**args),mixture_feed=feed,initial_column=column)
        if not train["validation"]["passed"]:raise ValueError("循环中的单程流程未通过物热检查")
        column=train["column"]
        distillate=next(s for s in column["outlets"] if s["outlet"]=="organic_distillate")
        g=(1-purge)*np.array([distillate["component_flows_kmol_h"][k] for k in COMPONENTS])
        f=g-recycle
        error=float(np.max(abs(f)/np.maximum(1.,abs(g))))
        history.append({"iteration":iteration+1,"inlet_recycle_kmol_h":recycle.tolist(),"returned_recycle_kmol_h":g.tolist(),"max_relative_or_absolute_residual":error})
        logging.getLogger(__name__).info("recycle iteration=%s error=%g",iteration+1,error)
        if error<1e-9:break
        xs.append(recycle.copy());gs.append(g.copy());fs.append(f.copy())
        candidate=g.copy()
        if len(fs)>=2:
            # Anderson acceleration, scaled by component flow magnitude.
            count=min(4,len(fs)-1)
            df=np.column_stack([fs[-i-2]-f for i in range(count)])
            dg=np.column_stack([gs[-i-2]-g for i in range(count)])
            scale=np.maximum(1.,abs(g))
            coefficients=np.linalg.lstsq(df/scale[:,None],-f/scale,rcond=1e-9)[0]
            accelerated=g+dg@coefficients
            if np.isfinite(accelerated).all() and np.min(accelerated)>=0 and np.max(accelerated)<fresh*20:
                candidate=accelerated
        recycle=candidate
    else:raise ValueError("四组分循环在24轮内未闭合，请调整排放比例与分离条件")
    thermo=wet_thermo(float(p.organic_henry_multiplier))
    mw=np.array(thermo.constants.MWs)
    fresh_h=thermo.data(p.fresh_temperature_K,True)
    # Pure-EB liquid from the same vapor-pressure Gibbs standard as the column.
    eb_h=float(fresh_h[0][7]-R*p.fresh_temperature_K**2*fresh_h[1][1])
    fresh_hflow=fresh*eb_h/3600
    steam_hflow=steam*gas_state("water",p.inlet_temperature_K)[0]/3600
    recycle_hflow=0.
    if recycle.sum()>0:
        recycle_hflow=float(recycle.sum()*thermo.phase(p.condenser_temperature_K,recycle/recycle.sum(),p.column_pressure_Pa,True)[2]/3600)
    pump=float(recycle.sum()*p.recycle_molar_volume_m3_kmol*(p.pressure_Pa-p.column_pressure_Pa)/(3.6e6*p.recycle_pump_efficiency))
    conditioning=train["streams"][0]["enthalpy_flow_kW"]-fresh_hflow-steam_hflow-recycle_hflow-pump
    streams=[dict(s) for s in train["streams"]]
    for s in streams:
        if s["id"] in {"S01","S06"}:s["boundary"]="internal"
    def stream(tag,name,flows,t,pressure,h,phase,boundary):
        return {"id":tag,"name":name,"component_flows_kmol_h":dict(zip(COMPONENTS,map(float,flows))),
            "total_kmol_h":float(sum(flows)),"mass_kg_h":float(flows@mw),"temperature_K":t,"pressure_Pa":pressure,
            "enthalpy_flow_kW":h,"phase":phase,"boundary":boundary}
    streams.extend([stream("F01","新鲜液态乙苯",np.array([0.,fresh,0.,0.]),p.fresh_temperature_K,p.pressure_Pa,fresh_hflow,"liquid","feed"),
        stream("F02","补充水蒸汽",np.array([steam,0.,0.,0.]),p.inlet_temperature_K,p.pressure_Pa,steam_hflow,"vapor","feed"),
        stream("R01","循环液（泵前/断开物流输入）",recycle,p.condenser_temperature_K,p.column_pressure_Pa,recycle_hflow,"organic_liquid","internal")])
    purge_flows=purge*np.array([distillate["component_flows_kmol_h"][k] for k in COMPONENTS])
    streams.append(stream("P01","循环排放（待回收处理）",purge_flows,p.condenser_temperature_K,p.column_pressure_Pa,
        purge*distillate["enthalpy_flow_kW"],"organic_liquid","outlet"))
    exits=[s for s in streams if s["boundary"]=="outlet"]
    external_in=np.array([steam,fresh,0.,0.])
    external_out=sum((np.array([s["component_flows_kmol_h"][k] for k in COMPONENTS]) for s in exits),start=np.zeros(4))
    component=external_out-external_in-np.array([0.,-1.,1.,1.])*train["reaction_extent_kmol_h"]
    atom=np.array([[0.,8.,8.,0.],[2.,10.,8.,2.],[1.,0.,0.,0.]])@(external_out-external_in)
    heat_in=train["total_heat_input_kW"]+conditioning
    heat_out=train["total_heat_removed_kW"]
    energy=fresh_hflow+steam_hflow+heat_in+pump-heat_out-sum(s["enthalpy_flow_kW"] for s in exits)
    mass_error=abs(float((external_out-external_in)@mw))/float(external_in@mw)
    product=next(s for s in column["outlets"] if s["outlet"]=="bottoms")
    product_mass_fraction=product["component_flows_kmol_h"]["styrene"]*mw[2]/sum(product["component_flows_kmol_h"][k]*mw[i] for i,k in enumerate(COMPONENTS))
    passed=bool(train["validation"]["passed"] and error<1e-9 and max(abs(component))<1e-5 and max(abs(atom))<1e-4
        and mass_error<1e-8 and abs(energy)<1e-3)
    return {"model":"four-component recycled dehydrogenation/condenser/wet-column with purge, pump and feed conditioning",
        "components":COMPONENTS,"streams":streams,"column":column,"reactor":train["frontend"]["reactor"],
        "fresh_ethylbenzene_kmol_h":fresh,"makeup_steam_kmol_h":steam,"recycle_component_kmol_h":dict(zip(COMPONENTS,recycle.tolist())),
        "reaction_extent_kmol_h":train["reaction_extent_kmol_h"],"reactor_heat_input_kW":train["reactor_heat_input_kW"],
        "condenser_heat_removed_kW":train["condenser_heat_removed_kW"],"column_reboiler_duty_kW":column["reboiler_duty_kW"],
        "column_condenser_duty_removed_kW":column["condenser_duty_removed_kW"],"feed_conditioning_heat_kW":conditioning,
        "recycle_pump_work_kW":pump,"total_heat_input_kW":heat_in,"total_heat_removed_kW":heat_out,
        "recycle_solver":{"method":"four-component fixed point with Anderson acceleration and preceding-column initialization","history":history,
            "elapsed_seconds":time.monotonic()-started},
        "product_specification":{"target_styrene_mass_fraction":p.target_styrene_mass_fraction,"predicted_styrene_mass_fraction":float(product_mass_fraction),
            "meets_assumed_purity_target":bool(product_mass_fraction>=p.target_styrene_mass_fraction),
            "styrene_product_kmol_h":product["component_flows_kmol_h"]["styrene"],"styrene_recovery_of_net_reaction":product["component_flows_kmol_h"]["styrene"]/train["reaction_extent_kmol_h"],
            "basis":"指定的质量纯度研究目标；未包含阻聚剂、其他杂质及完整产品标准，非工程验收"},
        "validation":{"component_balance_including_reaction_kmol_h":dict(zip(COMPONENTS,component.tolist())),
            "atom_balance_kmol_atoms_h":atom.tolist(),"mass_relative_residual":mass_error,"global_energy_residual_kW":energy,
            "recycle_max_relative_or_absolute_residual":error,"passed":passed},
        "warnings":["闭合循环的水、乙苯、苯乙烯和H2均保留；指定正向转化率须低于混合进料理想气体平衡上限。非催化动力学/副反应设计。",
            "循环泵功率采用用户给定平均液体摩尔体积和效率，默认值是待验证假设；未求出口温升、NPSH、泵曲线或机械设计。轴功进入预热及全流程能量衡算。",
            "进料调温热负荷包括新鲜乙苯和冷循环液的汽化/预热；外部水蒸汽已经以指定气相温度供应，不包含锅炉产汽和全厂热集成。",
            "塔底仅对用户指定质量纯度作数学比较，不能据此声称满足完整产品标准或赛题要求；物性仍缺实验/独立模拟器基准。",
            "两股水相、含氢尾气及循环排放仍需回收和环保设计；不含阻聚、真空系统、设备尺寸、安全、全厂公用工程或经济优化。"]}
