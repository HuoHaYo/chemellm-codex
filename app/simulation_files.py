"""Native model, inputs, outputs, plots and a standalone replay for gas studies."""
import csv
import importlib.metadata
import json
import threading
import zipfile

from app import config
from app.db import uid
from app.documents import artifact_record
from app.cantera_backend import packaged_mechanism

_plot_lock=threading.Lock()


def save_simulation(project_id,name,p,result):
    folder=config.ARTIFACTS/uid();folder.mkdir()
    model=result.pop("native_model_yaml")
    (folder/"mechanism.yaml").write_bytes(model.encode("utf-8"))
    original=packaged_mechanism(p.mechanism)
    source_target=folder/("source-"+original.name)
    source_target.write_bytes(original.read_bytes())
    (folder/"input.json").write_text(json.dumps({"tool":name,"arguments":p.model_dump()},ensure_ascii=False,indent=2),encoding="utf-8")
    (folder/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    (folder/"simulation.py").write_bytes((config.ROOT/"scripts/replay_cantera_native.py").read_bytes())
    license_file=next(f for f in importlib.metadata.files('cantera') if str(f).endswith('licenses/License.txt'))
    (folder/"LICENSE-Cantera.txt").write_bytes(license_file.locate().read_bytes())
    (folder/"README.md").write_text(
        "# Native Cantera study\n\nInstall Python 3.11+ and cantera==3.2.0, numpy<3, ruamel.yaml==0.18.16.\n"
        "Run `python simulation.py` next to mechanism.yaml, input.json and result.json.\n"
        "The replay checks the native and original-distribution YAML SHA256 and recorded numerical validation, then compares the final state "
        "and sampled trajectory with rel_tol=1e-6, abs_tol=1e-8.\n\n"
        "The native YAML contains the initial phase, all species thermodynamics and reactions used. "
        "Cantera source/license: https://cantera.org/3.2/ (BSD-3-Clause; see LICENSE-Cantera.txt). "
        "GRI-Mech source: https://combustion.berkeley.edu/gri-mech/ . "
        "source-*.yaml preserves the original distribution file, including its description, comments and references. "
        "mechanism.yaml is the derived self-contained model actually used for this study.\n\n"
        "Gas-only research calculation. Numerical consistency and replay do not establish experimental kinetics or engineering validity. "
        "wgs4 intentionally restricts equilibrium species to CO/H2O/CO2/H2. Other species, phases, catalysis and transport are excluded.\n",
        encoding="utf-8")
    targets=[folder/f for f in ["mechanism.yaml","input.json","result.json","simulation.py","LICENSE-Cantera.txt","README.md"]]+[source_target]
    if result.get("trajectory"):
        rows=result["trajectory"];species=list(result["initial"]["mole_fractions"])
        csv_path=folder/"trajectory.csv"
        with csv_path.open("w",encoding="utf-8-sig",newline="") as stream:
            writer=csv.writer(stream)
            writer.writerow(["time_s","temperature_K","pressure_Pa","density_kg_m3","volume_m3",*['X_'+s for s in species]])
            for row in rows:writer.writerow([row[k] for k in ['time_s','temperature_K','pressure_Pa','density_kg_m3','volume_m3']]+[row['mole_fractions'][s] for s in species])
        targets.append(csv_path)
        with _plot_lock:
            from matplotlib.figure import Figure
            fig=Figure(figsize=(8,6),layout='constrained');axes=fig.subplots(2,1,sharex=True)
            times=[r['time_s']*1000 for r in rows]
            axes[0].plot(times,[r['temperature_K'] for r in rows],color='#236454')
            axes[0].set_ylabel('Temperature [K]')
            tracked=sorted(species,key=lambda s:max(r['mole_fractions'][s] for r in rows),reverse=True)[:6]
            for s in tracked:axes[1].plot(times,[r['mole_fractions'][s] for r in rows],label=s)
            axes[1].set_ylabel('Mole fraction');axes[1].set_xlabel('Time [ms]');axes[1].legend(ncol=3,fontsize=8)
            for ax in axes:ax.grid(alpha=.2)
            axes[0].set_title('Cantera gas reactor / finite-time research calculation')
            for suffix in ['svg','pdf']:
                target=folder/('reactor-profile.'+suffix);fig.savefig(target);targets.append(target)
            fig.clear()
    archive_path=folder/'cantera-native-study.zip'
    with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED) as archive:
        for target in targets:archive.write(target,target.name)
    metadata={"status":"research_requires_review","backend":"Cantera","backend_version":result['backend_version'],
              "native_model_sha256":result['native_model_sha256']}
    files=[artifact_record(project_id,target,text=target.read_text(encoding='utf-8-sig') if target.suffix in {'.json','.yaml','.py','.md','.txt'} else '',metadata=metadata) for target in targets+[archive_path]]
    result['artifacts']=files
    result['native_archive_id']=files[-1]['id']
    result['plot_artifact_id']=next((f['id'] for f in files if f['name']=='reactor-profile.svg'),None)
    return result
