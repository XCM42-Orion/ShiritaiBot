param([string]$Installer = (Join-Path $PSScriptRoot '../install.ps1'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $Installer), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$definition = $ast.Find({ param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq 'Find-ShiritaiNode' }, $true)
if (-not $definition) { throw 'Node discovery function was not found.' }
Invoke-Expression $definition.Extent.Text

function Assert-NodeTest($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
$isWindowsHost = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
$command = if ($isWindowsHost) { 'node.exe' } else { 'node' }
$native = (Get-Command $command -CommandType Application | Select-Object -First 1).Source
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('shiritai-node-test-' + [Guid]::NewGuid().ToString('N'))
$originalPath = $env:PATH
try {
    $fixture = Join-Path $scratch ("Node user's folder & " + [char]0x4e2d + [char]0x6587)
    $null = New-Item -ItemType Directory -Path (Join-Path $fixture 'node_modules/npm/bin') -Force
    $executable = Join-Path $fixture 'node.exe'
    Copy-Item -LiteralPath $native -Destination $executable
    $resolvedExecutable = ((& $executable -p process.execPath) | Out-String).Trim()
    $npmCli = Join-Path $fixture 'node_modules/npm/bin/npm-cli.js'
    [IO.File]::WriteAllText($npmCli, "console.log('11.0.0')", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $fixture 'npm.ps1'), "throw 'Do not invoke the PowerShell npm wrapper.'")
    [IO.File]::WriteAllText((Join-Path $fixture 'npm.cmd'), "@echo off`r`nexit /b 93`r`n")
    $env:PATH = "$fixture$([IO.Path]::PathSeparator)$originalPath"

    foreach ($mode in @('Legacy', 'Standard')) {
        if ($PSVersionTable.PSVersion.Major -ge 7) { $PSNativeCommandArgumentPassing = $mode }
        $found = Find-ShiritaiNode -Candidates @($executable)
        Assert-NodeTest ($null -ne $found) "A working Node/npm pair was rejected in $mode mode."
        Assert-NodeTest ($found.Executable -eq $resolvedExecutable) 'The selected Node executable changed.'
        Assert-NodeTest ($found.NpmCli -eq (Join-Path (Split-Path -Parent $resolvedExecutable) 'node_modules/npm/bin/npm-cli.js')) 'npm did not use the selected Node distribution.'
    }

    foreach ($version in @('20.18.0', '20.19.0', '22.11.0', '22.12.0', '23.0.0', '24.0.0', '26.0.0')) {
        $shim = Join-Path $scratch ('node-' + $version + '.ps1')
        $body = "if (`$args[0] -eq '--version') { 'v$version' } else { '" + $executable.Replace("'", "''") + "' }; exit 0"
        [IO.File]::WriteAllText($shim, $body, [Text.UTF8Encoding]::new($true))
        $found = Find-ShiritaiNode -Candidates @($shim)
        $expected = $version -in @('20.19.0', '22.12.0', '24.0.0', '26.0.0')
        Assert-NodeTest (($null -ne $found) -eq $expected) "Incorrect compatibility decision for Node $version."
    }

    $bad = Join-Path $scratch 'broken-node.ps1'
    [IO.File]::WriteAllText($bad, 'exit 42')
    $found = Find-ShiritaiNode -Candidates @($bad, $executable)
    Assert-NodeTest ($null -ne $found) 'A broken first candidate hid the working Node.'
    [IO.File]::WriteAllText($npmCli, 'process.exit(42)')
    Assert-NodeTest ($null -eq (Find-ShiritaiNode -Candidates @($executable))) 'Broken npm was accepted.'
    Remove-Item -LiteralPath $npmCli
    Assert-NodeTest ($null -eq (Find-ShiritaiNode -Candidates @($executable))) 'Missing npm was accepted.'
    Write-Host 'PASS: Legacy/Standard arguments, version boundaries, native paths, shims, missing and broken npm.'
} finally {
    $env:PATH = $originalPath
    Remove-Item -LiteralPath $scratch -Recurse -Force
}
exit 0
