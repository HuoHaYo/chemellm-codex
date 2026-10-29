import math
import threading
from typing import Literal
import pint
from pydantic import Field, model_validator
from app.schemas import Inputs
from thermo import Chemical
from app.db import uid, now, execute
import json

class PropertiesInput(Inputs):
    compound: str = Field(min_length=1, max_length=160)
    temperature_K: float = Field(default=298.15, gt=0, le=2500)
    pressure_Pa: float = Field(default=101325, gt=0, le=1e9)

class SteamInput(Inputs):
    pressure_Pa: float = Field(default=1800000, gt=611.7, lt=100e6)
    temperature_K: float | None = Field(default=None, gt=273.15, lt=2000)
    quality: float | None = Field(default=1, ge=0, le=1)
    @model_validator(mode="after")
    def state(self):
        if (self.temperature_K is None) == (self.quality is None):
            raise ValueError("必须且只能给温度或干度；指定温度时把 quality 设为 null")
        return self

class UnitInput(Inputs):
    value: float
    from_unit: str = Field(max_length=60)
    to_unit: str = Field(max_length=60)

class BalanceInput(Inputs):
    product_t_per_year: float = Field(default=200000, gt=0, le=1e8)
    operating_hours: float = Field(default=8000, gt=0, le=8784)
    conversion: float = Field(default=0.60, gt=0, le=1)
    ethylbenzene_recovery: float = Field(default=0.99, ge=0, le=1)
    recycle_purge: float = Field(default=0.01, ge=0, le=1)

class HeatInput(Inputs):
    duty_kW: float = Field(default=5000, gt=0, le=1e9)
    hot_in_C: float = Field(default=180,gt=-273.15)
    hot_out_C: float = Field(default=120,gt=-273.15)
    cold_in_C: float = Field(default=30,gt=-273.15)
    cold_out_C: float = Field(default=90,gt=-273.15)
    U_W_m2_K: float = Field(default=500, gt=0, le=50000)
    correction_factor: float = Field(default=0.9, gt=0, le=1)

class FlashInput(Inputs):
    compounds: list[str] = Field(default=["benzene", "toluene"], min_length=2, max_length=10)
    mole_fractions: list[float] = Field(default=[0.5, 0.5], min_length=2, max_length=10)
    temperature_K: float = Field(default=368, gt=100, lt=2000)
    pressure_Pa: float = Field(default=101325, gt=100, le=1e8)
    @model_validator(mode="after")
    def fractions(self):
        if len(self.compounds) != len(self.mole_fractions) or any(v <= 0 or not math.isfinite(v) for v in self.mole_fractions) or abs(sum(self.mole_fractions)-1)>1e-8:
            raise ValueError("组分数量必须匹配，摩尔分数须为正且总和为 1")
        return self

class EconomicsInput(Inputs):
    capital_CNY: float = Field(default=1e9, gt=0)
    annual_revenue_CNY: float = Field(default=1.8e9, ge=0)
    annual_cost_CNY: float = Field(default=1.6e9, ge=0)
    years: int = Field(default=15, ge=1, le=50)
    discount_rate: float = Field(default=0.10, ge=0, le=1)

ALIASES = {"苯乙烯": "styrene", "乙苯": "ethylbenzene", "苯": "benzene", "甲苯": "toluene", "水": "water", "氢气": "hydrogen", "乙烯": "ethylene", "甲醇": "methanol"}
ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)

def properties(p):
    c = Chemical(ALIASES.get(p.compound,p.compound), T=p.temperature_K, P=p.pressure_Pa)
    def value(v, unit):
        return {"value": float(v) if v is not None and math.isfinite(v) else None, "unit": unit}
    correlations = {}
    invalid=[]
    for attr in ("VaporPressure", "HeatCapacityLiquid", "VolumeLiquid"):
        obj = getattr(c,attr,None)
        lower,upper=getattr(obj,"Tmin",None),getattr(obj,"Tmax",None)
        within=bool(obj and getattr(obj,"method",None) and lower is not None and upper is not None and lower<=p.temperature_K<=upper)
        correlations[attr] = {"method":getattr(obj,"method",None), "Tmin_K":lower, "Tmax_K":upper,"within_temperature_range":within}
        if not within:invalid.append(attr)
    liquid_valid=c.phase=="l" and (c.Tc is None or p.temperature_K<c.Tc)
    vapor_valid=correlations["VaporPressure"]["within_temperature_range"] and (c.Tc is None or p.temperature_K<c.Tc)
    result = {"compound": c.name, "CAS": c.CAS, "formula": c.formula, "temperature_K": p.temperature_K, "pressure_Pa": p.pressure_Pa,
        "estimated_phase":c.phase,
        "properties": {"MW":value(c.MW,"g/mol"), "normal_boiling_point":value(c.Tb,"K"), "critical_temperature":value(c.Tc,"K"), "critical_pressure":value(c.Pc,"Pa"), "liquid_density":value(c.rhol if liquid_valid and "VolumeLiquid" not in invalid else None,"kg/m³"), "liquid_Cp":value(c.Cpl if liquid_valid and "HeatCapacityLiquid" not in invalid else None,"J/(kg·K)"), "vapor_pressure":value(c.Psat if vapor_valid else None,"Pa")},
        "correlations":correlations, "sources":["https://thermo.readthedocs.io/", "https://chemicals.readthedocs.io/"],
        "warnings":["关联式计算值，不是本项目实验测量；缺失值保留 null，工程使用须复核原始数据、相态与适用温度。",
                    "非液相条件不返回液相物性；超出关联式温度范围或达到临界温度不返回外推的饱和蒸气压。压力适用性尚需核对。"]}
    if invalid:result["warnings"].append("温度范围未满足或无法确认："+", ".join(invalid))
    execute("INSERT INTO property_cache VALUES(?,?,?,?)", (uid(),p.compound,json.dumps(result,ensure_ascii=False),now()))
    return result

