"""DWSIM automation bridge.

DWSIM is external GPL software; it is detected locally and never bundled.
The bridge calls a small .NET runner (DwsimRunner.exe) that loads a DWSIM
flowsheet (.dwxmz / .dwxml), solves it with DWSIM.Automation.Automation3 and
returns stream results as JSON.

The runner sets TEMP/TMP to a workspace directory before loading because DWSIM
extracts .dwxmz archives through the system temp folder; this keeps the whole
operation inside the local data directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "dwsim" / "DwsimRunner.exe"


def find_home() -> Path | None:
    candidates = []
    env = os.getenv("DWSIM_HOME")
    if env:
        candidates.append(Path(env))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "DWSIM")
    candidates.append(Path(r"C:\Program Files\DWSIM"))
    for candidate in candidates:
        if (candidate / "DWSIM.Automation.dll").is_file():
            return candidate
    return None


def status() -> dict:
    home = find_home()
    samples = []
    if home and (home / "samples").is_dir():
        samples = [path.name for path in sorted((home / "samples").glob("*.dwxmz"))][:8]
    return {
        "installed": bool(home),
        "home": str(home) if home else "",
        "automation_dll": bool(home and (home / "DWSIM.Automation.dll").is_file()),
        "runner": RUNNER.is_file(),
        "samples": samples,
        "note": "DWSIM 是外部 GPL 软件，不随插件分发；此处只做本机检测与调用。",
    }


def run_flowsheet(flowsheet: str | Path, temp_dir: str | Path, timeout: int = 900) -> dict:
    home = find_home()
    if home is None:
        raise ValueError("未找到 DWSIM 安装（DWSIM.Automation.dll）；请安装 DWSIM 或设置 DWSIM_HOME")
    if not RUNNER.is_file():
        raise ValueError(f"DWSIM 运行器不存在：{RUNNER}")
    source = Path(flowsheet)
    if not source.is_file():
        raise ValueError(f"DWSIM 流程图不存在：{source}")
    if source.suffix.lower() not in {".dwxmz", ".dwxml"}:
        raise ValueError("flowsheet 必须是 .dwxmz 或 .dwxml 文件")
    temp = Path(temp_dir)
    temp.mkdir(parents=True, exist_ok=True)
    output = temp / f"dwsim-{uuid.uuid4().hex}.json"
    env = dict(os.environ)
    env["TEMP"] = str(temp)
    env["TMP"] = str(temp)
    command = [
        str(RUNNER), "run", str(source), str(output),
        "--temp", str(temp), "--dwsim", str(home),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
    if not output.is_file():
        message = (completed.stderr or completed.stdout or "").strip()
        raise ValueError("DWSIM 运行失败：" + (message[-1200:] or f"退出码 {completed.returncode}"))
    result = json.loads(output.read_text(encoding="utf-8"))
    if completed.returncode != 0:
        raise ValueError("DWSIM 运行失败：" + str(result.get("error") or completed.stderr)[:1200])
    return result


def streams_csv(result: dict) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["tag", "name", "type", "temperature_K", "pressure_Pa", "mass_flow_kg_s",
                     "compounds", "mole_fractions"])
    for stream in result.get("streams") or []:
        writer.writerow([
            stream.get("tag", ""), stream.get("name", ""), stream.get("type", ""),
            stream.get("temperature_k", ""), stream.get("pressure_pa", ""),
            stream.get("mass_flow_kg_s", ""),
            "；".join(stream.get("compounds") or []),
            "；".join(str(value) for value in (stream.get("mole_fractions") or [])),
        ])
    return buffer.getvalue()
