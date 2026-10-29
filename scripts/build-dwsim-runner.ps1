# Build the external DWSIM automation runner.
# Requires Windows .NET Framework 4.x (csc.exe ships with Windows), and DWSIM installed.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build-dwsim-runner.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "kernel\dwsim\DwsimRunner.cs"
$outDir = Join-Path $root "kernel\dwsim"
$exe = Join-Path $outDir "DwsimRunner.exe"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"

if (-not (Test-Path $src)) { throw "missing $src" }
if (-not (Test-Path $csc)) { throw "csc.exe not found: $csc" }
& $csc /nologo /out:$exe $src
if ($LASTEXITCODE -ne 0) { throw "csc failed: $LASTEXITCODE" }

$dwsim = if ($env:DWSIM_HOME) { $env:DWSIM_HOME } else { Join-Path $env:LOCALAPPDATA "DWSIM" }
$config = Join-Path $dwsim "DWSIM.Automation.dll.config"
if (Test-Path $config) {
    Copy-Item $config (Join-Path $outDir "DwsimRunner.exe.config") -Force
} else {
    Write-Warning "DWSIM.Automation.dll.config not found; runtime binding redirects may be missing"
}
Write-Host "built $exe"
