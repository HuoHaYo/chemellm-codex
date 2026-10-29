"""Bounded Cantera gas calculations; only packaged mechanisms are executable."""
import hashlib
import importlib.util
import io
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator
from app.schemas import Inputs


class GasInput(Inputs):
    mechanism: Literal["h2o2", "gri30", "wgs4"] = "h2o2"
    temperature_K: float = Field(default=1001, ge=300, le=3000)
    pressure_Pa: float = Field(default=101325, ge=1000, le=2e6)
    composition: dict[str, float] = Field(default={"H2":2,"O2":1,"N2":4}, min_length=1, max_length=53,
                                        description="物种名称及相对摩尔量，由后端归一化；区分大小写")

    @model_validator(mode="after")
    def mixture(self):
        if any(not name or len(name)>30 or not math.isfinite(v) or v<0 for name,v in self.composition.items()) or sum(self.composition.values())<=0:
            raise ValueError("组分相对摩尔量须非负且总量为正")
        if not math.isfinite(sum(self.composition.values())) or sum(self.composition.values())>1e12:
            raise ValueError("组分相对量过大，请统一缩放")
        return self


class GasEquilibriumInput(GasInput):
    constraint: Literal["TP", "HP"] = "HP"


class GasReactorInput(GasInput):
    constraint: Literal["constant_pressure", "constant_volume"] = "constant_pressure"
    energy: Literal["adiabatic", "isothermal"] = "adiabatic"
    end_time_s: float = Field(default=.001, ge=1e-8, le=10)
    samples: int = Field(default=160, ge=20, le=600)

    @model_validator(mode="after")
    def kinetics(self):
        if self.mechanism=="wgs4":
            raise ValueError("wgs4只含四物种热力学，不提供动力学；请选择h2o2或gri30")
        return self


WARNINGS=[
    "理想气相均相模型；未验证本项目催化剂、非理想流体、液固相、传热传质或设备设计。",
    "h2o2/gri30是随Cantera发布的指定机理，适用条件和参数不确定度需按原机理核查；不能作为乙苯催化脱氢机理。",
    "元素/能量/平衡残差检查属于数值一致性，不是实验准确性或工程审核。默认值为演示输入。",
]


def packaged_mechanism(mechanism):
    import cantera as ct
    if mechanism not in {"h2o2","gri30","wgs4"}:
        raise ValueError("只允许固定发布的机理")
    filename="gri30.yaml" if mechanism=="wgs4" else mechanism+".yaml"
    return Path(ct.__file__).resolve().parent/"data"/filename


def load_phase(p):
    import cantera as ct
    if ct.__version__!="3.2.0":
        raise ValueError("需使用锁定的Cantera 3.2.0以保留机理和复算版本")
    if p.mechanism=="wgs4":
        species=ct.Species.list_from_file(str(packaged_mechanism(p.mechanism)))
        gas=ct.Solution(thermo="ideal-gas",species=[s for s in species if s.name in {"CO","H2O","CO2","H2"}],name="wgs4")
    else:
        gas=ct.Solution(str(packaged_mechanism(p.mechanism)))
    unknown=set(p.composition)-set(gas.species_names)
    if unknown:
        raise ValueError("机理中不存在的物种："+", ".join(sorted(unknown)))
    gas.TPX=p.temperature_K,p.pressure_Pa,p.composition
    if not gas.min_temp<=p.temperature_K<=gas.max_temp:
        raise ValueError("进料温度超出机理共同热力学温区")
    return gas


def state(gas):
    return {"temperature_K":float(gas.T),"pressure_Pa":float(gas.P),"density_kg_m3":float(gas.density),
            "enthalpy_J_kg":float(gas.enthalpy_mass),"internal_energy_J_kg":float(gas.int_energy_mass),
            "mean_molecular_weight_kg_kmol":float(gas.mean_molecular_weight),
            "mole_fractions":dict(zip(gas.species_names,gas.X.tolist()))}


def native_yaml(gas):
    # Cantera adds generation timestamps. Remove only that volatile metadata;
    # keep every phase/species/reaction and the actual initial state.
    from ruamel.yaml import YAML
    parser=YAML(typ="safe",pure=True)
    content=parser.load(gas.write_yaml())
    content.pop("date",None)
    output=io.StringIO();parser.dump(content,output)
    return output.getvalue()


def element_mass(gas):
    return np.array([gas.elemental_mass_fraction(e) for e in gas.element_names])


