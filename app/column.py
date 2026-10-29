"""Binary equilibrium-stage MESH with energy balances and a total condenser.

Ideal Raoult liquid/ideal gas; a converged column is not a validated plant.
"""
import math
import time
from functools import lru_cache
import numpy as np
from pydantic import Field, model_validator
from scipy.interpolate import CubicSpline
from scipy.optimize import brentq, least_squares
from scipy.sparse import lil_matrix
from thermo import Chemical
from app.schemas import Inputs
from app.reaction import gas_state

NAMES={"苯":"benzene","甲苯":"toluene","乙苯":"ethylbenzene","苯乙烯":"styrene"}
SUPPORTED={"benzene","toluene","ethylbenzene","styrene"}

class ColumnInput(Inputs):
    light_component:str=Field(default="benzene",max_length=100)
    heavy_component:str=Field(default="toluene",max_length=100)
    feed_kmol_h:float=Field(default=100,gt=0,le=1e6)
    feed_light_fraction:float=Field(default=0.5,gt=0,lt=1)
    equilibrium_stages:int=Field(default=10,ge=3,le=100)
    feed_stage:int=Field(default=5,ge=1,le=99)
    pressure_Pa:float=Field(default=101325,ge=5000,le=300000)
    distillate_fraction:float=Field(default=0.5,gt=0.001,lt=0.999)
    reflux_ratio:float=Field(default=3,gt=0.01,le=100)

    @model_validator(mode="after")
    def design(self):
        if self.feed_stage>=self.equilibrium_stages:
            raise ValueError("进料级必须位于塔板上，不能设在末级部分再沸器")
        names=[NAMES.get(self.light_component,self.light_component.lower()),NAMES.get(self.heavy_component,self.heavy_component.lower())]
        if len(set(names))!=2 or any(n not in SUPPORTED for n in names):
            raise ValueError("当前热力学实现仅支持苯、甲苯、乙苯、苯乙烯组成的不同二元组分")
        return self

class BinaryThermo:
    def __init__(self,light,heavy,pressure):
        self.names=[light,heavy]
        self.p=pressure
        self.chemicals=[Chemical(n) for n in self.names]
        self.methods=[]
        for name,c in zip(self.names,self.chemicals):
            vp,hv=c.VaporPressure,c.EnthalpyVaporization
            if None in (vp.Tmin,vp.Tmax,hv.Tmin,hv.Tmax,c.Tc):
                raise ValueError("热力学关联式缺少可验证温度范围")
            self.methods.append({"component":name,"CAS":c.CAS,"vapor_pressure_method":vp.method,
                "vapor_pressure_range_K":[vp.Tmin,vp.Tmax],"latent_heat_method":hv.method,
                "latent_heat_range_K":[hv.Tmin,hv.Tmax],
                "gas_Cp_method":"NIST/TRC table" if name in {"styrene","ethylbenzene"} else c.HeatCapacityGas.method})
        low=max(max(c.VaporPressure.Tmin,c.EnthalpyVaporization.Tmin) for c in self.chemicals)
        high=min(min(c.VaporPressure.Tmax,c.EnthalpyVaporization.Tmax,c.Tc*.98) for c in self.chemicals)
        if low>=high:
            raise ValueError("两个组分关联式没有共同适用温度范围")
        pure=[]
        for c in self.chemicals:
            if not c.VaporPressure(low)<pressure<c.VaporPressure(high):
                raise ValueError("指定压力下的沸点超出所选物性关联式共同温度范围")
            pure.append(brentq(lambda t:c.VaporPressure(t)-pressure,low,high))
        if pure[0]>=pure[1]:
            raise ValueError("指定轻组分的沸点不低于重组分，请交换轻重组分")
        self.t_bounds=[min(pure),max(pure)]
        self.xgrid=np.linspace(0,1,401)
        values=[self.exact(x) for x in self.xgrid]
        self.table=CubicSpline(self.xgrid,np.asarray(values),axis=0)

    def ideal_gas_h(self,name,c,t):
        if name in {"styrene","ethylbenzene"}:
            return gas_state(name,t)[0]-gas_state(name,298.15)[0]
        cp=c.HeatCapacityGas
        if not cp.Tmin<=min(t,298.15)<=max(t,298.15)<=cp.Tmax:
            raise ValueError("气相热容积分超出关联式温度范围")
        return cp.T_dependent_property_integral(298.15,t)

    def exact(self,x):
        c1,c2=self.chemicals
        a,b=self.t_bounds
        if x<=0:temperature=b
        elif x>=1:temperature=a
        else:temperature=brentq(lambda t:x*c1.VaporPressure(t)+(1-x)*c2.VaporPressure(t)-self.p,a,b,xtol=1e-11)
        ps=[c.VaporPressure(temperature) for c in self.chemicals]
        y=x*ps[0]/self.p
        hg=[self.ideal_gas_h(n,c,temperature) for n,c in zip(self.names,self.chemicals)]
        latent=[c.EnthalpyVaporization(temperature) for c in self.chemicals]
        if any(v is None or not math.isfinite(v) or v<=0 for v in latent):
            raise ValueError("汽化焓缺失或不适用")
        hl=[h-dh for h,dh in zip(hg,latent)]
        return temperature,y,x*hl[0]+(1-x)*hl[1],y*hg[0]+(1-y)*hg[1]

    def at(self,x,exact=False):
        return np.asarray([self.exact(v) for v in x]) if exact else self.table(x)

