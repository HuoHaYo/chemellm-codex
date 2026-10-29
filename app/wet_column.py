"""Four-component MESH column with a VLL overhead condenser/decanter.

One liquid per hot stage is required and checked for instability afterwards.
Henry/UNIFAC models are predictions; convergence is not engineering validation.
"""
import math
import time
import logging
from functools import lru_cache
import numpy as np
from pydantic import Field, model_validator
from scipy.interpolate import CubicSpline
from scipy.optimize import least_squares, minimize, brentq
from scipy.special import softmax, logsumexp, expit
from scipy.sparse import lil_matrix
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import spsolve
from thermo import ChemicalConstantsPackage, UNIFAC
from thermo.unifac import DOUFIP2016, DOUFSG
from app.schemas import Inputs
from app.hydrogen import COMPONENTS, organic_henry_predictions
from app.reaction import gas_state, R

IAPWS_URL="https://iapws.org/technical-guidance/release/HenGuide.download"

def iapws_hydrogen_henry(t):
    """IAPWS G7-04 Eqs. (3)/(4), H2 row of Table 2; Hpx in Pa."""
    if not 273.15<=t<=636.09:raise ValueError("氢气-水亨利关联式超出IAPWS所拟合数据的温度区间")
    tr=t/647.096;tau=1-tr
    coefficients=[-7.85951783,1.84408259,-11.7866497,22.6807411,-15.9618719,1.80122502]
    powers=[1,1.5,3,3.5,4,7.5]
    psat=22.064e6*math.exp(sum(a*tau**b for a,b in zip(coefficients,powers))/tr)
    return psat*math.exp(-4.73284/tr+6.08954*tau**.355/tr+6.06066*tr**-.41*math.exp(tau))

class WetColumnInput(Inputs):
    feed_kmol_h:float=Field(default=100,gt=0,le=1e6)
    mole_fractions:list[float]=Field(default=[.013790854162,.393321120566,.592386308524,.000501716748],min_length=4,max_length=4,
        description="固定顺序：水、乙苯、苯乙烯、氢气；湿有机相进料")
    feed_temperature_K:float=Field(default=310,ge=303,le=430)
    feed_molar_enthalpy_J_mol:float|None=Field(default=None,ge=-4e5,le=4e5,
        description="可传入上游统一参考的实际进料焓；null时用当前模型液相焓")
    equilibrium_stages:int=Field(default=12,ge=4,le=60,
        description="已跑通6/12/50级示例；90秒求解时限，不保证全部工况收敛")
    feed_stage:int=Field(default=6,ge=1,le=59)
    pressure_Pa:float=Field(default=20000,ge=10000,le=80000)
    condenser_temperature_K:float=Field(default=310,ge=303,le=320)
    organic_distillate_fraction:float=Field(default=.4,gt=.01,lt=.95)
    reflux_ratio:float=Field(default=3,ge=.1,le=30)
    organic_henry_multiplier:float=Field(default=1,ge=.25,le=4)

    @model_validator(mode="after")
    def basis(self):
        if self.feed_stage>=self.equilibrium_stages:raise ValueError("进料级必须在末级部分再沸器上方")
        if any(not math.isfinite(x) or x<=0 for x in self.mole_fractions) or abs(sum(self.mole_fractions)-1)>1e-8:
            raise ValueError("水、乙苯、苯乙烯、氢气摩尔分数须为正且总和为1")
        if not 1e-4<=self.mole_fractions[0]<=.15 or not 1e-6<=self.mole_fractions[3]<=.01:
            raise ValueError("当前湿有机相塔仅支持水0.01–15 mol%、H2 0.0001–1 mol%的进料")
        return self

