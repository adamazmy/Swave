# Nightly backup of Swave's user data (pg_dump custom format), keeping the last 7 days.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1 [-Dir C:\SwaveBackups] [-Days 7]
#
# Backed up: what can't be rebuilt from the scripts: events, preview attempts, Spotify logins
# (refresh tokens: keep the folder private), and the lookup caches that took API calls to fill.
# NOT backed up: the music index (tracks, neighbours, LB tables, ~20 GB): rebuild it with make.
# The folder must be outside the repo (it holds personal data and tokens).
param(
    [string]$Dir = "C:\SwaveBackups",
    [int]$Days = 7
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$tables = "events", "preview_attempts", "spotify_auth", "lb_spotify", "covers", "deezer_related",
          "lb_similar", "provider_tracks", "preview_sources", "track_meta", "previews"
New-Item -ItemType Directory -Force $Dir | Out-Null
$name = "swave-{0:yyyyMMdd-HHmm}.dump" -f (Get-Date)
$t = ($tables | ForEach-Object { "-t $_" }) -join " "
# dump inside the container (binary-safe), then copy it out
docker compose exec -T db sh -c "pg_dump -U `$POSTGRES_USER -d `$POSTGRES_DB -Fc $t -f /tmp/$name"
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed ($LASTEXITCODE)" }
docker compose cp "db:/tmp/$name" (Join-Path $Dir $name)
if ($LASTEXITCODE -ne 0) { throw "copy out of the container failed ($LASTEXITCODE)" }
docker compose exec -T db rm -f "/tmp/$name" | Out-Null
$size = [math]::Round((Get-Item (Join-Path $Dir $name)).Length / 1MB, 1)
Get-ChildItem $Dir -Filter "swave-*.dump" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$Days) } |
    ForEach-Object { Remove-Item $_.FullName; "removed old backup $($_.Name)" }
"backup ok: $(Join-Path $Dir $name) ($size MB)"
