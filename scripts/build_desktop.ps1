param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & $Python -m PyInstaller --noconfirm StudyRoom.spec
    if ($LASTEXITCODE -ne 0) { throw 'Windows application build failed.' }
    Copy-Item -LiteralPath 'docs\DESKTOP_GUIDE.txt' -Destination 'dist\StudyRoom\使用说明.txt'
    Compress-Archive -LiteralPath 'dist\StudyRoom' -DestinationPath 'dist\StudyRoom-Windows-x64.zip' -Force
    Write-Output 'Created dist\StudyRoom-Windows-x64.zip'
} finally { Pop-Location }