class WetThermo:
    def __init__(self,multiplier):
        self.constants,self.correlations=ChemicalConstantsPackage.from_IDs(COMPONENTS)
        self.multiplier=multiplier
        self.ge=UNIFAC.from_subgroups(T=350,xs=[1/3]*3,chemgroups=self.constants.UNIFAC_Dortmund_groups[:3],
            subgroups=DOUFSG,interaction_data=DOUFIP2016,version=1)
        # Jacobian color groups vary composition at unchanged temperatures.
        # Cache scalar-temperature properties per model, never the composition.
        self.original=lru_cache(maxsize=2048)(self.original)
        self.hydrogen_logs=lru_cache(maxsize=2048)(self.hydrogen_logs)
        grid=np.linspace(303,450,589)
        values=[self.original(t) for t in grid]
        self.table=CubicSpline(grid,np.asarray(values),axis=0)
        self.data=lru_cache(maxsize=2048)(self.data)

    def original(self,t):
        lnps=[]
        for vp in self.correlations.VaporPressures[:3]:
            if not vp.Tmin<=t<=vp.Tmax:raise ValueError("湿有机物物性超出原始蒸气压关联式区间")
            lnps.append(math.log(vp(t)))
        ln_h=self.hydrogen_logs(t)
        hgas=[gas_state(name,t)[0] for name in COMPONENTS]
        return np.r_[lnps,ln_h,hgas]

    def hydrogen_logs(self,t):
        return np.log([iapws_hydrogen_henry(t),*organic_henry_predictions(self.constants,t,self.multiplier)])

    def data(self,t,exact=False):
        if not 303<=t<=450:raise ValueError("湿有机物塔物性只在303–450 K计算")
        if exact:
            values=self.original(t)
            dps=[vp.T_dependent_property_derivative(t)/vp(t) for vp in self.correlations.VaporPressures[:3]]
            dt=.002
            # Differentiate only Henry constants. Calling the whole property
            # vector below 303 K would exceed styrene's Psat fit at the lower
            # advertised condenser boundary even though Henry is in range.
            dh=(self.hydrogen_logs(t+dt)-self.hydrogen_logs(t-dt))/(2*dt)
            derivative=np.r_[dps,dh]
        else:
            values=self.table(t);derivative=self.table(t,1)[:6]
        return values,derivative

    def phase(self,t,x,p,exact=False):
        values,derivative=self.data(t,exact)
        lnps=values[:3]-math.log(p);lnh=values[3:6]-math.log(p)
        q=x[3];s=1-q;w=x[:3]/s
        ge=self.ge.to_T_xs(T=t,xs=w.tolist())
        average=float(w@lnh)
        logf=np.r_[np.log(x[:3])+np.log(ge.gammas())+lnps+q/s*(lnh-average),math.log(q)+average]
        y=softmax(logf)
        dge_rt=(ge.dGE_dT()/t-ge.GE()/t**2)/R
        hl=float(x@values[6:]-R*t*t*(x[:3]@derivative[:3]+s*dge_rt+q*(w@derivative[3:6])))
        hv=float(y@values[6:])
        return logf,y,hl,hv

    def stability(self,t,p,reference):
        values,_=self.data(t,True)
        lnps=values[:3]-math.log(p);lnh=values[3:6]-math.log(p)
        def objective(w):
            ge=self.ge.to_T_xs(T=t,xs=w.tolist())
            b=float(w@(np.log(w)+lnps-reference[:3]))+ge.GE()/(R*t)
            c=float(w@lnh-reference[3])
            return -float(logsumexp([-b,-c]))
        trials=[]
        for a in range(11):
            for b in range(11-a):
                w=np.maximum(np.array([a,b,10-a-b])/10,1e-12);w/=w.sum()
                trials.append((objective(w),w))
        trials.sort(key=lambda v:v[0])
        minimum=trials[0][0]
        seeds=[v[1] for v in trials[:2]]+[np.array([.02,.49,.49]),np.array([.999998,1e-6,1e-6])]
        for w in seeds:
            opt=minimize(lambda v:objective(softmax(np.r_[v,0.])),np.log(w[:2]/w[2]),method="L-BFGS-B",
                bounds=[(-28,28)]*2,options={"maxiter":100,"ftol":1e-12})
            minimum=min(minimum,float(opt.fun))
        return minimum

@lru_cache(maxsize=3)
def wet_thermo(multiplier):return WetThermo(multiplier)

