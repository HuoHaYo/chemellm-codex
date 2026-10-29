"""Low-pressure H2/water/ethylbenzene/styrene condenser screening.

Solvents: Dortmund UNIFAC. H2: dilute Henry model derived from a single
mixture Gibbs function, with water data from Sander and organic PR predictions.
No fitted hydrogen/organic interaction data or experimental validation implied.
"""
import math
import time
import numpy as np
from pydantic import Field, model_validator
from scipy.optimize import least_squares, minimize
from scipy.special import logsumexp, softmax
from thermo import ChemicalConstantsPackage, UNIFAC, PRMIX
from thermo.unifac import DOUFIP2016, DOUFSG
from CoolProp.CoolProp import PropsSI
from app.schemas import Inputs
from app.reaction import gas_state, R

COMPONENTS=["water","ethylbenzene","styrene","hydrogen"]
SANDER_URL="https://henrys-law.org/henry/casrn/1333-74-0"

def organic_henry_predictions(constants,t,multiplier=1):
    organic=[]
    for i in (1,2):
        h=[]
        for trace in (1e-9,1e-10):
            eos=PRMIX(Tcs=[constants.Tcs[i],constants.Tcs[3]],Pcs=[constants.Pcs[i],constants.Pcs[3]],
                omegas=[constants.omegas[i],constants.omegas[3]],zs=[1-trace,trace],kijs=[[0.,0.],[0.,0.]],T=t,P=100000)
            if not hasattr(eos,"lnphis_l"):raise ValueError("PR未返回有机溶剂液相，无法预测氢气无限稀释亨利常数")
            h.append(math.exp(eos.lnphis_l[1])*100000)
        if abs(h[0]/h[1]-1)>1e-6:raise ValueError("PR无限稀释预测尚未达到组成极限")
        organic.append(h[1]*multiplier)
    return organic

class CondenserInput(Inputs):
    feed_kmol_h:float=Field(default=100,gt=0,le=1e7)
    mole_fractions:list[float]=Field(default=[8/9.6,.4/9.6,.6/9.6,.6/9.6],min_length=4,max_length=4,
        description="固定顺序：水、乙苯、苯乙烯、氢气；氢气摩尔分数至少0.005")
    temperature_K:float=Field(default=310,ge=303,le=313.15)
    pressure_Pa:float=Field(default=100000,ge=50000,le=200000)
    organic_henry_multiplier:float=Field(default=1,ge=.25,le=4,
        description="有机溶剂PR预测亨利常数的敏感性倍率，不能当作实验拟合参数")

    @model_validator(mode="after")
    def composition(self):
        if any(not math.isfinite(x) or x<1e-9 for x in self.mole_fractions) or abs(sum(self.mole_fractions)-1)>1e-9:
            raise ValueError("四种组分的摩尔分数均须不小于1e-9且总和为1，顺序为水、乙苯、苯乙烯、氢气")
        if self.mole_fractions[3]<.005 or self.mole_fractions[3]>.9:
            raise ValueError("当前低压冷凝模型要求进料氢气摩尔分数在0.005–0.9之间")
        return self

