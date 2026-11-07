# Deploy the current checkout to the private instance (make private-deploy).
#
#  1. Refuses unless on `main` with a clean tree (FORCE=1 overrides both), so the commit it
#     prints is exactly what runs.
#  2. Backs up the private database (scripts\backup.ps1); stops if the backup fails.
#  3. Keeps the running images as swave-api:previous / swave-web:previous (make private-rollback).
#  4. Rebuilds api + web with the commit baked in, restarts only those two (db + tunnel stay up).
#  5. Health check: API healthy, /health reports the new commit, web answers, and
#     https://<domain> still redirects to the Cloudflare Access login.
#  6. Reports any database migration the new code ran at startup (schema diff before/after):
#     make private-rollback swaps images back but does NOT undo a migration, which is why
#     migrations must be backward-compatible (CLAUDE.md).
. "$PSScriptRoot\privdb.ps1"
$force = $env:FORCE -eq "1"
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$dirty = @(git status --porcelain).Count -gt 0
if (-not $force) {
    if ($branch -ne "main") { throw "on branch '$branch': deploy from main (or FORCE=1)" }
    if ($dirty) { throw "uncommitted changes: commit them first (or FORCE=1)" }
}
$commit = (git rev-parse --short HEAD).Trim()
if ($dirty) { $commit += "-dirty" }
$subject = (git log -1 --format=%s).Trim()
$t0 = Get-Date
"=== private-deploy {0} ({1}) {2:yyyy-MM-dd HH:mm:ss}" -f $commit, $branch, $t0
if ($force -and ($branch -ne "main" -or $dirty)) { "WARNING: FORCE=1: deploying from '$branch'$(if ($dirty) { ' with uncommitted changes' })" }

function Fingerprint {
    Psql $PrivateDb "SELECT table_name || '.' || column_name || ' ' || data_type FROM information_schema.columns
                     WHERE table_schema = 'public' ORDER BY 1"
}
function ApiGet([string]$url) {
    $py = "import urllib.request,sys; r=urllib.request.urlopen(sys.argv[1], timeout=10); print(r.status); print(r.read(400).decode('utf-8','replace'))"
    return docker exec swave-api-1 python -c $py $url 2>&1
}

# 1. backup (aborts the deploy on failure)
"[1/5] backup"
& "$PSScriptRoot\backup.ps1" -Database $PrivateDb
if (-not $?) { throw "backup failed: nothing deployed" }
$before = Fingerprint
$prevVersion = try { ((ApiGet "http://127.0.0.1:8000/health") | Select-Object -Skip 1 | ConvertFrom-Json).version } catch { "unknown" }

# 2. rollback point
"[2/5] keep the running images for rollback (was: $prevVersion)"
foreach ($img in "swave-api", "swave-web") {
    docker image inspect "${img}:latest" *> $null
    if ($LASTEXITCODE -eq 0) { docker tag "${img}:latest" "${img}:previous" | Out-Null }
}

# 3. build + restart api and web only
"[3/5] build $commit"
$env:SWAVE_GIT = $commit
docker @Compose build api web 2>&1 | Select-String -Pattern "error|ERROR|Built" | ForEach-Object { "  $_" }
if ($LASTEXITCODE -ne 0) { throw "build failed: the running version ($prevVersion) is untouched" }
$down = Get-Date
docker @Compose up -d --no-deps api web 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "restart failed: try make private-rollback" }

# 4. health
"[4/5] health check"
$ok = ApiHealth 180
$downtime = ((Get-Date) - $down).TotalSeconds
$problems = @()
if (-not $ok) { $problems += "API not healthy after 180 s" }
else {
    $h = ApiGet "http://127.0.0.1:8000/health"
    $ver = try { ($h | Select-Object -Skip 1 | ConvertFrom-Json).version } catch { "?" }
    if ($ver -ne $commit) { $problems += "API reports version '$ver', expected '$commit'" } else { "  API healthy, /health version $ver" }
    $w = ApiGet "http://web:3000/"
    if (($w | Select-Object -First 1) -ne "200") { $problems += "web didn't answer 200" } else { "  web answers 200" }
}
$domain = (EnvValue "WEB_ORIGINS").Split(",")[0].Trim()
if ($domain) {
    $r = curl.exe -s -m 20 -o NUL -w "%{http_code} %{redirect_url}" "$domain/"
    if ($r -match "^30[0-9] https://[^/]+\.cloudflareaccess\.com/") { "  $domain -> Cloudflare Access login (tunnel + Access OK)" }
    else { $problems += "$domain did not redirect to the Access login (got: $($r -replace '\?.*',''))" }
}
"  downtime: {0:n0} s" -f $downtime

# 5. migrations
"[5/5] database migrations"
$after = Fingerprint
$added = @($after | Where-Object { $before -notcontains $_ })
$removed = @($before | Where-Object { $after -notcontains $_ })
if ($added.Count -or $removed.Count) {
    "  *** DB MIGRATION RAN ***"
    $added | ForEach-Object { "    + $_" }
    $removed | ForEach-Object { "    - $_" }
    "  make private-rollback restores the previous images but will NOT undo this migration."
} else { "  none (schema unchanged)" }

if ($problems.Count) {
    "DEPLOY FAILED:"
    $problems | ForEach-Object { "  - $_" }
    "Roll back with: make private-rollback   (previous version: $prevVersion)"
    exit 1
}
"deployed {0} '{1}' in {2:n1} min (previous: {3})" -f $commit, $subject, ((Get-Date) - $t0).TotalMinutes, $prevVersion
