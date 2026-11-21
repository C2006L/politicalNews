param(
    [int]$Days = 90,
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path (Split-Path -Parent $projectRoot) '.conda\python.exe'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot ('dist\StaticReading-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Output directory already exists. Use a new directory to keep the previous export.' }
$site = Join-Path $OutputDirectory 'site'
$upload = Join-Path $OutputDirectory 'political-news-upload'
$env:PYTHONIOENCODING = 'utf-8'
& $pythonPath (Join-Path $PSScriptRoot 'export_static.py') --days $Days --output $site
if ($LASTEXITCODE -ne 0) { throw 'Export failed. Nothing was uploaded.' }
New-Item -ItemType Directory -Path $upload -Force | Out-Null
foreach ($name in 'static_release.py','serve_static.py','install_static.py') {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $upload $name)
}
& $pythonPath (Join-Path $PSScriptRoot 'static_release.py') package --site $site --output (Join-Path $upload 'site.tar.gz')
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed. Nothing was uploaded.' }
Write-Host ('Local preview: & "' + $pythonPath + '" "' + (Join-Path $PSScriptRoot 'serve_static.py') + '" --directory "' + $site + '" --port 8018')
Write-Host ('Upload folder: ' + $upload)
Write-Host 'No network connection or server upload was performed.'