def integrate(gas,p):
    import cantera as ct
    factory=ct.IdealGasConstPressureReactor if p.constraint=="constant_pressure" else ct.IdealGasReactor
    reactor=factory(gas,energy="on" if p.energy=="adiabatic" else "off",volume=1.,clone=False)
    sim=ct.ReactorNet([reactor]);sim.rtol=1e-10;sim.atol=1e-18;sim.max_steps=20000
    # Fixed requested samples; adaptive CVODES internal steps remain independent.
    times=np.concatenate(([0.],np.geomspace(p.end_time_s*1e-7,p.end_time_s,p.samples)))
    initial=state(gas);elements=element_mass(gas);mass=float(reactor.mass)
    energy_key="enthalpy_J_kg" if p.constraint=="constant_pressure" else "internal_energy_J_kg"
    trajectory=[];max_element=max_mass=max_energy=max_constraint=0.;all_temperatures_valid=True
    for t in times:
        if t>0:sim.advance(float(t))
        current=state(gas)
        trajectory.append({"time_s":float(sim.time),**current,"volume_m3":float(reactor.volume)})
        max_element=max(max_element,float(np.max(np.abs(element_mass(gas)-elements))))
        max_mass=max(max_mass,abs(reactor.mass-mass)/mass)
        if p.energy=="adiabatic":
            max_energy=max(max_energy,abs(current[energy_key]-initial[energy_key])/max(1e5,abs(initial[energy_key])))
        else:
            max_energy=max(max_energy,abs(gas.T-p.temperature_K)/p.temperature_K)
        c=abs(gas.P-p.pressure_Pa)/p.pressure_Pa if p.constraint=="constant_pressure" else abs(reactor.volume-1.)
        max_constraint=max(max_constraint,c)
        all_temperatures_valid &= gas.min_temp<=gas.T<=gas.max_temp
    return {"initial":initial,"final":state(gas),"trajectory":trajectory,
            "solver":{"name":"CVODES / ReactorNet","rtol":sim.rtol,"atol":sim.atol,"stats":dict(sim.solver_stats)},
            "validation":{"element_mass_fraction_max_error":max_element,"mass_relative_error":float(max_mass),
                          "energy_or_isothermal_scaled_error":float(max_energy),"constraint_relative_error":float(max_constraint),
                          "within_thermo_temperature_range":bool(all_temperatures_valid),
                          "passed":bool(max_element<1e-8 and max_mass<1e-8 and max_energy<2e-7 and max_constraint<1e-8 and all_temperatures_valid)},
            "energy_boundary":"closed, adiabatic" if p.energy=="adiabatic" else "closed, thermostatted; required heat not reported",
            "sampling_note":"Requested logarithmic output grid; not an ignition-delay or steady-state certificate. End time is finite, not equilibrium."}


def solve(mode,p):
    import cantera as ct
    gas=load_phase(p)
    model=native_yaml(gas)
    if mode=="reactor":
        result=integrate(gas,p)
    else:
        initial=state(gas);before=element_mass(gas)
        gas.equilibrate(p.constraint,solver="gibbs",rtol=1e-10,max_steps=1000)
        after=state(gas);error=float(np.max(np.abs(element_mass(gas)-before)))
        thermal=abs(gas.T-p.temperature_K)/p.temperature_K if p.constraint=="TP" else abs(gas.enthalpy_mass-initial["enthalpy_J_kg"])/max(1e5,abs(initial["enthalpy_J_kg"]))
        pressure_error=abs(gas.P-p.pressure_Pa)/p.pressure_Pa
        in_range=gas.min_temp<=gas.T<=gas.max_temp
        result={"initial":initial,"final":after,"solver":{"name":"Cantera Gibbs minimization","rtol":1e-10},
                "validation":{"element_mass_fraction_max_error":error,"constraint_scaled_error":float(thermal),
                              "pressure_relative_error":float(pressure_error),"within_thermo_temperature_range":bool(in_range),
                              "passed":bool(error<1e-8 and thermal<2e-7 and pressure_error<1e-8 and in_range)}}
    return {"backend":"Cantera","backend_version":ct.__version__,"mode":mode,"mechanism":p.mechanism,
            "source_mechanism":{"filename":packaged_mechanism(p.mechanism).name,
                                "sha256":hashlib.sha256(packaged_mechanism(p.mechanism).read_bytes()).hexdigest(),
                                "distribution":"cantera==3.2.0"},
            "phase_model":"ideal-gas; gas-only species from fixed mechanism",
            "species_count":gas.n_species,"reaction_count":gas.n_reactions,
            "thermo_temperature_range_K":[float(gas.min_temp),float(gas.max_temp)],
            "native_model_sha256":hashlib.sha256(model.encode()).hexdigest(),"native_model_yaml":model,
            **result,"warnings":WARNINGS+(["wgs4仅允许CO/H2O/CO2/H2四物种平衡；排除了甲烷、固体碳和其他可能产物。"] if p.mechanism=="wgs4" else []),
            "sources":["https://cantera.org/3.2/","https://cantera.org/3.2/examples/python/reactors/reactor1.html",
                       "https://combustion.berkeley.edu/gri-mech/"]}


def isolated(mode,p):
    if importlib.util.find_spec("cantera") is None:
        raise ValueError("Cantera后端未安装；请安装项目simulation扩展")
    try:
        process=subprocess.run([sys.executable,"-X","utf8","-m","app.cantera_backend","--worker",mode],
                               input=p.model_dump_json(),capture_output=True,text=True,encoding="utf-8",timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Cantera计算超过60秒，子进程已终止；请检查工况或缩短积分时间") from exc
    if process.returncode:
        try:message=json.loads(process.stdout)["error"]
        except (ValueError,KeyError):message="后端进程失败；请检查依赖、机理与输入"
        raise ValueError(message)
    try:result=json.loads(process.stdout)
    except ValueError as exc:raise ValueError("Cantera未返回有效数值结果") from exc
    return result


def gas_equilibrium(p):return isolated("equilibrium",p)
def gas_reactor(p):return isolated("reactor",p)


if __name__=="__main__":
    try:
        mode=sys.argv[2]
        schema={"equilibrium":GasEquilibriumInput,"reactor":GasReactorInput}[mode]
        result=solve(mode,schema.model_validate_json(sys.stdin.read()))
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    except Exception as exc:
        # Errors contain no provider request or secrets; do not expose arbitrary stderr.
        print(json.dumps({"error":str(exc)[:1800]},ensure_ascii=False))
        raise SystemExit(1)
