param(
    [string]$OutputPath = (Join-Path $env:TEMP "read-image-clipboard.png")
)

$ErrorActionPreference = "Stop"

if ([System.Threading.Thread]::CurrentThread.ApartmentState -ne "STA") {
    & powershell -NoProfile -STA -ExecutionPolicy Bypass -File $PSCommandPath -OutputPath $OutputPath
    exit $LASTEXITCODE
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$image = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $image) {
    throw "Clipboard does not contain an image."
}

$fullPath = [System.IO.Path]::GetFullPath($OutputPath)
$directory = [System.IO.Path]::GetDirectoryName($fullPath)
if ($directory) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$image.Save($fullPath, [System.Drawing.Imaging.ImageFormat]::Png)
$image.Dispose()

Write-Output $fullPath
