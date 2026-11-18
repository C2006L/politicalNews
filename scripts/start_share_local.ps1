$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$runtime = Join-Path $workspaceRoot 'preview'
$stateFile = Join-Path $runtime 'share-state.json'
$pythonPath = Join-Path $workspaceRoot '.conda\python.exe'
$tunnelPath = Join-Path $workspaceRoot 'tools\cloudflared.exe'
$healthPath = '/static/study.css'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
if (-not (Test-Path -LiteralPath $tunnelPath)) { throw 'Missing cloudflared.exe in workspace tools directory.' }
if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $existing = $null
    try { $existing = [Diagnostics.Process]::GetProcessById([int]$state.tunnel.id) } catch [ArgumentException] { }
    if ($existing -and $existing.StartTime.ToUniversalTime().Ticks.ToString() -eq $state.tunnel.started) {
        Write-Host ('Share is already running: ' + $state.url)
        return
    }
    throw 'A previous share record exists. Run stop_share_local.ps1 first, then start again.'
}
Invoke-WebRequest ('http://127.0.0.1:8004' + $healthPath) -UseBasicParsing -TimeoutSec 15 | Out-Null
$gateway = $null
try {
    Invoke-WebRequest ('http://127.0.0.1:8017' + $healthPath) -UseBasicParsing -TimeoutSec 5 | Out-Null
    throw 'Port 8017 is already occupied. Stop the previous share before starting another.'
} catch {
    if ($_.Exception.Message -like 'Port 8017*') { throw }
}
$gateway = Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','app.preview:app','--host','127.0.0.1','--port','8017' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'share-gateway.out.log') -RedirectStandardError (Join-Path $runtime 'share-gateway.err.log')
try {
    $ready = $false
    for ($i=0; $i -lt 15; $i++) {
        try { Invoke-WebRequest ('http://127.0.0.1:8017' + $healthPath) -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready=$true; break } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'Read-only sharing gateway did not start.' }
    $log = Join-Path $runtime 'share-tunnel.err.log'
    $tunnel = Start-Process -FilePath $tunnelPath -ArgumentList 'tunnel','--no-autoupdate','--url','http://127.0.0.1:8017','--protocol','http2' -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'share-tunnel.out.log') -RedirectStandardError $log
    $identity = { param($process) @{id=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks.ToString()} }
    $state = @{gateway=(& $identity $gateway); tunnel=(& $identity $tunnel); url='pending'}
    $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    for ($i=0; $i -lt 40; $i++) {
        $text = Get-Content -LiteralPath $log -Raw -ErrorAction SilentlyContinue
        if ($text -match 'https://[a-z0-9-]+\.trycloudflare\.com') {
            $state.url=$Matches[0]
            $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
            Write-Host ('Read-only share: ' + $state.url)
            Write-Host 'Keep this PC and the local reading service running. Restarting the share changes its URL.'
            return
        }
        Start-Sleep -Seconds 1
    }
    throw 'Tunnel URL not ready. Check preview/share-tunnel.err.log.'
} catch {
    if ($tunnel -and -not $tunnel.HasExited) { $tunnel.Kill() }
    if ($gateway -and -not $gateway.HasExited) { $gateway.Kill() }
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile }
    throw
}
