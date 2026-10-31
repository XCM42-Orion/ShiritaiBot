$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Run this integration test on Windows.' }
$installer = (Resolve-Path (Join-Path $PSScriptRoot '../install.ps1')).Path
$originalPath = $env:PATH
$native = (Get-Command node.exe -CommandType Application | Select-Object -First 1).Source
$native = ((& $native -p process.execPath) | Out-String).Trim()
$nodeHash = (Get-FileHash -LiteralPath $native -Algorithm SHA256).Hash
$npmPackage = Join-Path (Split-Path -Parent $native) 'node_modules/npm'
if (-not (Test-Path -LiteralPath $npmPackage)) { throw 'The Windows test runner needs a complete Node.js installation.' }
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('shiritai-windows-install-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $scratch
try {
    $tools = Join-Path $scratch 'tools'
    $null = New-Item -ItemType Directory -Path $tools
    $uvSource = Join-Path $tools 'uv.cs'
    [IO.File]::WriteAllText($uvSource, @'
using System;
public class UvFixture {
    public static int Main(string[] args) {
        if (args.Length > 0 && args[0] == "--version") Console.WriteLine("uv fixture");
        if (Array.IndexOf(args, "token") >= 0) Console.WriteLine("fixture-token");
        return 0;
    }
}
exit 0
'@)
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    & $compiler /nologo /target:exe ("/out:" + (Join-Path $tools 'uv.exe')) $uvSource
    if ($LASTEXITCODE -ne 0) { throw 'Unable to compile the isolated uv fixture.' }

    $nodeImage = Join-Path $scratch 'node-v24.0.0-win-x64'
    $null = New-Item -ItemType Directory -Path (Join-Path $nodeImage 'node_modules') -Force
    Copy-Item -LiteralPath $native -Destination (Join-Path $nodeImage 'node.exe')
    Copy-Item -LiteralPath $npmPackage -Destination (Join-Path $nodeImage 'node_modules/npm') -Recurse
    $nodeArchive = Join-Path $scratch 'node.zip'
    Compress-Archive -LiteralPath $nodeImage -DestinationPath $nodeArchive
    $nodeChecksum = (Get-FileHash -LiteralPath $nodeArchive -Algorithm SHA256).Hash

    $source = Join-Path $scratch 'ShiritaiBot-main'
    $null = New-Item -ItemType Directory -Path (Join-Path $source 'src/shiritaibot'), (Join-Path $source 'web') -Force
    [IO.File]::WriteAllText((Join-Path $source 'src/shiritaibot/cli.py'), '# isolated fixture')
    [IO.File]::WriteAllText((Join-Path $source 'uv.lock'), 'version = 1')
    [IO.File]::WriteAllText((Join-Path $source 'web/package.json'), '{"name":"installer-fixture","version":"1.0.0","scripts":{"build":"node build.cjs"}}')
    [IO.File]::WriteAllText((Join-Path $source 'web/package-lock.json'), '{"name":"installer-fixture","version":"1.0.0","lockfileVersion":3,"requires":true,"packages":{"":{"name":"installer-fixture","version":"1.0.0"}}}')
    [IO.File]::WriteAllText((Join-Path $source 'web/build.cjs'), 'const fs=require("fs");fs.mkdirSync("dist",{recursive:true});fs.writeFileSync("dist/index.html","installer fixture");')
    $sourceArchive = Join-Path $scratch 'source.zip'
    Compress-Archive -LiteralPath $source -DestinationPath $sourceArchive

    # All HTTP downloads are fixtures; Node and npm execute as real native programs.
    $global:ShiritaiInstallerFixture = @{
        Downloads = [Collections.Generic.List[string]]::new()
        SourceArchive = $sourceArchive
        NodeArchive = $nodeArchive
        NodeChecksum = $nodeChecksum
    }
    function global:Invoke-WebRequest {
        param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile, [int]$TimeoutSec)
        $global:ShiritaiInstallerFixture.Downloads.Add($Uri)
        if ($Uri -like '*/main.zip') { Copy-Item -LiteralPath $global:ShiritaiInstallerFixture.SourceArchive -Destination $OutFile }
        elseif ($Uri -like '*/SHASUMS256.txt') { [IO.File]::WriteAllText($OutFile, "$($global:ShiritaiInstallerFixture.NodeChecksum)  node-v24.0.0-win-x64.zip`n") }
        elseif ($Uri -like '*/node-v24.0.0-win-x64.zip') { Copy-Item -LiteralPath $global:ShiritaiInstallerFixture.NodeArchive -Destination $OutFile }
        else { throw "Unexpected network request in isolated test: $Uri" }
    }

    foreach ($scenario in @('existing', 'missing', 'broken-private', 'repair-private')) {
        $destination = Join-Path $scratch ("$scenario user's folder & " + [char]0x4e2d + [char]0x6587)
        $casePath = $originalPath
        if ($scenario -in @('missing', 'repair-private')) {
            $casePath = (($originalPath -split ';' | Where-Object { $_ -and -not (Test-Path -LiteralPath (Join-Path $_ 'node.exe')) }) -join ';')
        } else {
            $casePath = "$nodeImage;$casePath"
        }
        $env:PATH = "$tools;$casePath"
        $beforePath = $env:PATH
        $beforeCache = [Environment]::GetEnvironmentVariable('npm_config_cache', 'Process')
        if ($scenario -in @('broken-private', 'repair-private')) {
            $null = New-Item -ItemType Directory -Path (Join-Path $destination 'runtime/node'), (Join-Path $destination 'data') -Force
            [IO.File]::WriteAllText((Join-Path $destination '.shiritaibot-installer'), 'shiritaibot-installer-v1')
            [IO.File]::WriteAllText((Join-Path $destination 'runtime/node/node.exe'), 'broken private runtime')
            [IO.File]::WriteAllText((Join-Path $destination 'data/keep.txt'), 'keep existing data')
        }
        $global:ShiritaiInstallerFixture.Downloads.Clear()
        & $installer -InstallDir $destination -NoStart
        if (-not (Test-Path -LiteralPath (Join-Path $destination 'app/web/dist/index.html'))) { throw "Build was not completed: $scenario" }
        if ($env:PATH -cne $beforePath) { throw "Installer changed the caller PATH: $scenario" }
        if ([Environment]::GetEnvironmentVariable('npm_config_cache', 'Process') -cne $beforeCache) { throw "Installer changed the caller npm cache: $scenario" }
        $usedPrivate = @($global:ShiritaiInstallerFixture.Downloads | Where-Object { $_ -like '*/SHASUMS256.txt' }).Count -gt 0
        if ($usedPrivate -ne ($scenario -in @('missing', 'repair-private'))) { throw "Incorrect existing Node reuse: $scenario" }
        if ($scenario -in @('broken-private', 'repair-private')) {
            if ((Get-Content -LiteralPath (Join-Path $destination 'data/keep.txt') -Raw) -ne 'keep existing data') { throw 'Existing data was modified.' }
        }
        if ($scenario -eq 'repair-private') {
            if (@(Get-ChildItem -LiteralPath (Join-Path $destination 'runtime') -Filter '.node-backup-*' -Directory).Count -ne 1) { throw 'The old private runtime was not backed up.' }
        }
        Write-Host "PASS: Windows installer $scenario; native npm ci/build; caller environment and user data preserved."
    }
    if ((Get-FileHash -LiteralPath $native -Algorithm SHA256).Hash -ne $nodeHash) { throw 'The existing system Node binary was modified.' }
} finally {
    $env:PATH = $originalPath
    Remove-Item Function:\Invoke-WebRequest -ErrorAction SilentlyContinue
    Remove-Variable ShiritaiInstallerFixture -Scope Global -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $scratch -Recurse -Force
}
