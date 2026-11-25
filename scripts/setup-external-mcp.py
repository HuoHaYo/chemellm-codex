"""Install optional, version-pinned engineering MCPs into this ChemELLM package."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / "runtime" / "external-mcp"
PINS = json.loads((ROOT / "release/external-mcp.json").read_text(encoding="utf-8"))
CONFIG = ROOT / "codex-plugin/.mcp.json"
OWNED = {f"chemellm-{key}" for key in PINS}
PYTHON = str(Path(sys.executable).resolve())
SCRIPT = str(Path(__file__).resolve())


def require(path):
    if not path or not Path(path).is_file():
        raise ValueError(f"Executable or file not found: {path}")
    return str(Path(path).resolve())


def verified_bytes(path, pin):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != pin["sha256"]:
        raise ValueError(f"SHA256 mismatch: {path.name}")


def fetch(pin, destination, supplied=None, *, source_json=False):
    if supplied:
        verified_bytes(supplied, pin)
        if supplied.resolve() != destination.resolve():
            shutil.copyfile(supplied, destination)
        return destination
    if destination.exists():
        verified_bytes(destination, pin)
        return destination
    pending = destination.with_suffix(".partial")
    request = urllib.request.Request(pin["url"], headers={
        "User-Agent": "ChemELLM-MCP-setup",
        "Accept": "application/vnd.github+json" if source_json else "application/octet-stream",
    })
    try:
        with urllib.request.urlopen(request, timeout=45) as incoming:
            if source_json:
                raw = incoming.read(2 * 1024 * 1024)
                pending.write_bytes(base64.b64decode(json.loads(raw)["content"]))
            else:
                with pending.open("wb") as output:
                    shutil.copyfileobj(incoming, output)
        verified_bytes(pending, pin)
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    return destination


def unpack(path, destination, expected, *, strip_root=False):
    if (destination / expected).is_file():
        return
    with tempfile.TemporaryDirectory(prefix="external-stage-", dir=destination.parent) as scratch:
        stage = Path(scratch) / "payload"
        stage.mkdir()
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                name = PurePosixPath(member.filename.replace("\\", "/"))
                if name.is_absolute() or ".." in name.parts or any(":" in item for item in name.parts):
                    raise ValueError(f"Invalid asset member: {member.filename}")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Unexpected symbolic link in package")
                parts = name.parts[1:] if strip_root else name.parts
                if not parts:
                    continue
                output = stage.joinpath(*parts)
                if member.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as incoming, output.open("xb") as stream:
                        shutil.copyfileobj(incoming, stream)
        if not (stage / expected).is_file():
            raise ValueError(f"Release package is missing {expected}")
        stage.rename(destination)


def command(*args, cwd=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True, timeout=600)


def find_uv():
    packaged = sorted((ROOT / "runtime").glob("uv-*/uv.exe"), reverse=True)
    return require(next(iter(packaged), None) or shutil.which("uv"))


def local_python(folder):
    path = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return require(path)


def windows_pair(create=False):
    if os.name != "nt":
        raise ValueError("This FreeCAD profile launcher currently requires Windows.")
    import win32crypt

    wrapped = RUNTIME / "freecad-token.dpapi"
    if not wrapped.is_file() and create:
        wrapped.parent.mkdir(parents=True, exist_ok=True)
        data = win32crypt.CryptProtectData(secrets.token_urlsafe(32).encode(), "ChemELLM FreeCAD MCP",
                                          None, None, None, 1)
        try:
            with wrapped.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            pass
    return win32crypt.CryptUnprotectData(wrapped.read_bytes(), None, None, None, 1)[1].decode() if wrapped.exists() else ""


def freecad_stdio(node):
    profile = RUNTIME / "freecad-profile"
    env = {**os.environ, "FREECAD_MCP_HOST": "127.0.0.1", "FREECAD_MCP_PORT": "39280",
           "FREECAD_MCP_TOKEN_DIR": str(profile), "FREECAD_MCP_TOKEN": windows_pair(),
           "FREECAD_MCP_TLS_CERT_FILE": ""}
    return subprocess.call([node, str(RUNTIME / "freecad/mcp-stdio-shim/index.js")], env=env)


def launcher(name, program, arguments, env=None):
    if os.name != "nt":
        return
    lines = ["@echo off", "chcp 65001 >nul", "setlocal DisableDelayedExpansion"]
    for key, value in (env or {}).items():
        lines.append(f'set "{key}={str(value).replace("%", "%%")}"')
    launch = " ".join('"' + str(arg).replace("%", "%%") + '"' for arg in (program, *arguments))
    lines.append('start "" ' + launch)
    (RUNTIME / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def python_entry(*args, **kwargs):
    return {"command": PYTHON, "args": ["-X", "utf8", *map(str, args)], **kwargs}


def install(tool, args):
    from app.external_tools import find_blender, find_freecad, find_qelectrotech

    RUNTIME.mkdir(parents=True, exist_ok=True)
    exchange = RUNTIME / "exchange" / tool
    exchange.mkdir(parents=True, exist_ok=True)
    cache = RUNTIME / "downloads"
    cache.mkdir(exist_ok=True)
    pin = PINS[tool]

    if tool == "dwsim":
        if args.dwsim_server:
            binary = require(args.dwsim_server)
            # Keep the established Windows bridge available alongside the user's
            # native server which may be a Windows executable or a local wrapper.
            return {"command": binary, "args": [], "cwd": str(exchange),
                    "startup_timeout_sec": 60, "tool_timeout_sec": 300}
        return python_entry("-m", "chemellm_codex.dwsim_mcp", "--exchange", exchange, tool_timeout_sec=200)

    if tool == "vega":
        package = fetch(pin, cache / "vega-0.4.0.mcpb", args.archive)
        destination = RUNTIME / "vega"
        unpack(package, destination, "server.py")
        command(find_uv(), "sync", "--directory", destination, "--locked", "--no-dev", "--python", PYTHON)
        return {"command": local_python(destination / ".venv"), "args": ["-X", "utf8", str(destination / "server.py")],
                "cwd": str(destination), "startup_timeout_sec": 45}

    if tool == "blender":
        blender = require(args.executable or find_blender())
        version = subprocess.check_output([blender, "--version"], text=True, timeout=20).splitlines()[0]
        import re
        match = re.match(r"Blender (\d+)\.(\d+)", version)
        if not match or tuple(map(int, match.groups())) < (5, 1):
            raise ValueError("Blender Lab MCP needs Blender 5.1 or newer.")
        source = RUNTIME / "blender-source"
        git = require(shutil.which("git"))
        if not source.exists():
            command(git, "-c", "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null",
                    "clone", "--depth", "1", "--branch", pin["ref"], pin["git"], source)
        actual = subprocess.check_output([git, "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if actual != pin["commit"] or subprocess.check_output(
                [git, "-C", str(source), "status", "--porcelain", "--untracked-files=no"]).strip():
            raise ValueError("Blender Lab MCP source differs from the pinned release.")
        uv = find_uv()
        venv = RUNTIME / "blender-venv"
        if not (venv / "pyvenv.cfg").is_file():
            command(uv, "venv", "--python", PYTHON, venv)
        python = local_python(venv)
        # Reuse the installer lock's version constraints without inheriting
        # the main plugin's virtual environment.
        lock = ROOT / "release/requirements-windows-x64.lock"
        constraints = cache / "blender-constraints.txt"
        constraints.write_text("\n".join(line.split()[0] for line in lock.read_text().splitlines()
                                         if "==" in line and not line[0].isspace()) + "\n", encoding="utf-8")
        command(uv, "pip", "install", "--python", python, "--constraint", constraints,
                "mcp==1.30.0", "docutils==0.23", "PyYAML==6.0.3", "setuptools", "wheel")
        command(uv, "pip", "install", "--python", python, "--no-deps", "--no-build-isolation", source / "mcp")
        profile = RUNTIME / "blender-profile"
        env = {"BLENDER_USER_RESOURCES": str(profile), "BLENDER_USER_CONFIG": str(profile / "config"),
               "BLENDER_USER_EXTENSIONS": str(profile / "extensions"), "BLENDER_PATH": blender}
        subprocess.run([blender, "--command", "extension", "build",
                        "--source-dir=" + str(source / "addon/blender_mcp_addon"), "--output-dir=" + str(cache)],
                       env={**os.environ, **env}, timeout=90, check=True)
        subprocess.run([blender, "--background", "--factory-startup", "--disable-autoexec", "--online-mode",
                        "--command", "extension", "install-file", str(cache / f"mcp-{pin['version']}.zip"),
                        "--repo", "user_default", "--enable"],
                       env={**os.environ, **env}, timeout=90, check=True)
        preferences = ("import bpy; p=bpy.context.preferences.addons['bl_ext.user_default.mcp'].preferences; "
                       "p.host='127.0.0.1'; p.port=19876; p.use_autostart=False; bpy.ops.wm.save_userpref()")
        subprocess.run([blender, "--background", "--disable-autoexec", "--offline-mode", "--python-exit-code",
                        "1", "--python-expr", preferences], env={**os.environ, **env}, timeout=60, check=True)
        launcher("Open-Blender-MCP.cmd", blender, ["--disable-autoexec", "--online-mode"], env)
        return {"command": python, "args": ["-X", "utf8", "-m", "blmcp"], "tool_timeout_sec": 240,
                "env": {**env, "BLENDER_MCP_HOST": "127.0.0.1", "BLENDER_MCP_PORT": "19876"}}

    if tool == "freecad":
        node = require(shutil.which("node"))
        discovered = find_freecad()
        binary = require(args.executable or (discovered.with_name("freecad.exe") if discovered else None))
        windows_pair(create=True)
        package = fetch(pin, cache / "freecad-0.1.30.zip", args.archive)
        unpack(package, RUNTIME / "freecad", "mcp-stdio-shim/index.js", strip_root=True)
        profile = RUNTIME / "freecad-profile"
        profile.mkdir(exist_ok=True)
        launcher("Open-FreeCAD-MCP.cmd", binary, ["--user-cfg", profile / "user.cfg", "--system-cfg",
                 profile / "system.cfg", ROOT / "scripts/freecad-mcp-start.FCMacro"],
                 {"CHEMELLM_MCP_RUNTIME": RUNTIME, "CHEMELLM_MCP_PYTHON": PYTHON, "CHEMELLM_MCP_SETUP": SCRIPT})
        return python_entry(SCRIPT, "--freecad-stdio", node, tool_timeout_sec=180)

    script = fetch(pin, cache / "qet_mcp.py", args.archive, source_json=True)
    binary = args.executable or find_qelectrotech()
    if args.qet_native_archive or args.download_qet_native:
        package = fetch(pin["windows_asset"], cache / "qet-0.200.1.zip", args.qet_native_archive)
        with zipfile.ZipFile(package) as archive:
            binaries = [name for name in archive.namelist()
                        if PurePosixPath(name).name.casefold() == "qelectrotech.exe"]
        if len(binaries) != 1:
            raise ValueError("The QElectroTech package does not contain one native executable.")
        unpack(package, RUNTIME / "qet-native", binaries[0])
        binary = RUNTIME / "qet-native" / binaries[0]
    env = {"QET_MCP_WORKSPACE": str(exchange), "QET_MCP_ALLOW_ANY_PATH": "0", "QET_ENABLE_SCRIPTING": "0", "QET_BINARY": ""}
    if binary and Path(binary).is_file():
        env["QET_BINARY"] = require(binary)
    # Enforce capability sets at the server, independent of the MCP host policy.
    tools = ["qet_project_info", "qet_elements", "qet_conductors", "qet_items", "qet_diff",
             "qet_scan", "qet_element_info", "qet_check", "qet_about"]
    if args.qet_native_archive or args.download_qet_native or args.qet_native:
        if not binary:
            raise ValueError("A QElectroTech executable with native CLI support is required.")
        help_result = subprocess.run([str(binary), "--help"], capture_output=True, timeout=30)
        help_text = (help_result.stdout + help_result.stderr).decode("utf-8", "replace")
        if help_result.returncode or not all(flag in help_text for flag in ("--export-svg", "--run")):
            raise ValueError("This QElectroTech build does not expose the required native CLI commands.")
        env["QET_ENABLE_SCRIPTING"] = "1"
        tools += ["qet_export", "qet_query", "qet_continuity", "qet_layout_check", "qet_element_search",
                  "qet_project_new", "qet_edit", "qet_element_build"]
    return python_entry(SCRIPT, "--qet-stdio", script, *tools, cwd=str(exchange), env=env, tool_timeout_sec=240)


def qet_stdio(path, selected):
    import runpy

    upstream = runpy.run_path(path, run_name="chemellm_qet_upstream")
    handle = upstream["handle"]
    allowed = set(selected)
    assert allowed <= set(upstream["_BY_NAME"])
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("method") == "tools/call" and request.get("params", {}).get("name") not in allowed:
                response = upstream["_err"](request.get("id"), -32602, "Tool is not enabled for this installation.")
            else:
                response = handle(request)
                if request.get("method") == "tools/list":
                    response["result"]["tools"] = [tool for tool in response["result"]["tools"] if tool["name"] in allowed]
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except (ValueError, TypeError, AttributeError):
            print(json.dumps(upstream["_err"](None, -32700, "Invalid JSON-RPC request.")), flush=True)


def update_config(entries=None, disable=None):
    # Reuse the main launcher generator so data roots and user-managed entries
    # survive both setup and reruns of INSTALL.cmd.
    import importlib.util
    spec = importlib.util.spec_from_file_location("chemellm_configure", ROOT / "scripts/configure-codex.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.generate(CONFIG, write=False)
    table = result["mcpServers"]
    if disable:
        table.pop("chemellm-" + disable, None)
    if entries:
        table.update(entries)
    pending = CONFIG.with_suffix(".pending")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(CONFIG)
    return table


def main():
    if sys.argv[1:] == ["--freecad-pair"]:
        if sys.stdout.isatty():
            raise SystemExit("FreeCAD pairing runs from the dedicated launcher.")
        sys.stdout.write(windows_pair(create=True))
        return
    if sys.argv[1:2] == ["--freecad-stdio"]:
        raise SystemExit(freecad_stdio(sys.argv[2]))
    if sys.argv[1:2] == ["--qet-stdio"]:
        qet_stdio(sys.argv[2], sys.argv[3:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=[*PINS, "status"])
    parser.add_argument("--disable", action="store_true", help="Remove this launcher from the plugin configuration.")
    parser.add_argument("--archive", type=Path, help="Previously downloaded, hash-pinned asset (Vega, FreeCAD or QET script).")
    parser.add_argument("--executable", type=Path, help="Select the local Blender, FreeCAD or QElectroTech executable.")
    parser.add_argument("--dwsim-server", type=Path, help="Use an already installed native DWSIM MCP executable.")
    parser.add_argument("--qet-native", action="store_true", help="Enable verified native CLI features in the selected QET executable.")
    parser.add_argument("--qet-native-archive", type=Path, help="Use the pinned Windows QET 0.200.1 portable ZIP.")
    parser.add_argument("--download-qet-native", action="store_true", help="Download the pinned QET preview side by side.")
    args = parser.parse_args()
    if args.tool == "status":
        entries = json.loads(CONFIG.read_text(encoding="utf-8"))["mcpServers"] if CONFIG.is_file() else {}
        for tool, pin in PINS.items():
            print(f"chemellm-{tool}: {'configured' if 'chemellm-' + tool in entries else 'not configured'}; "
                  f"upstream {pin.get('version', pin.get('upstream_version'))}")
        return
    if args.disable:
        update_config(disable=args.tool)
    else:
        entry = install(args.tool, args)
        update_config({"chemellm-" + args.tool: entry})
    print("Configuration saved. Run INSTALL.cmd to refresh the Codex plugin cache; then open a new chat.")


if __name__ == "__main__":
    main()