class HydrogenThermo:
    def __init__(self,temperature,pressure,multiplier=1):
        self.t=temperature;self.p=pressure;self.multiplier=multiplier
        self.constants,self.correlations=ChemicalConstantsPackage.from_IDs(COMPONENTS)
        self.psat=[];self.dlnpsat=[];self.methods=[]
        for name,vp in zip(COMPONENTS[:3],self.correlations.VaporPressures[:3]):
            if vp.Tmin is None or vp.Tmax is None or not vp.Tmin<=temperature<=vp.Tmax:
                raise ValueError(name+"蒸气压关联式不支持指定温度")
            value=vp(temperature)
            self.psat.append(value)
            self.dlnpsat.append(vp.T_dependent_property_derivative(temperature)/value)
            self.methods.append({"component":name,"vapor_pressure_method":vp.method,"range_K":[vp.Tmin,vp.Tmax]})
        self.lnpsat=np.log(np.array(self.psat)/pressure)
        self.henry=self.henry_at(temperature)
        self.logh=np.log(self.henry/pressure)
        step=.002
        self.dlogh=(np.log(self.henry_at(temperature+step))-np.log(self.henry_at(temperature-step)))/(2*step)
        self.ge=UNIFAC.from_subgroups(T=temperature,xs=[1/3]*3,
            chemgroups=self.constants.UNIFAC_Dortmund_groups[:3],subgroups=DOUFSG,interaction_data=DOUFIP2016,version=1)

    def henry_at(self,t):
        # Sander Hscp = c(H2)/p(H2), mol/(m3 Pa). Convert to Hpx = p/x
        # using the pure solvent molar density, not an extra factor of RT.
        hscp=7.8e-6*math.exp(530*(1/t-1/298.15))
        density=PropsSI("D","T",t,"P",100000,"Water")
        water=density/(self.constants.MWs[0]/1000)/hscp
        return np.array([water,*organic_henry_predictions(self.constants,t,self.multiplier)])

    def liquid_logf(self,x):
        x=np.asarray(x);q=x[3];s=1-q
        if s<=0:raise ValueError("Henry液相必须含溶剂")
        w=x[:3]/s
        gamma=np.array(self.ge.to_T_xs(T=self.t,xs=w.tolist()).gammas())
        average=float(w@self.logh)
        # These solvent corrections follow from nH2 * sum(w_i ln H_i).
        # Omitting them would violate Gibbs–Duhem for a variable H_mix.
        return np.r_[np.log(x[:3])+np.log(gamma)+self.lnpsat+q/s*(self.logh-average),math.log(q)+average]

    def liquid_enthalpy(self,x):
        x=np.asarray(x);s=1-x[3];w=x[:3]/s
        ge=self.ge.to_T_xs(T=self.t,xs=w.tolist())
        dge_rt=(ge.dGE_dT()/self.t-ge.GE()/self.t**2)/R
        hgas=np.array([gas_state(name,self.t)[0] for name in COMPONENTS])
        # Common ideal-gas reference. Temperature derivatives of this same
        # Gibbs model determine condensation and dilute dissolution enthalpy.
        return float(x@hgas-R*self.t**2*(x[:3]@self.dlnpsat+s*dge_rt+x[3]*(w@self.dlogh)))

    def stability(self,reference):
        def liquid_tpd(w):
            ge=self.ge.to_T_xs(T=self.t,xs=w.tolist())
            b=float(w@(np.log(w)+self.lnpsat-reference[:3]))+ge.GE()/(R*self.t)
            c=float(w@self.logh-reference[3])
            # Minimize hydrogen composition analytically at each solvent ratio.
            value=-float(logsumexp([-b,-c]))
            q=float(softmax([-b,-c])[1])
            return value,np.r_[(1-q)*w,q]
        trials=[]
        for a in range(21):
            for b in range(21-a):
                w=np.maximum(np.array([a,b,20-a-b],dtype=float)/20,1e-12);w/=w.sum()
                value,x=liquid_tpd(w)
                trials.append((value,x))
        trials.sort(key=lambda item:item[0])
        # Include both nearly pure aqueous and organic seeds and minima of the
        # grid. This is a search for instability, never a global stability proof.
        seeds=[item[1][:3]/item[1][:3].sum() for item in trials[:4]]
        seeds.extend([np.array([1-2e-6,1e-6,1e-6]),np.array([.02,.49,.49])])
        for w in seeds:
            opt=minimize(lambda v:liquid_tpd(softmax(np.r_[v,0.]))[0],np.log(w[:2]/w[2]),method="L-BFGS-B",
                bounds=[(-28,28)]*2,options={"maxiter":150,"ftol":1e-13,"gtol":1e-9})
            value,x=liquid_tpd(softmax(np.r_[opt.x,0.]))
            trials.append((value,x))
        value,x=min(trials,key=lambda item:item[0])
        return {"minimum_liquid_TPD":float(value),"ideal_gas_TPD":-float(logsumexp(reference)),
            "liquid_grid_points":231,"local_search_starts":len(seeds)},x