def composition(v):
    # Bound trial liquid H2 as well as accepted states. A four-way softmax
    # can round xH2 to exactly one and make the solvent Gibbs model singular.
    q=.01*expit(v[2])
    return np.r_[(1-q)*softmax(np.r_[v[:2],0.]),q]

def logits(x):
    x=np.asarray(x)
    return np.r_[np.log(x[:2]/x[2]),math.log(x[3]/(.01-x[3]))]

def gas_composition(v):return softmax(np.r_[v[:2],0.,v[2]])

def gas_logits(x):return np.log(np.array(x)[[0,1,3]]/x[2])

def jacobian_groups(pattern):
    matrix=pattern.tocsc()
    groups=[];occupied=[]
    columns=[matrix.indices[matrix.indptr[j]:matrix.indptr[j+1]] for j in range(matrix.shape[1])]
    for j,rows in sorted(enumerate(columns),key=lambda item:-len(item[1])):
        rowset=set(rows)
        for group,used in zip(groups,occupied):
            if not rowset.intersection(used):
                group.append(j);used.update(rowset);break
        else:groups.append([j]);occupied.append(rowset)
    return matrix,columns,groups


def damped_newton(fun,u,bounds,jacobian):
    """Square MESH equations: colored differences, sparse LU and line search."""
    low,high=bounds;matrix,columns,groups=jacobian
    u=u.copy();f=fun(u)
    for iteration in range(35):
        if iteration%10==0:logging.getLogger(__name__).debug("wet Newton iteration=%s max_error=%g",iteration,float(np.max(abs(f))))
        if np.max(abs(f))<1e-10:return u,True
        h=1e-6*np.maximum(1.,abs(u))
        h=np.where(u+h<high,h,-h)
        values=np.zeros(matrix.nnz)
        for group in groups:
            trial=u.copy();trial[group]+=h[group]
            df=fun(trial)-f
            for j in group:values[matrix.indptr[j]:matrix.indptr[j+1]]=df[columns[j]]/h[j]
        delta=spsolve(csc_matrix((values,matrix.indices,matrix.indptr),shape=matrix.shape),-f)
        if not np.isfinite(delta).all():return u,False
        alpha=1.
        positive=delta>0;negative=delta<0
        if positive.any():alpha=min(alpha,.99*float(np.min((high[positive]-u[positive])/delta[positive])))
        if negative.any():alpha=min(alpha,.99*float(np.min((low[negative]-u[negative])/delta[negative])))
        norm=float(f@f)
        for backtrack in range(18):
            trial=u+alpha*delta
            ft=fun(trial)
            if float(ft@ft)<norm*(1-1e-4*alpha):
                u,f=trial,ft;break
            alpha*=.5
        else:return u,False
    return u,False


