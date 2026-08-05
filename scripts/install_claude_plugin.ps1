param(
    [string]$Target = (Join-Path $HOME ".claude\skills\omnimodal")
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$target = [System.IO.Path]::GetFullPath($Target)
$skillsRoot = [System.IO.Path]::GetFullPath((Join-Path $HOME ".claude\skills"))

if (-not $target.StartsWith($skillsRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Target must be under $skillsRoot"
}

New-Item -ItemType Directory -Path $target -Force | Out-Null

$sourceEnv = Join-Path $repo ".env"
$targetEnv = Join-Path $target ".env"
$envBackup = Join-Path $env:TEMP "omnimodal-clipboard-env-backup"
if ((Test-Path -LiteralPath $targetEnv) -and -not (Test-Path -LiteralPath $sourceEnv)) {
    Copy-Item -LiteralPath $targetEnv -Destination $envBackup -Force
}

robocopy $repo $target /MIR /XD .git .venv .mypy_cache .pytest_cache .ruff_cache __pycache__ /XF *.egg-info | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

if (Test-Path -LiteralPath $sourceEnv) {
    Copy-Item -LiteralPath $sourceEnv -Destination $targetEnv -Force
}
elseif (Test-Path -LiteralPath $envBackup) {
    Copy-Item -LiteralPath $envBackup -Destination $targetEnv -Force
}

Write-Output "Installed omnimodal plugin to: $target"
Write-Output "Please close Claude Code before running this script for upgrades."
Write-Output "Restart Claude Code to load the plugin."
