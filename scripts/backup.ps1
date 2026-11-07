# Nightly backup of Swave's user data (pg_dump custom format), keeping the last 7 days.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1 [-Dir <folder>] [-Days 7] [-IncludeTokens]
#
# Folder: -Dir, else $env:SWAVE_BACKUP_DIR, else SWAVE_BACKUP_DIR in .env.private. Point it at a
# folder that leaves this PC, e.g. your OneDrive (C:\Users\<you>\OneDrive\SwaveBackups), so a
# dead PC doesn't take the backups with it. It must be outside the repo (personal data).
#
# Backed up: what can't be rebuilt from the scripts: events, preview attempts and the lookup
# caches that took API calls to fill. Spotify logins (refresh tokens) are left out unless
# -IncludeTokens (or SWAVE_BACKUP_TOKENS=1): backups usually sync to a cloud drive, and after a
# restore each person just reconnects Spotify once.
# NOT backed up: the music index (tracks, neighbours, LB tables, ~20 GB): rebuild it with make.
param(
    [string]$Dir = "",
    [int]$Days = 7,
    [switch]$IncludeTokens,
    [string]$Database = "swave_private"   # the private instance's database (dev: -Database swave)
)
# "Continue", not "Stop": Docker writes progress to stderr, which Windows PowerShell 5.1 turns
# into error records when output is redirected (scheduled task); success is judged by exit codes.
$ErrorActionPreference = "Continue"
"=== Swave backup {0:yyyy-MM-dd HH:mm:ss}" -f (Get-Date)
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function FromEnvFile([string]$key) {
    $f = Join-Path $repo ".env.private"
    if (-not (Test-Path $f)) { return "" }
    $line = Get-Content $f | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -Last 1
    if (-not $line) { return "" }
    return ($line -split "=", 2)[1].Trim().Trim('"')
}

if (-not $Dir) { $Dir = $env:SWAVE_BACKUP_DIR }
if (-not $Dir) { $Dir = FromEnvFile "SWAVE_BACKUP_DIR" }
if (-not $Dir) { throw "No backup folder: pass -Dir or set SWAVE_BACKUP_DIR (e.g. in .env.private)" }
$Dir = [Environment]::ExpandEnvironmentVariables($Dir)
$full = [IO.Path]::GetFullPath($Dir)
if ($full.StartsWith([IO.Path]::GetFullPath($repo), [StringComparison]::OrdinalIgnoreCase)) {
    throw "The backup folder must be outside the repo ($full)"
}
if (-not $IncludeTokens -and ($env:SWAVE_BACKUP_TOKENS -eq "1" -or (FromEnvFile "SWAVE_BACKUP_TOKENS") -eq "1")) {
    $IncludeTokens = $true
}
$offPc = $env:OneDrive -and $full.StartsWith($env:OneDrive, [StringComparison]::OrdinalIgnoreCase)
if (-not $offPc -and $full.StartsWith($env:SystemDrive, [StringComparison]::OrdinalIgnoreCase)) {
    Write-Warning "$full is on this PC's system drive and not in OneDrive: a dead PC loses these backups"
}

$tables = "events", "preview_attempts", "lb_spotify", "covers", "deezer_related",
          "lb_similar", "provider_tracks", "preview_sources", "track_meta", "previews"
if ($IncludeTokens) { $tables += "spotify_auth" }
New-Item -ItemType Directory -Force $full -ErrorAction Stop | Out-Null
$name = "swave-{0:yyyyMMdd-HHmm}.dump" -f (Get-Date)
if ($Database -ne "swave_private") { $name = "$Database-{0:yyyyMMdd-HHmm}.dump" -f (Get-Date) }
$t = ($tables | ForEach-Object { "-t $_" }) -join " "
# dump inside the container (binary-safe), then copy it out
docker compose exec -T db sh -c "pg_dump -U `$POSTGRES_USER -d $Database -Fc $t -f /tmp/$name" 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed ($LASTEXITCODE)" }
docker compose cp "db:/tmp/$name" (Join-Path $full $name) 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { throw "copy out of the container failed ($LASTEXITCODE)" }
docker compose exec -T db rm -f "/tmp/$name" 2>&1 | Out-Null
$size = [math]::Round((Get-Item (Join-Path $full $name) -ErrorAction Stop).Length / 1MB, 1)
Get-ChildItem $full -Filter "swave-*.dump" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$Days) } |
    ForEach-Object { Remove-Item $_.FullName -ErrorAction Stop; "removed old backup $($_.Name)" }
$what = $(if ($IncludeTokens) { "with Spotify logins" } else { "without Spotify logins" }) + ", database $Database"
"backup ok: $(Join-Path $full $name) ($size MB, $what)"
