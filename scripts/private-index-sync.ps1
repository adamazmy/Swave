# Copy the dev music index (database `swave`) into the private database, with minimal downtime.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\private-index-sync.ps1 [-NoApi]
#   (make private-index-sync)
#
# 1. Load the index tables into schema `staging` of the private DB while the private API keeps
#    serving (pg_dump | psql inside the db container: no intermediate file, ~11 GB, 15-30 min).
# 2. Check row counts against dev.
# 3. Stop the private API, swap staging <-> live in ONE transaction, recreate the all_tracks
#    view, start the API, wait until healthy: the measured downtime is printed.
# 4. Drop the replaced tables (only after the API is healthy again).
# -NoApi: don't stop/start the API (first setup, before the API uses this database).
# Disk: the new copy sits beside the old one until step 4 (~11 GB extra, temporarily).
param(
    [string]$Db = "",
    [string[]]$Tables = @(),
    [switch]$NoApi
)
. "$PSScriptRoot\privdb.ps1"
if (-not $Db) { $Db = $PrivateDb }
if (-not $Tables) { $Tables = $IndexTables }
$u = PgUser
$t0 = Get-Date
"=== private-index-sync {0:yyyy-MM-dd HH:mm:ss}: {1} table(s) -> {2}" -f $t0, $Tables.Count, $Db

# 1. staging copy, while the API serves
Psql $Db "CREATE EXTENSION IF NOT EXISTS pg_trgm; DROP SCHEMA IF EXISTS staging CASCADE; CREATE SCHEMA staging;" | Out-Null
$alt = ($Tables -join "|")
$sed = "sed -E '/^(CREATE|ALTER|COPY|SELECT pg_catalog\.setval)/ s/public\.($alt)\b/staging.\1/g'"
$dump = "pg_dump -U $u -d swave --no-owner --no-privileges " + (($Tables | ForEach-Object { "-t public.$_" }) -join " ")
"loading into staging (the private site keeps serving) ..."
InContainer "$dump | $sed | psql -U $u -d $Db -v ON_ERROR_STOP=1 -X -q >/dev/null" | Out-Null
"loaded in {0:n1} min" -f ((Get-Date) - $t0).TotalMinutes

# 2. verify
foreach ($t in $Tables) {
    $a = (Psql "swave" "SELECT count(*) FROM public.$t") -join ""
    $b = (Psql $Db "SELECT count(*) FROM staging.$t") -join ""
    if ($a -ne $b) { throw "row count mismatch for ${t}: dev $a, staging $b (nothing swapped)" }
    "  $t : $a rows"
}

# 3. swap in one transaction (API stopped only for this)
$viewSql = [regex]::Match((Get-Content -Raw "$Repo\api\swave\lb.py"), 'VIEW = """(.*?)"""', "Singleline").Groups[1].Value
if (-not $viewSql) { throw "couldn't read lb.VIEW from api/swave/lb.py" }
$moves = ($Tables | ForEach-Object {
    "ALTER TABLE IF EXISTS public.$_ SET SCHEMA retired; ALTER TABLE staging.$_ SET SCHEMA public; ALTER TABLE public.$_ OWNER TO $PrivateRole;"
}) -join "`n"
$swap = @"
DROP SCHEMA IF EXISTS retired CASCADE;
BEGIN;
CREATE SCHEMA retired;
DROP VIEW IF EXISTS public.all_tracks;
$moves
SET search_path = public;
$viewSql
ALTER VIEW public.all_tracks OWNER TO $PrivateRole;
COMMIT;
"@
$down = $null
if (-not $NoApi) {
    $down = Get-Date
    docker @Compose stop api 2>&1 | Out-Null
}
Psql $Db $swap | Out-Null
if (-not $NoApi) {
    docker @Compose start api 2>&1 | Out-Null
    if (-not (ApiHealth 180)) { throw "the private API didn't become healthy after the swap: check make private-logs" }
    "swap done; private API downtime: {0:n0} s" -f ((Get-Date) - $down).TotalSeconds
} else {
    "swap done (API not running on this database yet)"
}

# 4. free the old copy
Psql $Db "DROP SCHEMA IF EXISTS retired CASCADE; DROP SCHEMA IF EXISTS staging CASCADE;" | Out-Null
"private-index-sync done in {0:n1} min" -f ((Get-Date) - $t0).TotalMinutes