def wet_column(p,initial=None):
    started=time.monotonic()
    thermo=wet_thermo(float(p.organic_henry_multiplier))
    n=p.equilibrium_stages;feed=p.feed_stage-1
    z=np.array(p.mole_fractions);d=p.organic_distillate_fraction;reflux=d*p.reflux_ratio
    hf=p.feed_molar_enthalpy_J_mol
    if hf is None:hf=thermo.phase(p.feed_temperature_K,z,p.pressure_Pa)[2]
    f=np.zeros(n);f[feed]=1
    balance_scale=np.maximum(z,np.array([.01,.1,.1,1e-4]))
    evaluations=0

    def unpack(u,exact=False):
        stage=u[:6*n].reshape(n,6)
        xs=np.array([composition(v[:3]) for v in stage]);ts=303+stage[:,3]*100
        ls=np.exp(stage[:,4]);vs=np.exp(stage[:,5])
        props=[thermo.phase(t,x,p.pressure_Pa,exact) for t,x in zip(ts,xs)]
        logf=np.array([v[0] for v in props]);ys=np.array([v[1] for v in props])
        hl=np.array([v[2] for v in props]);hv=np.array([v[3] for v in props])
        cond=u[6*n:];xo=composition(cond[:3]);xa=composition(cond[3:6]);yg=gas_composition(cond[6:9])
        aq,vent=np.exp(cond[9:11]);tc=p.condenser_temperature_K
        of,_,ho,_=thermo.phase(tc,xo,p.pressure_Pa,exact)
        af,_,ha,_=thermo.phase(tc,xa,p.pressure_Pa,exact)
        hg=float(yg@thermo.data(tc,exact)[0][6:])
        lin=np.r_[reflux,ls[:-1]];vin=np.r_[vs[1:],0.]
        xin=np.vstack([xo,xs[:-1]]);yin=np.vstack([ys[1:],np.zeros(4)])
        hlin=np.r_[ho,hl[:-1]];hvin=np.r_[hv[1:],0.]
        component_in=lin[:,None]*xin+vin[:,None]*yin+f[:,None]*z
        component_out=ls[:,None]*xs+vs[:,None]*ys
        balances=component_in-component_out
        log_balances=np.log(component_in)-np.log(component_out)
        energy=lin*hlin+vin*hvin+f*hf-ls*hl-vs*hv
        condenser=vs[0]*ys[0]-(d+reflux)*xo-aq*xa-vent*yg
        equilibrium=np.r_[of-np.log(yg),af-np.log(yg)]
        return xs,ts,ls,vs,ys,logf,hl,hv,xo,xa,yg,aq,vent,ho,ha,hg,balances,energy,condenser,equilibrium,log_balances

    def residual(u,homotopy,exact=False):
        nonlocal evaluations
        evaluations+=1
        if time.monotonic()-started>90:raise ValueError("四元湿有机相塔求解超过90秒；请调整级数、进料级、回流或馏出比例")
        xs,ts,ls,vs,ys,logf,hl,hv,xo,xa,yg,aq,vent,ho,ha,hg,m,e,mc,eq,lm=unpack(u,exact)
        thermal=homotopy*e[:-1]/60000+(1-homotopy)*(vs[1:]-vs[:-1])
        blocks=[np.r_[lm[j],logsumexp(logf[j]),thermal[j]] if j<n-1 else np.r_[lm[j],logsumexp(logf[j])] for j in range(n)]
        return np.r_[np.concatenate(blocks),mc/balance_scale,eq]

    if initial is not None:
        if len(initial["stages"])!=n or initial["validation"]["passed"] is not True:
            raise ValueError("Invalid internal column initialization")
        rows=[np.r_[logits(s["liquid_mole_fractions"]),(s["temperature_K"]-303)/100,
            math.log(s["liquid_kmol_h"]/initial["feed_kmol_h"]),math.log(s["vapor_kmol_h"]/initial["feed_kmol_h"])] for s in initial["stages"]]
        products={s["outlet"]:s for s in initial["outlets"]}
        u=np.r_[np.concatenate(rows),logits(products["organic_distillate"]["mole_fractions"]),
            logits(products["aqueous_distillate"]["mole_fractions"]),gas_logits(products["vent"]["mole_fractions"]),
            math.log(products["aqueous_distillate"]["flow_kmol_h"]/initial["feed_kmol_h"]),
            math.log(products["vent"]["flow_kmol_h"]/initial["feed_kmol_h"])]
    else:
        rows=[]
        for j in range(n):
            w=.001*math.exp(-j*.8)
            eb=float(np.linspace(.8,.08,n)[j]);h2=3e-8*math.exp(-max(0,j-feed)*5)
            x=[w,eb,1-w-eb-h2,h2]
            rows.append(np.r_[logits(x),(345+20*j/(n-1)-303)/100,
                math.log(reflux+(1 if j>=feed else 0)),math.log((p.reflux_ratio+1)*d+.01)])
        rows[-1][4]=math.log(1-d-.008)
        xo=np.array([.014,.78,.20593,.00007]);xo/=xo.sum()
        xa=np.array([.9998,.000035,.000163,2e-6]);xa/=xa.sum()
        of=thermo.phase(p.condenser_temperature_K,xo,p.pressure_Pa)[0]
        yg=np.exp(of);yg[3]=max(.01,1-yg[:3].sum());yg/=yg.sum()
        aq=max(.0001,z[0]-d*.014);vent=max(1e-5,z[3]/yg[3])
        if n>=20:
            # An ideal dry-binary column supplies only an initial hydrocarbon
            # profile. Restore both trace species by their full stage balances;
            # the final solve always uses the four-component equations above.
            from app.column import ColumnInput,column_mesh
            binary=column_mesh(ColumnInput(light_component="ethylbenzene",heavy_component="styrene",
                feed_light_fraction=float(z[1]/sum(z[1:3])),equilibrium_stages=n,feed_stage=p.feed_stage,
                pressure_Pa=p.pressure_Pa,reflux_ratio=p.reflux_ratio,distillate_fraction=d))
            ratio=binary["distillate_light_fraction"]
            def condenser_state(v):
                q=.01*expit(v[1]);w=.3*expit(v[0])
                organic=np.r_[(1-q)*np.array([w,(1-w)*ratio,(1-w)*(1-ratio)]),q]
                return organic,composition(v[2:5]),gas_composition(v[5:8])
            def condenser_residual(v):
                org,aqueous,gas=condenser_state(v)
                return np.r_[thermo.phase(p.condenser_temperature_K,org,p.pressure_Pa)[0]-np.log(gas),
                    thermo.phase(p.condenser_temperature_K,aqueous,p.pressure_Pa)[0]-np.log(gas)]
            cond=least_squares(condenser_residual,np.r_[math.log(.013/(.3-.013)),math.log(.00007/(.01-.00007)),logits(xa),gas_logits(yg)],
                bounds=(-32.,32.),ftol=1e-12,xtol=1e-12,gtol=1e-12,max_nfev=100)
            if np.max(abs(condenser_residual(cond.x)))>1e-7:raise ValueError("湿塔初始化未得到塔顶VLL平衡")
            xo,xa,yg=condenser_state(cond.x)
            aq,vent=np.linalg.solve(np.array([[xa[0],yg[0]],[xa[3],yg[3]]]),np.array([z[0]-d*xo[0],z[3]-d*xo[3]]))
            if min(aq,vent)<=0:raise ValueError("指定馏出比例不支持当前三相塔顶初始化，请调整工况")
            ls=np.array([reflux+(j>=feed) for j in range(n)],dtype=float);ls[-1]=1-d-aq-vent
            vs=np.full(n,d+reflux+aq+vent)
            ts=np.array([s["temperature_K"] for s in binary["stages"]])
            hydrocarbon=np.array([s["liquid_light_fraction"] for s in binary["stages"]])
            xs=np.array([[1e-10,(1-2e-10)*x,(1-2e-10)*(1-x),1e-10] for x in hydrocarbon])
            for iteration in range(4):
                k=np.array([np.exp(thermo.phase(t,x,p.pressure_Pa)[0])/x for t,x in zip(ts,xs)])
                traces=[]
                for component in (0,3):
                    matrix=np.diag(ls+vs*k[:,component])
                    for j in range(n-1):matrix[j,j+1]=-vs[j+1]*k[j+1,component];matrix[j+1,j]=-ls[j]
                    rhs=f*z[component];rhs[0]+=reflux*xo[component]
                    trace=np.linalg.solve(matrix,rhs)
                    if np.min(trace)<=0:raise ValueError("湿塔初始化的稀组分衡算未得到正流量")
                    traces.append(trace)
                water,h2=traces
                xs=np.column_stack([water,(1-water-h2)*hydrocarbon,(1-water-h2)*(1-hydrocarbon),h2])
                ts=np.array([brentq(lambda t:logsumexp(thermo.phase(t,x,p.pressure_Pa)[0]),303.003,449.997) for x in xs])
            rows=[np.r_[logits(x),(t-303)/100,math.log(l),math.log(v)] for x,t,l,v in zip(xs,ts,ls,vs)]
        u=np.r_[np.concatenate(rows),logits(xo),logits(xa),gas_logits(yg),math.log(aq),math.log(vent)]
    low=np.tile([-700.,-32.,-700.,.00003,-20.,-20.],n)
    high=np.tile([32.,32.,30.,1.46997,math.log(1000),math.log(1000)],n)
    low=np.r_[low,[-32.,-32.,-120.]*2,[-32.]*3,[-25.]*2]
    high=np.r_[high,[32.,32.,30.]*2,[32.]*3,[math.log(2.)]*2]
    # Long-column initial trace profiles can lie below the numerical bounds.
    u=np.clip(u,low+1e-8,high-1e-8)
    pattern=lil_matrix((6*n+11,6*n+11),dtype=int)
    for j in range(n):
        deps=list(range(max(0,j-1)*6,min(n,j+2)*6))
        if j==0:deps+=list(range(6*n,6*n+3))
        for row in range(6*j,6*j+(6 if j<n-1 else 5)):
            for dep in deps:pattern[row,dep]=1
    for row in range(6*n-1,6*n+11):
        for dep in [*range(6),*range(6*n,6*n+11)]:pattern[row,dep]=1
    jacobian=jacobian_groups(pattern)
    for homotopy,exact in ([(1.,False),(1.,True)] if initial is not None else [(0.,False),(.5,False),(1.,False),(1.,True)]):
        fun=lambda v:residual(v,homotopy,exact)
        u,solved=damped_newton(fun,u,(low,high),jacobian)
        message="damped sparse Newton converged"
        if not solved:
            result=least_squares(fun,u,bounds=(low,high),jac_sparsity=pattern.tocsr(),
                x_scale="jac",ftol=1e-12,xtol=1e-12,gtol=1e-13,max_nfev=350,
                tr_options={"atol":1e-12,"btol":1e-12,"maxiter":2000,"regularize":False})
            u=result.x;message=result.message
        error=float(np.max(np.abs(residual(u,homotopy,exact))))
        logging.getLogger(__name__).info("wet column continuation=%s exact=%s Newton=%s max_error=%g elapsed=%.2fs",homotopy,exact,solved,error,time.monotonic()-started)
        if error>2e-6:
            raise ValueError(f"四元塔未收敛：能量加载{homotopy:g}，原关联式={exact}，最大残差{error:.3g}，方程{int(np.argmax(np.abs(residual(u,homotopy,exact))))}；{message}")
    values=unpack(u,True)
    xs,ts,ls,vs,ys,logf,hl,hv,xo,xa,yg,aq,vent,ho,ha,hg,m,e,mc,eq,lm=values
    qr=-float(e[-1]);qc=float(vs[0]*hv[0]-(d+reflux)*ho-aq*ha-vent*hg)
    product=d*xo+aq*xa+vent*yg+ls[-1]*xs[-1]
    closure=float(np.max(np.abs(product-z)))
    energy=hf+qr-qc-d*ho-aq*ha-vent*hg-ls[-1]*hl[-1]
    stability=[thermo.stability(t,p.pressure_Pa,lf) for t,lf in zip(ts,logf)]
    cond_stability=thermo.stability(p.condenser_temperature_K,p.pressure_Pa,np.log(yg))
    if min(stability+[cond_stability])<-1e-6:
        raise ValueError(f"四元塔发现单液相不稳定（最小TPD={min(stability+[cond_stability]):.3g}）；须采用支持塔板液液分相的模型")
    if min(aq,vent)<1e-8 or xa[0]<.9 or xo[0]>.3 or np.max(xs[:,3])>.01:
        raise ValueError("四元塔解不满足当前VLL塔顶及稀溶H2模型边界，不能按此流程结构使用")
    flow=p.feed_kmol_h
    outputs=[]
    for label,fraction,x,h in [("organic_distillate",d,xo,ho),("aqueous_distillate",aq,xa,ha),("vent",vent,yg,hg),("bottoms",ls[-1],xs[-1],hl[-1])]:
        outputs.append({"outlet":label,"flow_kmol_h":float(flow*fraction),"mole_fractions":x.tolist(),
            "temperature_K":float(ts[-1]) if label=="bottoms" else p.condenser_temperature_K,
            "pressure_Pa":p.pressure_Pa,"molar_enthalpy_J_mol":h,
            "component_flows_kmol_h":dict(zip(COMPONENTS,(flow*fraction*x).tolist())),"enthalpy_flow_kW":float(flow*fraction*h/3600)})
    passed=closure<2e-6 and error<2e-6 and abs(energy)/60000<2e-6 and qr>0 and qc>0
    return {"model":"four-component UNIFAC/Henry MESH, single-liquid stages, VLL partial condenser/decanter",
        "components":COMPONENTS,"feed_kmol_h":flow,"feed_mole_fractions":z.tolist(),"feed_temperature_K":p.feed_temperature_K,
        "feed_molar_enthalpy_J_mol":hf,"pressure_Pa":p.pressure_Pa,"condenser_temperature_K":p.condenser_temperature_K,
        "reflux_ratio":p.reflux_ratio,"organic_distillate_fraction":d,"reflux_flow_kmol_h":reflux*flow,
        "reboiler_duty_kW":qr*flow/3600,"condenser_duty_removed_kW":qc*flow/3600,"outlets":outputs,
        "stages":[{"stage":j+1,"role":"partial_reboiler" if j==n-1 else "equilibrium_tray","feed":j==feed,
            "temperature_K":float(ts[j]),"liquid_mole_fractions":xs[j].tolist(),"vapor_mole_fractions":ys[j].tolist(),
            "liquid_kmol_h":float(ls[j]*flow),"vapor_kmol_h":float(vs[j]*flow),
            "liquid_molar_enthalpy_J_mol":float(hl[j]),"vapor_molar_enthalpy_J_mol":float(hv[j]),
            "component_residuals_kmol_h":(m[j]*flow).tolist(),"energy_residual_kW":float((e[j]+(qr if j==n-1 else 0))*flow/3600),
            "minimum_searched_liquid_TPD":stability[j]} for j in range(n)],
        "validation":{"max_scaled_equation_residual":error,"max_global_component_fraction_residual":closure,
            "max_stage_log_component_flow_residual":float(np.max(abs(lm))),
            "global_energy_residual_kW":energy*flow/3600,"minimum_searched_stage_liquid_TPD":min(stability),
            "minimum_searched_condenser_liquid_TPD":cond_stability,"passed":bool(passed)},
        "solver":{"method":"damped sparse Newton, bounded least_squares fallback, CMO to energy continuation, original-property refinement",
            "residual_evaluations":evaluations,"elapsed_seconds":time.monotonic()-started},
        "sources":[{"url":IAPWS_URL,"reference":"IAPWS G7-04 Eq.(3)/(4), H2 Table2; checked against Table6"},
            {"url":"https://thermo.readthedocs.io/thermo.unifac.html","reference":"Dortmund UNIFAC version1/DOUFIP2016"},
            {"url":"https://thermo.readthedocs.io/thermo.eos_mix.html","reference":"organic H2 Henry constants are PR/kij=0 infinite-dilution predictions, not measurements"}],
        "warnings":["四元湿有机相研究塔；塔顶有机液分为馏出与回流，水相单独排出，H2尾气保留。未将水和H2从进料中删除。",
            "水中H2采用IAPWS宽温区式，有机H2仍是PR预测；UNIFAC及整个塔尚无该体系实验或独立模拟软件基准。",
            "每级假设一个液相；最终做66点溶剂网格/4起点液相TPD搜索，发现液液不稳定即拒绝。有限搜索不是全局稳定性证明。",
            "303–450 K、10–80 kPa为实现边界；忽略气相非理想、Poynting、塔压降、副反应和聚合。级数含再沸器、不含塔顶冷凝分相器。",
            "逐级组分衡算采用流入/流出对数比，以保留微量组分方程；极微量尾数仍不代表可测精度或可靠脱除率。",
            "默认进料来自前端示例的湿有机相组成；默认液相焓由当前IAPWS/UNIFAC模型计算。连接上游时应显式传入共同参考下的实际焓。未完成塔径、内件、阻聚、真空系统或机械设计。"]}