def condenser_flash(p):
    started=time.monotonic()
    thermo=HydrogenThermo(p.temperature_K,p.pressure_Pa,p.organic_henry_multiplier)
    z=np.array(p.mole_fractions)
    # Try VLL and both VL branches. Stable all-vapor states are also considered.
    gas=np.array([thermo.psat[0]/p.pressure_Pa,.005,.005,.9]);gas/=gas.sum()
    guesses=[np.array([.99980,.00002,.00018]),np.array([.014,.3944,.5916])]
    liquids=[]
    for w in guesses:
        w/=w.sum()
        q=min(.001,max(1e-9,.9*p.pressure_Pa/math.exp(w@np.log(thermo.henry))))
        liquids.append(np.r_[w*(1-q),q])
    candidates=[]
    gas_check,_=thermo.stability(np.log(z))
    if gas_check["minimum_liquid_TPD"]>=-1e-7:
        candidates.append(([z],[1.],gas_check,0.,0))
    attempts=[]
    for liquid_seeds in ([liquids[0],liquids[1]],[liquids[0]],[liquids[1]]):
        phase_seeds=[gas,*liquid_seeds];count=len(phase_seeds)
        beta=np.array([max(z[3],.01),max(z[0],.01),max(z[1]+z[2],.01)])[:count];beta/=beta.sum()
        initial=np.r_[*(np.log(x[:3]/x[3]) for x in phase_seeds),np.log(beta[:-1]/beta[-1])]
        def unpack(u):
            x=[softmax(np.r_[u[3*j:3*j+3],0.]) for j in range(count)]
            beta=softmax(np.r_[u[3*count:],0.])
            return x,beta
        def residual(u):
            if time.monotonic()-started>45:raise ValueError("含氢分相求解超过45秒；请调整工况后重试")
            x,beta=unpack(u)
            reference=np.log(x[0])
            equilibrium=np.concatenate([thermo.liquid_logf(v)-reference for v in x[1:]])
            closure=sum((b*v for b,v in zip(beta,x)),start=np.zeros(4))-z
            return np.r_[equilibrium,closure[:3]/np.maximum(z[:3],.02)]
        solution=least_squares(residual,initial,bounds=(-35.,35.),ftol=1e-11,xtol=1e-11,gtol=1e-11,max_nfev=400)
        error=float(np.max(np.abs(residual(solution.x))))
        x,beta=unpack(solution.x)
        if error>1e-7 or min(beta)<1e-8:
            attempts.append({"phases":count,"residual":error,"accepted":False});continue
        if count==3 and np.max(np.abs(x[1]-x[2]))<1e-4:continue
        check,_=thermo.stability(np.log(x[0]))
        stable=min(check["minimum_liquid_TPD"],check["ideal_gas_TPD"])>=-1e-6
        attempts.append({"phases":count,"residual":error,"accepted":stable})
        if stable:candidates.append((x,beta,check,error,int(solution.nfev)))
    if not candidates:
        raise ValueError("含氢分相未获得通过逸度、守恒和稳定性搜索的解："+str(attempts))
    x,beta,check,error,evaluations=min(candidates,key=lambda candidate:float(z@np.log(candidate[0][0])))
    phases=[]
    hgas=sum(z_i*gas_state(name,p.temperature_K)[0] for z_i,name in zip(x[0],COMPONENTS))
    for j,(v,b) in enumerate(zip(x,beta)):
        kind="vapor" if j==0 else "aqueous_liquid" if v[0]>.5 else "organic_liquid"
        if j and v[3]>.01:raise ValueError("液相氢气摩尔分数超过0.01，超出无限稀释模型的使用边界")
        ln_f=np.log(v) if j==0 else thermo.liquid_logf(v)
        enthalpy=hgas if j==0 else thermo.liquid_enthalpy(v)
        phases.append({"phase":kind,"mole_fraction_of_feed":float(b),"flow_kmol_h":float(b*p.feed_kmol_h),
            "mole_fractions":v.tolist(),"component_flows_kmol_h":(v*b*p.feed_kmol_h).tolist(),
            "component_fugacities_Pa":(np.exp(ln_f)*p.pressure_Pa).tolist(),"molar_enthalpy_J_mol":enthalpy})
    phases.sort(key=lambda phase:{"vapor":0,"aqueous_liquid":1,"organic_liquid":2}[phase["phase"]])
    recovered=sum((np.array(phase["component_flows_kmol_h"]) for phase in phases),start=np.zeros(4))
    mass_in=float(z@np.array(thermo.constants.MWs))*p.feed_kmol_h
    mass_out=float(recovered@np.array(thermo.constants.MWs))
    balance=float(np.max(np.abs(recovered/p.feed_kmol_h-z)))
    enthalpy_flow=sum(phase["flow_kmol_h"]*phase["molar_enthalpy_J_mol"]/3600 for phase in phases)
    return {"model":"Dortmund UNIFAC solvents + Gibbs-consistent dilute H2 Henry mixture / ideal gas",
        "components":COMPONENTS,"temperature_K":p.temperature_K,"pressure_Pa":p.pressure_Pa,
        "feed_kmol_h":p.feed_kmol_h,"feed_mole_fractions":p.mole_fractions,"phases":phases,
        "outlet_enthalpy_flow_kW":enthalpy_flow,
        "hydrogen_Hpx_Pa":dict(zip(COMPONENTS[:3],thermo.henry.tolist())),
        "organic_henry_multiplier":p.organic_henry_multiplier,"thermodynamic_methods":thermo.methods,
        "validation":{"max_component_fraction_residual":balance,"mass_relative_residual":abs(mass_out-mass_in)/mass_in,
            "max_scaled_equation_residual":error,**check,"passed":bool(balance<1e-7 and error<1e-7 and abs(mass_out-mass_in)/mass_in<1e-7)},
        "solver":{"method":"VLL and VL log-composition equilibrium candidates, finite stability search","accepted_nfev":evaluations,"attempts":attempts},
        "sources":[{"url":SANDER_URL,"compilation_DOI":"10.5194/acp-23-10901-2023","cited_reference_DOI":"10.1063/1.1564818",
            "Hscp_298_15_mol_m3_Pa":7.8e-6,"dlnHscp_dinverseT_K":530,"evidence":"Sander 2023 compilation page inspected; underlying paper not independently re-fitted"},
            {"url":"https://thermo.readthedocs.io/thermo.eos_mix.html","evidence":"organic H2 Henry constants predicted at infinite dilution using PR, kij=0, reference P=100 kPa; no experimental fit"}],
        "warnings":["研究筛选模型，未以该四元体系实验数据或独立模拟器验证。水中H2采用Sander汇编局部van't Hoff近似；有机溶剂亨利常数是PR/kij=0预测，不是实测值。",
            "亨利混合使用溶剂摩尔分数的对数加权，并从同一Gibbs函数导出溶剂修正；尚无混合溶剂H2数据验证。建议对organic_henry_multiplier做敏感性检查。",
            "只支持303–313.15 K、50–200 kPa；液相H2≤1 mol%。范围是实现边界，不是实验认证区间。有限稳定性搜索不是全局稳定性证明。",
            "忽略气相非理想性、Poynting修正和液体体积；焓由共同气相参考和该Gibbs模型温度导数构造。无其他组分、副反应、聚合或固相。"]}
