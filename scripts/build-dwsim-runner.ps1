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
$sources = @(Get-ChildItem -LiteralPath $outDir -Filter *.cs | ForEach-Object { $_.FullName })
$dwsim = if ($env:DWSIM_HOME) { $env:DWSIM_HOME } else { Join-Path $env:LOCALAPPDATA "DWSIM" }
$skia = Join-Path $dwsim 'SkiaSharp.dll'
if (-not (Test-Path $skia)) { throw "DWSIM SkiaSharp.dll not found: $skia" }
& $csc /nologo /platform:x64 /reference:System.Web.Extensions.dll /reference:System.Xml.Linq.dll "/reference:$skia" /out:$exe $sources
if ($LASTEXITCODE -ne 0) { throw "csc failed: $LASTEXITCODE" }

$sourceHashes = [ordered]@{}
foreach ($source in ($sources | Sort-Object)) {
    $bytes = [IO.File]::ReadAllBytes($source)
    $normalized = [Text.Encoding]::UTF8.GetString($bytes).Replace("`r`n", "`n")
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($normalized))
        $sourceHashes[[IO.Path]::GetFileName($source)] = ([BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
    } finally { $hasher.Dispose() }
}
$fingerprint = [ordered]@{
    schema_version = 1
    runner_sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    sources = $sourceHashes
}
[IO.File]::WriteAllText((Join-Path $outDir 'DwsimRunner.build.json'), ($fingerprint | ConvertTo-Json -Depth 4) + "`n", (New-Object Text.UTF8Encoding($false)))

$config = Join-Path $dwsim "DWSIM.Automation.dll.config"
if (Test-Path $config) {
    $configText = ((Get-Content -LiteralPath $config) | ForEach-Object { $_.TrimEnd() }) -join "`n"
    [System.IO.File]::WriteAllText((Join-Path $outDir "DwsimRunner.exe.config"), $configText.TrimEnd() + "`n", (New-Object System.Text.UTF8Encoding($false)))
} else {
    Write-Warning "DWSIM.Automation.dll.config not found; runtime binding redirects may be missing"
}
Write-Host "built $exe"