def steam(p):
    from CoolProp.CoolProp import PropsSI
    key, val = ("T",p.temperature_K) if p.temperature_K is not None else ("Q",p.quality)
    result = {name: PropsSI(code, "P",p.pressure_Pa,key,val,"Water") for name,code in [("temperature_K","T"),("enthalpy_J_kg","H"),("entropy_J_kg_K","S"),("density_kg_m3","D")]}
    return {**result, "sources":["https://coolprop.org/"], "warnings":["压力为绝对压力；焓和熵依赖参考态。"]}

def convert_units(p):
    q = ureg.Quantity(p.value, p.from_unit).to(p.to_unit)
    return {"value": q.magnitude, "unit":str(q.units), "dimensionality":str(q.dimensionality), "validation":{"compatible_dimensions":True}}

def material_balance(p):
    mw = {name:Chemical(name).MW for name in ("styrene","ethylbenzene","hydrogen")}
    sm = p.product_t_per_year*1000/p.operating_hours
    extent = sm/mw["styrene"]
    feed = extent/p.conversion
    unreacted = feed-extent
    recycle = unreacted*p.ethylbenzene_recovery*(1-p.recycle_purge)
    eb_out = unreacted-recycle
    fresh = feed-recycle
    mass_in = fresh*mw["ethylbenzene"]
    mass_out = sm+extent*mw["hydrogen"]+eb_out*mw["ethylbenzene"]
    carbon_error = abs(8*fresh-8*(extent+eb_out))
    hydrogen_error = abs(10*fresh-(8*extent+2*extent+10*eb_out))
    residual = abs(mass_in-mass_out)/mass_in
    return {"basis": "C8H10 → C8H8 + H2，稳态、单反应、完全选择性", "streams": {"styrene_kg_h":sm, "fresh_ethylbenzene_kg_h":mass_in, "reactor_ethylbenzene_kg_h":feed*mw["ethylbenzene"] , "recycle_ethylbenzene_kg_h":recycle*mw["ethylbenzene"], "ethylbenzene_loss_kg_h":eb_out*mw["ethylbenzene"] , "hydrogen_kg_h":extent*mw["hydrogen"]},
        "validation":{"mass_relative_residual":residual, "carbon_kmol_atoms_h_residual":carbon_error, "hydrogen_kmol_atoms_h_residual":hydrogen_error, "passed":residual<1e-8 and carbon_error<1e-7 and hydrogen_error<1e-7},
        "warnings":["初步计量模型，不包含蒸汽、杂质、副反应、平衡、动力学、分离能耗与完整热量衡算。默认产能和操作参数均为示例假设。"], "sources":["https://thermo.readthedocs.io/"]}

def heat_exchanger(p):
    if p.hot_in_C<=p.hot_out_C or p.cold_out_C<=p.cold_in_C:
        raise ValueError("热流必须降温，冷流必须升温")
    a,b = p.hot_in_C-p.cold_out_C, p.hot_out_C-p.cold_in_C
    if min(a,b)<=0:
        raise ValueError("逆流两端温差必须大于零")
    lmtd = a if math.isclose(a,b,rel_tol=1e-10) else (a-b)/math.log(a/b)
    area = p.duty_kW*1000/(p.U_W_m2_K*p.correction_factor*lmtd)
    return {"LMTD_K":lmtd,"area_m2":area,"terminal_temperature_differences_K":[a,b], "validation":{"positive_approach":True,"duty_reconstructed_kW":area*p.U_W_m2_K*p.correction_factor*lmtd/1000},
        "warnings":["初步热工面积，不等同厂家选型；U、污垢、修正因子、压降与材料必须核实。"]}