@lru_cache(maxsize=8)
def thermodynamic_table(light,heavy,pressure):
    return BinaryThermo(light,heavy,pressure)

def column_mesh(p):
    started=time.monotonic()
    light=NAMES.get(p.light_component,p.light_component.lower())
    heavy=NAMES.get(p.heavy_component,p.heavy_component.lower())
    thermo=thermodynamic_table(light,heavy,p.pressure_Pa)
    n,feed=p.equilibrium_stages,p.feed_stage-1
    d,b=p.distillate_fraction,1-p.distillate_fraction
    reflux=p.reflux_ratio*d
    vtop=(p.reflux_ratio+1)*d
    hfeed=thermo.exact(p.feed_light_fraction)[2]
    f=np.zeros(n);f[feed]=1
    energy_scale=40000.0
    eps=1e-10

    def unpack(u,exact=False):
        x=u[:n]
        liquid=np.r_[u[n:2*n-1],b]
        vapor=np.r_[vtop,u[2*n-1:]]
        props=thermo.at(x,exact)
        t,y,hl,hv=props.T
        xd=y[0]
        hr=thermo.exact(float(xd))[2] if exact else float(thermo.table(xd)[2])
        lin=np.r_[reflux,liquid[:-1]]
        vin=np.r_[vapor[1:],0]
        xin=np.r_[xd,x[:-1]]
        yin=np.r_[y[1:],0]
        hlin=np.r_[hr,hl[:-1]]
        hvin=np.r_[hv[1:],0]
        total=lin+vin+f-liquid-vapor
        component=lin*xin+vin*yin+f*p.feed_light_fraction-liquid*x-vapor*y
        energy=lin*hlin+vin*hvin+f*hfeed-liquid*hl-vapor*hv
        return x,t,y,liquid,vapor,hl,hv,xd,hr,total,component,energy

    evaluations=0
    def residual(u,homotopy):
        nonlocal evaluations
        evaluations+=1
        if time.monotonic()-started>60:
            raise ValueError("塔器求解超过60秒；请调整级数、进料级、回流比或分割比例后重试")
        x,t,y,l,v,hl,hv,xd,hr,total,component,energy=unpack(u)
        cmo=v[1:]-v[:-1]
        thermal=(1-homotopy)*cmo+homotopy*energy[:-1]/energy_scale
        return np.r_[total[:-1],component,thermal]

    liquid0=np.full(n-1,reflux)
    liquid0[feed:]+=1
    guess_top=min(.98,p.feed_light_fraction+.3)
    guess_bottom=max(.02,p.feed_light_fraction-.3)
    u=np.r_[np.linspace(guess_top,guess_bottom,n),liquid0,np.full(n-1,vtop)]
    # Each stage couples only to its two neighbors; the condenser couples to stage 1.
    pattern=lil_matrix((3*n-2,3*n-2),dtype=int)
    for j in range(n):
        deps=set(range(max(0,j-1),min(n,j+2)))
        if j==0:deps.add(0)
        for k in (j-1,j):
            if 0<=k<n-1:deps.add(n+k)
        for k in (j,j+1):
            if 1<=k<n:deps.add(2*n-1+k-1)
        row_ids=[n-1+j]
        if j<n-1:row_ids.extend([j,2*n-1+j])
        for row in row_ids:
            for dep in deps:pattern[row,dep]=1
    lower=np.r_[np.full(n,eps),np.full(2*n-2,eps)]
    upper=np.r_[np.full(n,1-eps),np.full(2*n-2,1000.)]
    result=None
    for homotopy in (0.,.5,1.):
        result=least_squares(lambda v:residual(v,homotopy),u,bounds=(lower,upper),
            jac_sparsity=pattern.tocsr() if n>30 else None,x_scale="jac",ftol=1e-10,xtol=1e-10,gtol=1e-10,max_nfev=350,
            tr_options={"atol":1e-13,"btol":1e-13,"maxiter":2000,"regularize":False} if n>30 else {})
        u=result.x
        if np.max(np.abs(residual(u,homotopy)))>1e-6:
            raise ValueError(f"塔器方程未收敛：能量方程加载比例 {homotopy:g}，最大缩放残差 {np.max(np.abs(residual(u,homotopy))):.3g}，求解状态 {result.message}。请调整回流比、级数、进料位置或分割比例")
    x,t,y,l,v,hl,hv,xd,hr,total,component,energy=unpack(u,exact=True)
    flow=p.feed_kmol_h
    q_reboiler=-float(energy[-1])*flow/3600
    q_condenser=vtop*(float(hv[0])-hr)*flow/3600
    global_energy=q_reboiler-q_condenser+(hfeed-d*hr-b*float(hl[-1]))*flow/3600
    max_total=float(np.max(np.abs(total)))
    max_component=float(np.max(np.abs(component)))
    max_energy=float(np.max(np.abs(energy[:-1])))/energy_scale
    global_scale=max(1,abs(q_reboiler),abs(q_condenser))
    passed=max(max_total,max_component,max_energy,abs(global_energy)/global_scale)<1e-6 and q_reboiler>0 and q_condenser>0
    stages=[{"stage":j+1,"role":"partial_reboiler" if j==n-1 else "equilibrium_tray",
        "feed":j==feed,"temperature_K":float(t[j]),"liquid_light_fraction":float(x[j]),"vapor_light_fraction":float(y[j]),
        "liquid_kmol_h":float(l[j]*flow),"vapor_kmol_h":float(v[j]*flow),
        "total_residual_kmol_h":float(total[j]*flow),"light_residual_kmol_h":float(component[j]*flow),
        "energy_residual_kW":float(energy[j]*flow/3600) if j<n-1 else float((energy[j]*flow/3600)+q_reboiler)}
        for j in range(n)]
    return {"model":"binary ideal Raoult / equilibrium-stage MESH, total condenser + partial reboiler",
        "components":[light,heavy],"feed_state":"saturated liquid","feed_temperature_K":thermo.exact(p.feed_light_fraction)[0],
        "distillate_kmol_h":flow*d,"bottoms_kmol_h":flow*b,"distillate_light_fraction":float(xd),
        "bottoms_light_fraction":float(x[-1]),"condenser_temperature_K":thermo.exact(float(xd))[0],
        "reboiler_duty_kW":q_reboiler,"condenser_duty_removed_kW":q_condenser,"stages":stages,
        "solver":{"algorithm":"bounded least_squares with CMO→energy homotopy; sparse Jacobian for >30 stages","success":bool(result.success),
            "residual_evaluations":evaluations,"interpolation":"401-composition cubic table; final validation evaluates original correlations"},
        "validation":{"max_total_relative_residual":max_total,"max_light_component_relative_residual":max_component,
            "max_stage_energy_scaled_residual":max_energy,"global_energy_residual_kW":float(global_energy),"passed":bool(passed)},
        "thermodynamic_methods":thermo.methods,
        "sources":["https://thermo.readthedocs.io/","https://webbook.nist.gov/cgi/cbook.cgi?ID=C100414&Units=SI&Mask=1","https://webbook.nist.gov/cgi/cbook.cgi?ID=C100425&Units=SI&Mask=1"],
        "warnings":["理想液相Raoult和理想气体；气相显热及汽化焓构建理想混合焓。尚未用实测VLE或独立专业模拟软件验证，不能视为已校核的苯乙烯塔器设计。",
                    "级数包含部分再沸器，不含全凝器。进料为饱和液体；各级等压、无侧线和热损失。未计算塔径、压降、液泛、阻聚、停留时间或机械强度。",
                    "默认参数为苯/甲苯求解示例；乙苯/苯乙烯应由用户明确输入、确认操作压力与设计依据。"]}
