# Restore Swave's user data from a backup made by scripts\backup.ps1.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restore.ps1 -File C:\SwaveBackups\swave-YYYYMMDD-HHMM.dump [-Database swave]
#
# Replaces the backed-up tables (events, Spotify logins, caches…) with the backup's contents, in
# one transaction; the music index is untouched. Stop the API first (make private-down, or
# Ctrl-C `make dev`) so nothing writes meanwhile. -Database lets you restore into a scratch
# database to inspect a backup without touching the live one.
param(
    [Parameter(Mandatory = $true)][string]$File,
    [string]$Database = ""
)
$ErrorActionPreference = "Continue"  # docker writes progress to stderr; exit codes are checked
if (-not (Test-Path $File)) { throw "no such backup: $File" }
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$name = Split-Path -Leaf $File
docker compose cp $File "db:/tmp/$name" 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { throw "copy into the container failed ($LASTEXITCODE)" }
$db = if ($Database) { $Database } else { '$POSTGRES_DB' }
docker compose exec -T db sh -c "pg_restore -U `$POSTGRES_USER -d $db --clean --if-exists --no-owner --single-transaction /tmp/$name"
$code = $LASTEXITCODE
docker compose exec -T db rm -f "/tmp/$name" 2>&1 | Out-Null
if ($code -ne 0) { throw "pg_restore failed ($code): nothing was changed (single transaction)" }
"restore ok from $File"
