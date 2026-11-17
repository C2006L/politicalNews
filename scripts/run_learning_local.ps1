$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$env:WERSS_DATABASE_PATH = Join-Path $workspaceRoot 'werss-local\local-deploy\data\learning.snapshot.db'
$env:WERSS_SOURCE_IS_SNAPSHOT = '1'
$channelCredential = Join-Path $workspaceRoot 'werss-local\local-deploy\data\political-news-channel-key.json'
if ((Test-Path -LiteralPath $channelCredential) -and -not $env:WERSS_API_TOKEN_FILE) {
    if (-not $env:WERSS_API_URL) { $env:WERSS_API_URL = 'http://127.0.0.1:8003' }
    $env:WERSS_API_TOKEN_FILE = $channelCredential
}
$env:AUTO_SYNC_ON_STARTUP = '0'
$recoveryCredential = Join-Path $workspaceRoot 'werss-local\local-deploy\data\political-news-recovery-key.json'
if ((Test-Path -LiteralPath $recoveryCredential) -and -not $env:WERSS_RECOVERY_TOKEN_FILE) {
    if (-not $env:WERSS_API_URL) { $env:WERSS_API_URL = 'http://127.0.0.1:8003' }
    $env:WERSS_RECOVERY_TOKEN_FILE = $recoveryCredential
}
$env:BOOTSTRAP_RECENT_NEWS_ON_STARTUP = '0'
$env:SCHEDULER_ENABLED = '1'
$env:OFFICIAL_WEBSITES_ENABLED = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $workspaceRoot '.conda\python.exe'
& $pythonPath -c 'from app.database import init_db; init_db()'
if ($LASTEXITCODE -ne 0) { throw '主项目数据库初始化失败，请检查版本与备份；未启动阅读服务。' }
& $pythonPath -c 'from app.materials import sync_materials; print(sync_materials())'
if ($LASTEXITCODE -ne 0) { Write-Warning '公众号资料暂未导入，请在资料导入页检查并重试；继续启动已有资料阅读服务。' }
& $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port 8004
