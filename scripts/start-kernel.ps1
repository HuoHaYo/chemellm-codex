param([int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Missing local .venv. Create it and run: .venv\Scripts\python.exe -m pip install -e ".[dev]"'
}
$env:CHEMELLM_KERNEL_HOST = '127.0.0.1'
$env:CHEMELLM_KERNEL_PORT = "$Port"
$env:CHEMELLM_DATA_ROOT = Join-Path $root 'data'
$env:CHEMELLM_KERNEL_DATA = Join-Path $root 'data\kernel'
$env:CHEMELLM_DATA_DIR = $env:CHEMELLM_KERNEL_DATA
$env:CHEMELLM_SNAPSHOT_DIR = Join-Path $root '.chem'
$env:CHEMELLM_APP_DIR = Join-Path $root 'app'
Push-Location $root
try {
    & $python -X utf8 (Join-Path $root 'kernel\server.py')
    if ($LASTEXITCODE -ne 0) { throw "Kernel exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
