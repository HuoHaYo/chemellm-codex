"""Explicit engineering screening equations with bounded assumptions."""
import math
from typing import Literal
from pydantic import Field, model_validator
from scipy.optimize import brentq
from fluids import friction_factor
from app.schemas import Inputs

class DistillationInput(Inputs):
    feed_kmol_h: float = Field(default=400,gt=0,le=1e7)
    feed_light_fraction: float = Field(default=0.4,gt=0,lt=1)
    distillate_light_fraction: float = Field(default=0.99,gt=0,lt=1)
    bottoms_light_fraction: float = Field(default=0.005,gt=0,lt=1)
    relative_volatility: float = Field(default=1.35,gt=1,le=50)
    feed_liquid_fraction: float = Field(default=1,ge=0,le=1)
    reflux_multiple: float = Field(default=1.3,gt=1,le=10)
    overall_tray_efficiency: float = Field(default=0.65,gt=0,le=1)
    @model_validator(mode="after")
    def compositions(self):
        if not self.bottoms_light_fraction<self.feed_light_fraction<self.distillate_light_fraction:
            raise ValueError("必须满足 xB < zF < xD；轻组分定义须在全塔保持一致")
        return self

def distillation_shortcut(p):
    a,z,xd,xb=p.relative_volatility,p.feed_light_fraction,p.distillate_light_fraction,p.bottoms_light_fraction
    d=p.feed_kmol_h*(z-xb)/(xd-xb)
    b=p.feed_kmol_h-d
    nmin=math.log(xd/(1-xd)*(1-xb)/xb)/math.log(a)
    eps=(a-1)*1e-9
    theta=brentq(lambda t:a*z/(a-t)+(1-z)/(1-t)-(1-p.feed_liquid_fraction),1+eps,a-eps)
    rmin=a*xd/(a-theta)+(1-xd)/(1-theta)-1
    if rmin<=0:
        raise ValueError("此输入对应非正最小回流比，超出当前捷算法适用范围")
    reflux=p.reflux_multiple*rmin
    xx=(reflux-rmin)/(reflux+1)
    yy=1-math.exp((1+54.4*xx)/(11+117.2*xx)*(xx-1)/math.sqrt(xx))
    stages=(nmin+yy)/(1-yy)
    # Total condenser excluded, partial equilibrium reboiler included in N.
    trays=max(1,math.ceil((stages-1)/p.overall_tray_efficiency))
    residual=abs(p.feed_kmol_h*z-d*xd-b*xb)/p.feed_kmol_h
    return {"model":"binary constant-alpha Fenske–Underwood–Gilliland / Eduljee",
            "distillate_kmol_h":d,"bottoms_kmol_h":b,"minimum_stages":nmin,"underwood_root":theta,
            "minimum_reflux_ratio":rmin,"reflux_ratio":reflux,"theoretical_stages_including_reboiler":stages,
            "estimated_actual_trays_excluding_reboiler":trays,
            "overhead_vapor_kmol_h":(reflux+1)*d,"rectifying_liquid_kmol_h":reflux*d,
            "validation":{"total_balance_residual_kmol_h":abs(p.feed_kmol_h-d-b),"light_component_relative_residual":residual,"passed":residual<1e-9},
            "sources":["Fenske (1932), Ind. Eng. Chem. 24, 482–485, https://doi.org/10.1021/ie50269a003","Underwood (1948), Chem. Eng. Prog. 44, 603–614","Eduljee (1975), Hydrocarbon Processing 54(9), 120–122"],
            "warnings":["二元、恒定相对挥发度与恒摩尔流近似；默认 α、回流倍数和板效率均为示例，不是乙苯/苯乙烯实测设计依据。",
                        "只支持 q∈[0,1]、全凝器和部分再沸器，不含共沸、侧线、多组分、热负荷及水力学。理论级数包括再沸器、不包括全凝器。",
                        "苯乙烯热敏且会聚合，需独立核定真空操作、停留时间、阻聚措施、严密VLE和塔内件；不能直接据此完成塔器设计。"]}

class PipeInput(Inputs):
    volume_flow_m3_h:float=Field(default=100,gt=0,le=1e6)
    inner_diameter_m:float=Field(default=0.2,gt=0,le=5)
    length_m:float=Field(default=100,ge=0,le=1e6)
    roughness_m:float=Field(default=0.000045,ge=0,le=0.05)
    density_kg_m3:float=Field(default=900,gt=0,le=30000)
    viscosity_Pa_s:float=Field(default=0.001,gt=0,le=100)
    minor_loss_K:float=Field(default=5,ge=0,le=1e5)
    elevation_rise_m:float=Field(default=10,ge=-10000,le=10000)
    terminal_pressure_rise_Pa:float=Field(default=0,ge=-1e9,le=1e9)
    pump_efficiency:float=Field(default=0.7,gt=0,le=1)
    @model_validator(mode="after")
    def geometry(self):
        if self.roughness_m/self.inner_diameter_m>0.05:
            raise ValueError("相对粗糙度超过0.05，当前摩擦因子模型不适用")
        return self

