$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$env:WERSS_DATABASE_PATH = Join-Path $workspaceRoot 'werss-local\local-deploy\data\db.db'
$env:AUTO_SYNC_ON_STARTUP = '0'
$env:BOOTSTRAP_RECENT_NEWS_ON_STARTUP = '0'
$env:SCHEDULER_ENABLED = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $workspaceRoot '.conda\python.exe'
& $pythonPath -c 'from app.database import init_db; from app.materials import sync_materials; init_db(); print(sync_materials())'
if ($LASTEXITCODE -ne 0) { throw '公众号资料首次导入失败，请检查 WeRSS 数据目录。' }
& $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port 8004
