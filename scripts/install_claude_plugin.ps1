param(
    [string]$Target = (Join-Path $HOME ".claude\skills\read-image")
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$target = [System.IO.Path]::GetFullPath($Target)
$skillsRoot = [System.IO.Path]::GetFullPath((Join-Path $HOME ".claude\skills"))

if (-not $target.StartsWith($skillsRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Target must be under $skillsRoot"
}

New-Item -ItemType Directory -Path $target -Force | Out-Null

$exclude = @(
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "*.egg-info"
)

$items = Get-ChildItem -LiteralPath $repo -Force | Where-Object {
    $name = $_.Name
    -not ($exclude | Where-Object { $name -like $_ })
}

foreach ($item in $items) {
    Copy-Item -LiteralPath $item.FullName -Destination $target -Recurse -Force
}

Write-Output "Installed read-image plugin to: $target"
Write-Output "Restart Claude Code to load the plugin."
