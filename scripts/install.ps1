param(
    [string]$InstallDir,
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [switch]$NoStart
)

# Isolate variables/functions when invoked through: irm URL/install.ps1 | iex
& {
    param($Destination, $ListenPort, $InstallOnly)
    $ErrorActionPreference = 'Stop'
    Set-StrictMode -Version 2
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This installer is for Windows. Use install.sh on Linux or macOS.'
    }
    if (-not $Destination) {
        $Destination = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ShiritaiBot'
    }
    $ShiritaiRoot = [IO.Path]::GetFullPath($Destination)
    $architectureHint = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $architecture = switch ($architectureHint) {
        'AMD64' { 'x64' }
        'ARM64' { 'arm64' }
        default { throw 'Windows x64 and ARM64 are supported.' }
    }
    if ((Test-Path -LiteralPath $ShiritaiRoot) -and -not (Test-Path -LiteralPath $ShiritaiRoot -PathType Container)) {
        throw "Installation path is not a directory: $ShiritaiRoot"
    }
    if ((Test-Path -LiteralPath $ShiritaiRoot -PathType Container) -and
        (Get-ChildItem -LiteralPath $ShiritaiRoot -Force | Select-Object -First 1) -and
        -not (Test-Path -LiteralPath (Join-Path $ShiritaiRoot '.shiritaibot-installer'))) {
        throw "Directory contains other files. Choose an empty -InstallDir: $ShiritaiRoot"
    }

    $null = New-Item -ItemType Directory -Path $ShiritaiRoot -Force
    Set-Content -LiteralPath (Join-Path $ShiritaiRoot '.shiritaibot-installer') -Value 'shiritaibot-installer-v1'
    $runtime = Join-Path $ShiritaiRoot 'runtime'
    $bin = Join-Path $runtime 'bin'
    $app = Join-Path $ShiritaiRoot 'app'
    $data = Join-Path $ShiritaiRoot 'data'
    $staging = Join-Path $ShiritaiRoot ('.install-' + [Guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $bin, $data, $staging -Force
    $savedEnvironment = @{}
    foreach ($key in @('PATH', 'UV_PYTHON_INSTALL_DIR', 'UV_PYTHON_BIN_DIR', 'UV_CACHE_DIR', 'UV_UNMANAGED_INSTALL', 'npm_config_cache')) {
        $savedEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    }
    $savedTls = [Net.ServicePointManager]::SecurityProtocol
    $locationPushed = $false
    try {
        [Net.ServicePointManager]::SecurityProtocol = $savedTls -bor [Net.SecurityProtocolType]::Tls12
        $env:PATH = "$bin;$(Join-Path $runtime 'node');$env:PATH"
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
        $env:UV_PYTHON_BIN_DIR = $bin
        $env:UV_CACHE_DIR = Join-Path $runtime 'cache/uv'
        $env:npm_config_cache = Join-Path $runtime 'cache/npm'

        function Invoke-ShiritaiDownload([string]$Url, [string]$Path) {
            for ($attempt = 1; $attempt -le 3; $attempt++) {
                try {
                    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Path -TimeoutSec 180
                    return
                } catch {
                    if ($attempt -eq 3) { throw }
                    Start-Sleep -Seconds 2
                }
            }
        }
        function Invoke-ShiritaiNative([string]$File, [string[]]$Arguments) {
            & $File @Arguments
            if ($LASTEXITCODE -ne 0) { throw "$File exited with status $LASTEXITCODE" }
        }
        function Find-ShiritaiNode {
            param([string[]]$Candidates = @(Get-Command node.exe -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }))
            foreach ($candidate in $Candidates) {
                try {
                    # Windows PowerShell 5.1 removes embedded quotes from node -e arguments.
                    $version = ((& $candidate --version 2>$null) | Out-String).Trim()
                    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^v(\d+)\.(\d+)\.(\d+)$') { continue }
                    $major = [int]$Matches[1]
                    $minor = [int]$Matches[2]
                    if (-not (($major -eq 20 -and $minor -ge 19) -or ($major -eq 22 -and $minor -ge 12) -or $major -ge 24)) { continue }
                    # Resolve version-manager shims and invoke npm with this same Node binary.
                    $executable = ((& $candidate -p process.execPath 2>$null) | Out-String).Trim()
                    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $executable -PathType Leaf)) { continue }
                    $directory = Split-Path -Parent $executable
                    $npmCli = Join-Path $directory 'node_modules/npm/bin/npm-cli.js'
                    if (-not (Test-Path -LiteralPath $npmCli -PathType Leaf)) { continue }
                    $npmVersion = ((& $executable $npmCli --version 2>$null) | Out-String).Trim()
                    if ($LASTEXITCODE -ne 0 -or -not $npmVersion) { continue }
                    return [pscustomobject]@{ Executable = $executable; Directory = $directory; NpmCli = $npmCli; Version = $version }
                } catch { continue }
            }
            return $null
        }
        function Test-ShiritaiSource([string]$Path) {
            return ((Test-Path -LiteralPath (Join-Path $Path 'src/shiritaibot/cli.py')) -and
                (Test-Path -LiteralPath (Join-Path $Path 'uv.lock')) -and
                (Test-Path -LiteralPath (Join-Path $Path 'web/package-lock.json')))
        }

        if (Test-Path -LiteralPath $app) {
            if (-not (Test-ShiritaiSource $app)) { throw "Existing app is not complete ShiritaiBot source: $app" }
            Write-Host 'Using existing ShiritaiBot source.'
        } else {
            Write-Host '[1/4] Downloading ShiritaiBot'
            $archive = Join-Path $staging 'source.zip'
            Invoke-ShiritaiDownload 'https://github.com/XCM42-Orion/ShiritaiBot/archive/refs/heads/main.zip' $archive
            $sourceStage = Join-Path $staging 'source'
            Expand-Archive -LiteralPath $archive -DestinationPath $sourceStage
            $sourceFolders = @(Get-ChildItem -LiteralPath $sourceStage -Directory)
            if ($sourceFolders.Count -ne 1 -or -not (Test-ShiritaiSource $sourceFolders[0].FullName)) {
                throw 'Downloaded source is incomplete.'
            }
            Move-Item -LiteralPath $sourceFolders[0].FullName -Destination $app
        }

        Write-Host '[2/4] Preparing Python and Node.js'
        $shellExe = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
        $uv = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $uv) {
            $uvInstaller = Join-Path $staging 'uv-install.ps1'
            Invoke-ShiritaiDownload 'https://astral.sh/uv/install.ps1' $uvInstaller
            $env:UV_UNMANAGED_INSTALL = $bin
            Invoke-ShiritaiNative -File $shellExe -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $uvInstaller)
            $uv = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        if (-not $uv) { throw 'uv installation did not produce uv.exe.' }
        $uvExe = $uv.Source
        Invoke-ShiritaiNative -File $uvExe -Arguments @('--version')

        $node = Find-ShiritaiNode
        if (-not $node) {
            Write-Host 'Preparing Node.js 24 inside the installation directory.'
            $nodeDestination = Join-Path $runtime 'node'
            $manifest = Join-Path $staging 'SHASUMS256.txt'
            Invoke-ShiritaiDownload 'https://nodejs.org/download/release/latest-v24.x/SHASUMS256.txt' $manifest
            $entry = Get-Content -LiteralPath $manifest | Where-Object { $_ -match "^[a-fA-F0-9]{64}\s+node-v24\.\d+\.\d+-win-$architecture\.zip$" } | Select-Object -First 1
            if (-not $entry) { throw 'No Node.js 24 archive was found for this architecture.' }
            $checksum, $fileName = $entry -split '\s+'
            $nodeArchive = Join-Path $staging 'node.zip'
            Invoke-ShiritaiDownload "https://nodejs.org/download/release/latest-v24.x/$fileName" $nodeArchive
            if ((Get-FileHash -LiteralPath $nodeArchive -Algorithm SHA256).Hash -ne $checksum) { throw 'Node.js checksum verification failed. Please retry.' }
            $nodeStage = Join-Path $staging 'node'
            Expand-Archive -LiteralPath $nodeArchive -DestinationPath $nodeStage
            $nodeFolders = @(Get-ChildItem -LiteralPath $nodeStage -Directory)
            if ($nodeFolders.Count -ne 1) { throw 'Node.js archive is incomplete.' }
            $stagedNode = Find-ShiritaiNode -Candidates @((Join-Path $nodeFolders[0].FullName 'node.exe'))
            if (-not $stagedNode) { throw 'Downloaded Node.js/npm could not be validated.' }
            if (Test-Path -LiteralPath $nodeDestination) {
                $previousNode = Join-Path $runtime ('.node-backup-' + [Guid]::NewGuid().ToString('N'))
                Move-Item -LiteralPath $nodeDestination -Destination $previousNode
            }
            Move-Item -LiteralPath $nodeFolders[0].FullName -Destination $nodeDestination
            $node = Find-ShiritaiNode -Candidates @((Join-Path $nodeDestination 'node.exe'))
        }
        if (-not $node) { throw 'Node.js/npm could not be validated in the installation directory.' }
        $nodeExe = $node.Executable
        $nodeDir = $node.Directory
        $npmCli = $node.NpmCli
        $env:PATH = "$nodeDir;$env:PATH"
        Write-Host "Using Node.js $($node.Version): $nodeExe"

        Write-Host '[3/4] Installing dependencies and building the control panel'
        Push-Location -LiteralPath $app
        $locationPushed = $true
        Invoke-ShiritaiNative -File $uvExe -Arguments @('sync', '--frozen', '--no-dev')
        Invoke-ShiritaiNative -File $nodeExe -Arguments @($npmCli, '--prefix', 'web', 'ci', '--no-audit', '--no-fund')
        Invoke-ShiritaiNative -File $nodeExe -Arguments @($npmCli, '--prefix', 'web', 'run', 'build')

        $launcher = Join-Path $ShiritaiRoot 'start.ps1'
        $settings = "param([switch]`$Token, [ValidateRange(1, 65535)][int]`$Port = $ListenPort)`r`n"
        $settings += "`$ShiritaiUv = '" + $uvExe.Replace("'", "''") + "'`r`n"
        $settings += "`$ShiritaiNode = '" + $nodeDir.Replace("'", "''") + "'`r`n"
        $launcherBody = @'
