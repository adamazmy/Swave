# Shared helpers for the private-database scripts (dot-sourced: . "$PSScriptRoot\privdb.ps1").
# The private instance has its own database `swave_private` (owner/login role swave_private, not a
# superuser) in the same `db` container as development (`swave`). Admin work runs as the dev
# superuser inside the container; SQL goes in on stdin, so secrets never appear on a command line.

# "Continue": Docker/psql write notices to stderr, which Windows PowerShell 5.1 would turn into
# terminating errors when redirected; every native call is checked by exit code instead.
$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

$PrivateDb = "swave_private"
$PrivateRole = "swave_private"
# The music index the app reads (copied from dev by private-index-sync; NOT eval/build tables).
$IndexTables = "tracks", "index_meta", "neighbours", "lb_tracks", "neighbours_lb", "lb_meta",
               "artist_tracks", "artist_styles"
# Shared, non-personal caches (copied once at setup, then grow separately).
$CacheTables = "lb_spotify", "covers", "deezer_related", "lb_similar", "provider_tracks",
               "preview_sources", "track_meta", "previews"
# Personal data, per user_id (moved from dev at setup: Access email users only).
$UserTables = "events", "preview_attempts", "spotify_auth"
$Compose = @("compose", "-f", "docker-compose.yml", "-f", "docker-compose.private.yml", "--env-file", ".env.private")

function EnvValue([string]$key) {
    $f = Join-Path $Repo ".env.private"
    if (-not (Test-Path $f)) { return "" }
    $line = Get-Content $f | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -Last 1
    if (-not $line) { return "" }
    return ($line -split "=", 2)[1].Trim().Trim('"')
}

function PgUser { $u = EnvValue "POSTGRES_USER"; if ($u) { $u } else { "swave" } }

function Psql([string]$db, [string]$sql) {
    # Runs SQL (stdin) as the dev superuser; returns trimmed output lines; throws on error.
    $out = $sql | docker compose exec -T db psql -U (PgUser) -d $db -v ON_ERROR_STOP=1 -X -q -tA 2>&1
    $code = $LASTEXITCODE
    $text = ($out | ForEach-Object { "$_" }) | Where-Object { $_ -ne "" }
    if ($code -ne 0) { throw "psql ($db) failed ($code): $($text -join ' | ')" }
    return $text
}

function InContainer([string]$cmd) {
    # Runs a shell pipeline inside the db container (e.g. pg_dump | psql); throws on failure.
    # The script goes in on stdin: Windows PowerShell 5.1 mangles quotes in native arguments,
    # and its pipe adds CRLF line ends, which `tr` strips before bash reads the script.
    $out = ($cmd + "`n") | docker compose exec -T db sh -c 'tr -d \\r | bash -o pipefail -s' 2>&1
    $code = $LASTEXITCODE
    $text = ($out | ForEach-Object { "$_" }) | Where-Object { $_ -ne "" }
    if ($code -ne 0) { throw "db container command failed ($code): $($text -join ' | ')" }
    return $text
}

function OwnEverything([string]$db, [string]$schema = "public") {
    # Hand every table, sequence and view in `schema` to the private role (the API runs
    # CREATE/ALTER ... IF NOT EXISTS at startup, which needs ownership).
    Psql $db @"
DO `$`$ DECLARE r record; BEGIN
  FOR r IN SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = '$schema' AND c.relkind IN ('r','S','v','m') LOOP
    EXECUTE format('ALTER %s %I.%I OWNER TO $PrivateRole',
      CASE r.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
      '$schema', r.relname);
  END LOOP;
END `$`$;
"@ | Out-Null
}

function ApiHealth([int]$timeoutSec = 120) {
    # Waits for the private API container to report healthy; returns $true/$false.
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $s = docker inspect -f "{{.State.Health.Status}}" swave-api-1 2>$null
        if ($s -eq "healthy") { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}
