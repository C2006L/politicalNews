param(
    [Parameter(Mandatory=$true)][string]$SiteDirectory,
    [string]$SshHost = 'tx',
    [string]$RemoteRoot = '/home/ubuntu/lcz/political-news-static',
    [int]$Port = 3011
)
# Only runs when the user invokes it. export_static.ps1 never calls this script.
$ErrorActionPreference = 'Stop'
if ($SshHost -notmatch '^[a-zA-Z0-9_.@-]+$' -or $SshHost.StartsWith('-')) { throw 'Invalid SSH host alias.' }
if ($RemoteRoot -notmatch '^/home/ubuntu/lcz/[a-zA-Z0-9_-]+$') { throw 'Remote root must be one independent directory under /home/ubuntu/lcz.' }
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Invalid nonprivileged port.' }
$SiteDirectory = (Resolve-Path -LiteralPath $SiteDirectory).Path
$pythonPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) '.conda\python.exe'
$work = Join-Path (Split-Path -Parent $SiteDirectory) ('publish-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $work | Out-Null
$remoteUpload = '/home/ubuntu/lcz/political-news-upload'
$previous = Join-Path $work 'previous.json'
$response = & ssh $SshHost "if test -f '$RemoteRoot/current/manifest.json'; then cat '$RemoteRoot/current/manifest.json'; else printf '{}'; fi"
if ($LASTEXITCODE -ne 0) { throw 'Could not read remote version. No upload was attempted.' }
[IO.File]::WriteAllText($previous, ($response -join "`n"), [Text.UTF8Encoding]::new($false))
$bundle = Join-Path $work 'site.tar.gz'
& $pythonPath (Join-Path $PSScriptRoot 'static_release.py') package --site $SiteDirectory --previous $previous --output $bundle
if ($LASTEXITCODE -ne 0) { throw 'Incremental package validation failed.' }
& ssh $SshHost "mkdir -p '$remoteUpload'"
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the dedicated upload directory.' }
$files = @($bundle) + @('static_release.py','serve_static.py','install_static.py' | ForEach-Object { Join-Path $PSScriptRoot $_ })
& scp @files "${SshHost}:${remoteUpload}/"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Published version was not switched.' }
& ssh $SshHost "python3 '$remoteUpload/install_static.py' --root '$RemoteRoot' --port $Port --bundle '$remoteUpload/site.tar.gz'"
if ($LASTEXITCODE -ne 0) { throw 'Server validation/startup failed. Check the reported reason before retrying.' }
Write-Host ('Published on port ' + $Port + '. Open your server IP with this port.')
