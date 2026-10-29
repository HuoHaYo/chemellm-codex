"""Water/ethylbenzene/styrene VLLE screening with Dortmund UNIFAC.

This intentionally excludes hydrogen: its gas/liquid partition needs a Henry
or other validated fugacity model. It must not silently enter a Raoult liquid.
"""
import math
import numpy as np
from pydantic import Field, model_validator
from thermo import ChemicalConstantsPackage, UNIFAC, GibbsExcessLiquid, IdealGas, FlashVLN
from thermo.unifac import DOUFIP2016, DOUFSG
from app.schemas import Inputs

COMPONENTS=["water","ethylbenzene","styrene"]

class AqueousFlashInput(Inputs):
    feed_kmol_h:float=Field(default=100,gt=0,le=1e7)
    mole_fractions:list[float]=Field(default=[.8,.08,.12],min_length=3,max_length=3,
        description="按水、乙苯、苯乙烯顺序；不含氢气或其他组分")
    temperature_K:float=Field(default=310,ge=300,le=400)
    pressure_Pa:float=Field(default=101325,ge=5000,le=300000)

    @model_validator(mode="after")
    def fractions(self):
        if any(not math.isfinite(x) or x<1e-9 for x in self.mole_fractions) or abs(sum(self.mole_fractions)-1)>1e-9:
            raise ValueError("水、乙苯、苯乙烯摩尔分数均须不小于1e-9，且总和为1；含氢气物流不能使用此三元模型")
        return self

def aqueous_flash(p):
    constants,correlations=ChemicalConstantsPackage.from_IDs(COMPONENTS)
    methods=[]
    for i,name in enumerate(COMPONENTS):
        for key,objects in [("vapor_pressure",correlations.VaporPressures),("latent_heat",correlations.EnthalpyVaporizations)]:
            obj=objects[i]
            if obj.Tmin is None or obj.Tmax is None or not obj.Tmin<=p.temperature_K<=obj.Tmax:
                raise ValueError(f"{name} {key} 超出可验证的物性温度范围")
            methods.append({"component":name,"property":key,"method":obj.method,"range_K":[obj.Tmin,obj.Tmax]})
    ge=UNIFAC.from_subgroups(T=p.temperature_K,xs=p.mole_fractions,
        chemgroups=constants.UNIFAC_Dortmund_groups,subgroups=DOUFSG,interaction_data=DOUFIP2016,version=1)
    liquid=GibbsExcessLiquid(VaporPressures=correlations.VaporPressures,
        VolumeLiquids=correlations.VolumeLiquids,HeatCapacityGases=correlations.HeatCapacityGases,
        GibbsExcessModel=ge,EnthalpyVaporizations=correlations.EnthalpyVaporizations,
        equilibrium_basis="Psat",caloric_basis="Hvap")
    engine=FlashVLN(constants,correlations,liquids=[liquid,liquid],
        gas=IdealGas(HeatCapacityGases=correlations.HeatCapacityGases))
    state=engine.flash(T=p.temperature_K,P=p.pressure_Pa,zs=p.mole_fractions)
    psat=np.array([vp(p.temperature_K) for vp in correlations.VaporPressures])
    phases=[]
    potentials=[]
    for phase,beta in zip(state.phases,state.betas):
        x=np.array(phase.zs)
        if phase is state.gas:
            kind="vapor"
            gamma=None
            f=x*p.pressure_Pa
        else:
            kind="aqueous_liquid" if x[0]>.5 else "organic_liquid"
            gamma=ge.to_T_xs(T=p.temperature_K,xs=x.tolist()).gammas()
            # Re-evaluate original UNIFAC and Psat rather than trusting a saved
            # FlashVLN convergence flag or reconstructed material balance alone.
            f=x*np.array(gamma)*psat
        if min(x)<=0 or not np.all(np.isfinite(f)) or min(f)<=0:
            raise ValueError("相平衡解含无效组成或逸度，请调整工况并核对热力学模型")
        potentials.append(np.log(f/p.pressure_Pa))
        phases.append({"phase":kind,"mole_fraction_of_feed":float(beta),"flow_kmol_h":float(beta*p.feed_kmol_h),
            "mole_fractions":x.tolist(),"component_flows_kmol_h":(x*beta*p.feed_kmol_h).tolist(),
            "activity_coefficients":gamma,"component_fugacities_Pa":f.tolist()})
    reference=potentials[int(np.argmax(state.betas))]
    fugacity_residual=max(float(np.max(np.abs(d-reference))) for d in potentials)
    reconstructed=sum((beta*np.array(phase.zs) for phase,beta in zip(state.phases,state.betas)),start=np.zeros(3))
    closure=float(np.max(np.abs(reconstructed-p.mole_fractions)))
    # A finite stability screen is reported as such; it is not a global proof.
    candidates=[]
    for a in range(21):
        for b in range(21-a):
            x=np.maximum(np.array([a,b,20-a-b],dtype=float)/20,1e-12)
            x/=x.sum()
            gamma=np.array(ge.to_T_xs(T=p.temperature_K,xs=x.tolist()).gammas())
            candidates.append(float(np.dot(x,np.log(x*gamma*psat/p.pressure_Pa)-reference)))
    # Minimum ideal-gas TPD can be calculated analytically.
    gas_tpd=-math.log(float(np.exp(reference).sum()))
    min_tpd=min(candidates+[gas_tpd])
    beta_error=abs(sum(state.betas)-1)
    passed=closure<1e-8 and beta_error<1e-8 and min(state.betas)>=0 and fugacity_residual<1e-5 and min_tpd>=-1e-6
    if not passed:
        raise ValueError(f"三元分相未通过独立检查：组分残差 {closure:.3g}，对数逸度残差 {fugacity_residual:.3g}，采样稳定性 TPD {min_tpd:.3g}")
    return {"model":"Dortmund UNIFAC (version 1, DOUFIP2016) liquid / ideal gas; up to VLL",
        "components":COMPONENTS,"temperature_K":p.temperature_K,"pressure_Pa":p.pressure_Pa,
        "feed_kmol_h":p.feed_kmol_h,"feed_mole_fractions":p.mole_fractions,"phase":state.phase,"phases":phases,
        "validation":{"max_component_fraction_residual":closure,"phase_fraction_sum_residual":beta_error,
            "max_log_fugacity_residual":fugacity_residual,"minimum_sampled_liquid_TPD":min(candidates),
            "ideal_gas_TPD":gas_tpd,"liquid_stability_sample_points":len(candidates),"passed":bool(passed)},
        "thermodynamic_methods":methods,"UNIFAC_groups":constants.UNIFAC_Dortmund_groups,
        "sources":["https://thermo.readthedocs.io/thermo.unifac.html","https://thermo.readthedocs.io/thermo.flash.html"],
        "warnings":["三元等温分相筛选，按水、乙苯、苯乙烯顺序输入；不包含氢气、杂质、反应、聚合或固相。不能直接接收完整反应器出口物流。",
            "UNIFAC为基团贡献预测，尚未以该体系实验VLE/LLE或独立模拟器校核。气相为理想气体，液相忽略Poynting修正。",
            "独立检查包括物料闭合、共存相逸度和231点液相稳定性筛查；有限采样不能证明全局稳定性。当前范围300–400 K、5–300 kPa是实现边界，不是实验验证区间。",
            "输出保留有机相含水和水相溶解有机物，不视作完全分割；未计算冷凝热负荷、分离设备尺寸或停留时间。默认输入仅为演示假设。"]}