$ErrorActionPreference = 'Stop'
$ShiritaiRoot = $PSScriptRoot
$env:PATH = "$ShiritaiNode;$(Join-Path $ShiritaiRoot 'runtime/bin');$env:PATH"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ShiritaiRoot 'runtime/python'
$env:UV_PYTHON_BIN_DIR = Join-Path $ShiritaiRoot 'runtime/bin'
$env:UV_CACHE_DIR = Join-Path $ShiritaiRoot 'runtime/cache/uv'
Set-Location -LiteralPath (Join-Path $ShiritaiRoot 'app')
if ($Token) {
    & $ShiritaiUv run --frozen --no-sync python -m shiritaibot.cli token --home (Join-Path $ShiritaiRoot 'data')
} else {
    & $ShiritaiUv run --frozen --no-sync python -m shiritaibot.cli run --home (Join-Path $ShiritaiRoot 'data') --port $Port
}
exit $LASTEXITCODE
'@
        # UTF-8 BOM lets stock Windows PowerShell 5.1 read non-ASCII install paths.
        [IO.File]::WriteAllText($launcher, $settings + $launcherBody, [Text.UTF8Encoding]::new($true))
        $quotedLauncher = "'" + $launcher.Replace("'", "''") + "'"
        Write-Host "`n[4/4] Deployment complete"
        Write-Host "Control panel: http://127.0.0.1:$ListenPort"
        Write-Host "Restart: powershell -NoProfile -ExecutionPolicy Bypass -File $quotedLauncher"
        Write-Host "Admin token: powershell -NoProfile -ExecutionPolicy Bypass -File $quotedLauncher -Token"
        Write-Host "Data: $data"
        if (-not $InstallOnly) {
            Write-Host 'Starting ShiritaiBot. Keep this terminal open; Ctrl+C stops it.'
            Invoke-ShiritaiNative -File $shellExe -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $launcher)
        }
    } finally {
        if ($locationPushed) { Pop-Location }
        foreach ($key in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($key, $savedEnvironment[$key], 'Process') }
        [Net.ServicePointManager]::SecurityProtocol = $savedTls
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
} $InstallDir $Port $NoStart.IsPresent
