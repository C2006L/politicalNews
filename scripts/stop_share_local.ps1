$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$stateFile = Join-Path $workspaceRoot 'preview\share-state.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Host 'No recorded share is running.'; return }
$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
foreach ($entry in @($state.tunnel,$state.gateway)) {
    if (-not $entry) { continue }
    try {
        $process = [Diagnostics.Process]::GetProcessById([int]$entry.id)
        if ($process.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.started) { $process.Kill() }
    } catch [ArgumentException] { }
}
Remove-Item -LiteralPath $stateFile
Write-Host 'Sharing stopped. The local reading service is still running.'