def pipe_pressure_drop(p):
    q=p.volume_flow_m3_h/3600
    area=math.pi*p.inner_diameter_m**2/4
    velocity=q/area
    reynolds=p.density_kg_m3*velocity*p.inner_diameter_m/p.viscosity_Pa_s
    if 2040<=reynolds<4000:
        raise ValueError("雷诺数处于2040–4000过渡区，不能可靠给出本工具的单一摩擦因子")
    factor=friction_factor(Re=reynolds,eD=p.roughness_m/p.inner_diameter_m,Darcy=True)
    dynamic=p.density_kg_m3*velocity**2/2
    friction=factor*p.length_m/p.inner_diameter_m*dynamic
    minor=p.minor_loss_K*dynamic
    static=p.density_kg_m3*9.80665*p.elevation_rise_m
    required=friction+minor+static+p.terminal_pressure_rise_Pa
    return {"model":"single-phase incompressible Darcy–Weisbach / Clamond","velocity_m_s":velocity,"Reynolds":reynolds,
            "regime":"laminar" if reynolds<2040 else "turbulent","Darcy_friction_factor":factor,
            "friction_loss_Pa":friction,"minor_loss_Pa":minor,"static_pressure_Pa":static,"required_pump_pressure_Pa":required,
            "required_pump_head_m":max(0,required)/(p.density_kg_m3*9.80665),
            "shaft_power_kW":max(0,required)*q/p.pump_efficiency/1000,
            "validation":{"pressure_sum_residual_Pa":abs(required-(friction+minor+static+p.terminal_pressure_rise_Pa)),"passed":True},
            "sources":["https://fluids.readthedocs.io/fluids.friction.html"],
            "warnings":["仅定密度、定黏度、单相不可压缩流；不适用于气体大压降、两相或非牛顿流。",
                        "负所需压差表示可由静压/端压提供驱动；轴功率按无泵需求置零，不计算能量回收。未校核NPSH、气蚀、允许流速、水锤或厂家泵曲线。"]}

class ThermalStream(Inputs):
    name:str=Field(min_length=1,max_length=80)
    kind:Literal["hot","cold"]
    supply_C:float=Field(gt=-273.15,lt=2000)
    target_C:float=Field(gt=-273.15,lt=2000)
    heat_capacity_flow_kW_K:float=Field(gt=0,le=1e8)
    @model_validator(mode="after")
    def direction(self):
        if self.kind=="hot" and self.supply_C<=self.target_C:
            raise ValueError("热流入口必须高于出口")
        if self.kind=="cold" and self.supply_C>=self.target_C:
            raise ValueError("冷流出口必须高于入口")
        return self

class PinchInput(Inputs):
    delta_T_min_K:float=Field(default=20,gt=0,le=300)
    streams:list[ThermalStream]=Field(min_length=2,max_length=100)
    @model_validator(mode="after")
    def streams_valid(self):
        if len({s.name for s in self.streams})!=len(self.streams):
            raise ValueError("物流名称必须唯一")
        if {s.kind for s in self.streams}!={"hot","cold"}:
            raise ValueError("夹点分析至少需要一条热流和一条冷流")
        return self

def pinch_analysis(p):
    shifted=[]
    hot=cold=0.0
    for s in p.streams:
        shift=-p.delta_T_min_K/2 if s.kind=="hot" else p.delta_T_min_K/2
        a,b=s.supply_C+shift,s.target_C+shift
        shifted.append((min(a,b),max(a,b),s.heat_capacity_flow_kW_K,s.kind))
        duty=abs(s.supply_C-s.target_C)*s.heat_capacity_flow_kW_K
        if s.kind=="hot":hot+=duty
        else:cold+=duty
    levels=sorted({v for item in shifted for v in item[:2]},reverse=True)
    intervals=[]
    cascade=[0.0]
    for top,bottom in zip(levels,levels[1:]):
        mid=(top+bottom)/2
        hc=sum(cp for lo,hi,cp,kind in shifted if lo<mid<hi and kind=="hot")
        cc=sum(cp for lo,hi,cp,kind in shifted if lo<mid<hi and kind=="cold")
        net=(hc-cc)*(top-bottom)
        cascade.append(cascade[-1]+net)
        intervals.append({"shifted_top_C":top,"shifted_bottom_C":bottom,"hot_CP_kW_K":hc,"cold_CP_kW_K":cc,"net_heat_kW":net})
    qh=max(0,-min(cascade))
    adjusted=[v+qh for v in cascade]
    qc=max(0,adjusted[-1])
    tol=1e-9*max(1,hot,cold)
    pinch=[{"shifted_C":t,"hot_C":t+p.delta_T_min_K/2,"cold_C":t-p.delta_T_min_K/2} for t,residual in zip(levels,adjusted) if abs(residual)<=tol]
    for i,item in enumerate(intervals):
        item["cascade_in_kW"]=adjusted[i]
        item["cascade_out_kW"]=adjusted[i+1]
    residual=abs(qh+hot-cold-qc)
    return {"model":"sensible-heat problem-table cascade","minimum_hot_utility_kW":qh,"minimum_cold_utility_kW":qc,
            "total_hot_stream_duty_kW":hot,"total_cold_stream_duty_kW":cold,"maximum_heat_recovery_kW":min(hot-qc,cold-qh),
            "pinch_temperatures":pinch,"intervals":intervals,
            "validation":{"energy_balance_residual_kW":residual,"minimum_cascade_kW":min(adjusted),"passed":residual<=tol and min(adjusted)>=-tol},
            "sources":["Linnhoff and Hindmarsh (1983), The pinch design method for heat exchanger networks, Chemical Engineering Science 38, 745–763, https://doi.org/10.1016/0009-2509(83)80185-7"],
            "warnings":["仅恒定热容流率的显热物流，不含潜热、温变热容、压降或非同时工况。",
                        "公用工程数值是热力学目标；尚未设计换热网络，不保证有限面积、操作约束、改造可行性与经济性。"]}
