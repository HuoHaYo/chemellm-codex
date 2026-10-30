param(
    [string]$CodexPath = '',
    [switch]$SkipRegistration
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + ';' + $env:PSModulePath
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtime = Join-Path $root 'runtime'
$python = Join-Path $root '.venv\Scripts\python.exe'

function Invoke-Checked {
    param([string]$Executable, [string[]]$ArgumentList)
    & $Executable @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($ArgumentList -join ' ')"
    }
}

function Find-Codex {
    if ($CodexPath) {
        if (-not (Test-Path -LiteralPath $CodexPath -PathType Leaf)) {
            throw "Codex executable does not exist: $CodexPath"
        }
        return (Resolve-Path -LiteralPath $CodexPath).Path
    }
    foreach ($name in @('codex.exe', 'codex.cmd', 'codex.ps1', 'codex')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
    }
    # Current desktop distribution and older Electron / Store layouts.
    $candidates = @()
    $desktopBins = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
    if (Test-Path -LiteralPath $desktopBins) {
        $candidates += Get-ChildItem -LiteralPath $desktopBins -Directory |
            Sort-Object LastWriteTime -Descending |
            ForEach-Object { Join-Path $_.FullName 'codex.exe' }
    }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Codex\resources\codex.exe'),
        (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\resources\codex.exe')
    )
    if (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue) {
        foreach ($package in @(Get-AppxPackage -Name '*Codex*' -ErrorAction SilentlyContinue)) {
            $candidates += Join-Path $package.InstallLocation 'app\resources\codex.exe'
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw 'Install or update Codex first, then rerun INSTALL.cmd. https://developers.openai.com/codex/app/'
}

try {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw 'This package supports Windows x64. ARM64 is not validated.'
    }
    # Fail before downloads if Codex is missing or lacks plugin management.
    $codex = $null
    if (-not $SkipRegistration) {
        $codex = Find-Codex
        & $codex plugin add --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Update Codex to a version that supports plugin management.' }
    }

    Write-Host "`nChemELLM Codex - install into: $root"
    Write-Host 'First installation downloads Python and scientific dependencies. No administrator access is required.'
    $bootstrap = Get-Content -LiteralPath (Join-Path $root 'release\bootstrap.json') -Raw | ConvertFrom-Json
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $uvRoot = Join-Path $runtime "uv-$($bootstrap.uvVersion)"
    $uv = Join-Path $uvRoot 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        $archive = Join-Path $runtime "uv-$($bootstrap.uvVersion).zip"
        if (-not (Test-Path -LiteralPath $archive) -or
            (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $bootstrap.uvSha256) {
            Write-Host '[1/6] Downloading the pinned uv bootstrap...'
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $bootstrap.uvUrl -OutFile $archive -UseBasicParsing
        }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $bootstrap.uvSha256) {
            throw 'Bootstrap SHA256 does not match. Download stopped; rerun the installer.'
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $uvRoot -Force
    }

    # Keep runtime, downloads and cache under this package; never touch DSH.
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
    $env:UV_CACHE_DIR = Join-Path $runtime 'cache'
    $env:UV_PYTHON_INSTALL_BIN = 'false'
    $env:UV_LINK_MODE = 'copy'
    $env:UV_SYSTEM_CERTS = 'true'
    if (-not $env:HTTPS_PROXY) {
        $testUrl = [Uri]'https://github.com/'
        $systemProxy = [Net.WebRequest]::GetSystemWebProxy().GetProxy($testUrl)
        if ($systemProxy -and $systemProxy.AbsoluteUri -ne $testUrl.AbsoluteUri) {
            $env:HTTPS_PROXY = $systemProxy.AbsoluteUri
        }
    }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Host '[2/6] Preparing an independent Python environment...'
        Invoke-Checked $uv @('venv', '--managed-python', '--python', $bootstrap.pythonVersion,
                             (Join-Path $root '.venv'))
    }
    Write-Host '[3/6] Installing locked, SHA256-checked dependencies...'
    Invoke-Checked $uv @('pip', 'install', '--python', $python, '--require-hashes',
                         '--only-binary', ':all:', '-r', (Join-Path $root 'release\requirements-windows-x64.lock'))
    Write-Host '[4/6] Installing ChemELLM and generating local configuration...'
    Invoke-Checked $uv @('pip', 'install', '--python', $python, '--no-deps',
                         '--no-build-isolation', '--editable', $root)
    Invoke-Checked $python @('-X', 'utf8', (Join-Path $root 'scripts\configure-codex.py'))
    Write-Host '[5/6] Checking real MCP startup and tool discovery...'
    Invoke-Checked $python @('-X', 'utf8', (Join-Path $root 'scripts\check-installation.py'))

    if (-not $SkipRegistration) {
        Write-Host '[6/6] Registering the plugin in Codex...'
        $catalogText = & $codex plugin list --json
        if ($LASTEXITCODE -ne 0) { throw 'Unable to read installed Codex plugins.' }
        $catalog = ($catalogText -join "`n") | ConvertFrom-Json
        $existing = @($catalog.installed | Where-Object { $_.pluginId -eq 'chemellm@chemellm-local' })
        if ($existing.Count -gt 0) {
            # Refresh the cached launcher; project data stays in its original folder.
            Invoke-Checked $codex @('plugin', 'remove', 'chemellm@chemellm-local', '--json')
        }
        $sourcesText = & $codex plugin marketplace list --json
        if ($LASTEXITCODE -ne 0) { throw 'Unable to read Codex marketplaces.' }
        $sources = ($sourcesText -join "`n") | ConvertFrom-Json
        $sourceList = if ($sources.marketplaces) { @($sources.marketplaces) } else { @($sources) }
        if (@($sourceList | Where-Object { $_.name -eq 'chemellm-local' }).Count -gt 0) {
            Invoke-Checked $codex @('plugin', 'marketplace', 'remove', 'chemellm-local', '--json')
        }
        Invoke-Checked $codex @('plugin', 'marketplace', 'add', $root, '--json')
        Invoke-Checked $codex @('plugin', 'add', 'chemellm@chemellm-local', '--json')
    }
    Write-Host "`nInstalled successfully. Restart Codex and open a new chat."
    Write-Host "Keep this folder in place. Your data is stored under: $(Join-Path $root 'data')"
    Write-Host 'Try: Use ChemELLM to calculate water properties and generate a Markdown report.'
} catch {
    Write-Host "`nInstallation failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Check your network / proxy, keep the complete extracted folder, and rerun INSTALL.cmd.'
    exit 1
}