def flash(p):
    from thermo import ChemicalConstantsPackage, CEOSGas, CEOSLiquid, PRMIX, FlashVL
    constants, correlations = ChemicalConstantsPackage.from_IDs([ALIASES.get(c,c) for c in p.compounds])
    kw = dict(Tcs=constants.Tcs, Pcs=constants.Pcs, omegas=constants.omegas)
    gas = CEOSGas(PRMIX, eos_kwargs=kw, HeatCapacityGases=correlations.HeatCapacityGases)
    liquid = CEOSLiquid(PRMIX, eos_kwargs=kw, HeatCapacityGases=correlations.HeatCapacityGases)
    result = FlashVL(constants,correlations,gas=gas,liquid=liquid).flash(T=p.temperature_K,P=p.pressure_Pa,zs=p.mole_fractions)
    xs = result.liquid0.zs if result.liquid_count else None
    ys = result.gas.zs if result.gas else None
    beta = result.VF
    recovered = [sum(phase.zs[i]*fraction for phase,fraction in zip(result.phases,result.betas)) for i in range(len(p.compounds))]
    residual = max(abs(a-b) for a,b in zip(recovered,p.mole_fractions))
    return {"model":"Peng–Robinson, kij=0","phase":result.phase,"vapor_fraction":beta,"liquid_mole_fractions":xs,"vapor_mole_fractions":ys,"validation":{"component_max_residual":residual,"passed":residual<1e-7},"sources":["https://thermo.readthedocs.io/thermo.flash.html"],"warnings":["未拟合二元交互参数；强非理想体系需验证 NRTL/UNIQUAC 等模型。收敛不能证明物性模型适用。"]}

def economics(p):
    cf=p.annual_revenue_CNY-p.annual_cost_CNY
    factor=sum(1/(1+p.discount_rate)**y for y in range(1,p.years+1))
    return {"annual_cashflow_CNY":cf,"NPV_CNY":-p.capital_CNY+cf*factor,"simple_payback_years":p.capital_CNY/cf if cf>0 else None,"sensitivity":[{"revenue_change":d,"NPV_CNY":-p.capital_CNY+(p.annual_revenue_CNY*(1+d)-p.annual_cost_CNY)*factor} for d in [-0.2,-0.1,0,0.1,0.2]],"warnings":["税前筛选模型，未包含税费、折旧、融资、建设期、营运资本、价格波动与残值。"]}

REGISTRY = {"properties":(PropertiesInput,properties),"steam":(SteamInput,steam),"convert_units":(UnitInput,convert_units),"material_balance":(BalanceInput,material_balance),"heat_exchanger":(HeatInput,heat_exchanger),"flash":(FlashInput,flash),"economics":(EconomicsInput,economics)}
_engine_lock=threading.RLock()

def calculate(name, args):
    if name not in REGISTRY:
        raise ValueError("未知计算工具")
    schema, function = REGISTRY[name]
    validated=schema.model_validate(args)
    # chemicals/thermo lazily initialize shared data tables. Their first-use
    # flags can become visible to another thread before the tables exist.
    # Serialize numerical engine entry points, including first-use loading.
    with _engine_lock:
        result = function(validated)
    json.dumps(result,allow_nan=False)
    return result

# Register specialized models after the common calculations.
from app.reaction import ReactionInput, reaction_thermo
REGISTRY["reaction_thermo"]=(ReactionInput,reaction_thermo)
from app.reaction import MixtureReactionInput, mixture_reaction
REGISTRY["mixture_reaction"]=(MixtureReactionInput,mixture_reaction)
from app.equipment import DistillationInput, PipeInput, PinchInput, distillation_shortcut, pipe_pressure_drop, pinch_analysis
REGISTRY.update(distillation_shortcut=(DistillationInput,distillation_shortcut),pipe_pressure_drop=(PipeInput,pipe_pressure_drop),pinch_analysis=(PinchInput,pinch_analysis))
from app.column import ColumnInput, column_mesh
REGISTRY["column_mesh"]=(ColumnInput,column_mesh)
from app.aqueous import AqueousFlashInput, aqueous_flash
REGISTRY["aqueous_flash"]=(AqueousFlashInput,aqueous_flash)
from app.hydrogen import CondenserInput, condenser_flash
REGISTRY["condenser_flash"]=(CondenserInput,condenser_flash)
from app.frontend_process import FrontendInput, frontend_process
REGISTRY["frontend_process"]=(FrontendInput,frontend_process)
from app.wet_column import WetColumnInput, wet_column
REGISTRY["wet_column"]=(WetColumnInput,wet_column)
from app.separation_train import SeparationTrainInput, wet_separation_train
REGISTRY["wet_separation_train"]=(SeparationTrainInput,wet_separation_train)
from app.recycle_process import RecycleInput, recycle_process
REGISTRY["recycle_process"]=(RecycleInput,recycle_process)
from app.experimental import FitInput, fit_data
REGISTRY["fit_data"]=(FitInput,fit_data)
from app.cantera_backend import GasEquilibriumInput, GasReactorInput, gas_equilibrium, gas_reactor
REGISTRY.update(cantera_equilibrium=(GasEquilibriumInput,gas_equilibrium),cantera_reactor=(GasReactorInput,gas_reactor))
