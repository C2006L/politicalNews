$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$deployRoot = Join-Path $workspaceRoot 'werss-local\local-deploy'
$sourcePatch = Join-Path $projectRoot 'integrations\werss\weread_mp.py'
$targetPatch = Join-Path $deployRoot 'patches\weread_mp.py'
$composePath = Join-Path $deployRoot 'compose.yaml'
$containerName = 'political-news-werss'
$image = & docker inspect --format '{{.Config.Image}}' $containerName
if ($LASTEXITCODE -ne 0 -or $image -ne 'ghcr.io/rachelos/we-mp-rss@sha256:af771f21b3f7958a5dea16911fba050a6d7b92eac2fb2499c467c1b11f07ef34') {
    throw 'The tested WeRSS image is not running. Review compatibility before installing.'
}
if (-not (Test-Path -LiteralPath $targetPatch) -or -not (Test-Path -LiteralPath $sourcePatch)) {
    throw 'The source or deployed adapter is missing.'
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path $workspaceRoot "preview\history-adapter-$stamp"
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$backupPatch = Join-Path $backupRoot 'weread_mp.before.py'
Copy-Item -LiteralPath $targetPatch -Destination $backupPatch
$backupProgram = @'
import os, sqlite3, sys
from contextlib import closing
target='/app/data/history-adapter-backups/before-'+sys.argv[1]+'.db'
os.makedirs(os.path.dirname(target),exist_ok=True)
with closing(sqlite3.connect('/app/data/db.db')) as source,closing(sqlite3.connect(target)) as backup:source.backup(backup)
with closing(sqlite3.connect(target)) as backup:
    if backup.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Backup check failed')
print('WeRSS database backup verified.')
'@
$backupProgram | & docker exec -i -w /app $containerName /usr/local/bin/python3 - $stamp
if ($LASTEXITCODE -ne 0) { throw 'Database backup failed; adapter was not changed.' }
try {
    Copy-Item -LiteralPath $sourcePatch -Destination $targetPatch
    & docker compose -f $composePath restart werss
    if ($LASTEXITCODE -ne 0) { throw 'WeRSS restart failed.' }
    $smokeProgram = @'
import contextlib,io
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
    from core.wx.model.weread_mp import ADAPTER_VERSION
assert ADAPTER_VERSION=='history-90-v1'
print('History adapter loaded: '+ADAPTER_VERSION)
'@
    $smokeProgram | & docker exec -i -w /app $containerName /app/env_x86_64/bin/python3 -
    if ($LASTEXITCODE -ne 0) { throw 'Adapter import check failed.' }
    Write-Host "History adapter installed. Rollback copy: $backupPatch"
} catch {
    Copy-Item -LiteralPath $backupPatch -Destination $targetPatch
    & docker compose -f $composePath restart werss
    throw 'History adapter installation failed; previous patch restored. Inspect local logs.'
}
