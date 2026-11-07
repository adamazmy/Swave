# One-time: give the private instance its own database (see docs/PRIVATE.md).
#
#   scripts\private-db-setup.ps1 -Create         role + database + caches + music index (site keeps
#                                                running on the old database meanwhile)
#   scripts\private-db-setup.ps1 -MoveUsers      copy the Access users' rows (emails) from dev into
#                                                the private DB, verify counts (site must be stopped)
#   scripts\private-db-setup.ps1 -CleanupDev     delete those rows from dev, once the private
#                                                instance is verified on its own database
#
# The role `swave_private` is not a superuser and cannot connect to the dev database `swave`.
# Its password is generated into .env.private (PRIVATE_DB_PASSWORD) and never printed.
param(
    [switch]$Create,
    [switch]$MoveUsers,
    [switch]$CleanupDev,
    [string]$Db = "",
    [string[]]$IndexTables2 = @()
)
. "$PSScriptRoot\privdb.ps1"
if (-not $Db) { $Db = $PrivateDb }
$u = PgUser
$emailRows = "user_id LIKE '%@%'"   # Cloudflare Access users; dev rows are 'local' or NULL

if ($Create) {
    "=== create {0}" -f $Db
    $pw = EnvValue "PRIVATE_DB_PASSWORD"
    if (-not $pw) {
        $bytes = New-Object byte[] 24
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $pw = ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x')
        Add-Content -Encoding utf8 (Join-Path $Repo ".env.private") "`n# Private database role (scripts/private-db-setup.ps1)`nPRIVATE_DB_PASSWORD=$pw"
        "  generated PRIVATE_DB_PASSWORD into .env.private (not shown)"
    }
    Psql "swave" @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$PrivateRole') THEN
    CREATE ROLE $PrivateRole LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END `$`$;
ALTER ROLE $PrivateRole PASSWORD '$pw';
REVOKE CONNECT ON DATABASE swave FROM PUBLIC;
"@ | Out-Null
    if (-not ((Psql "swave" "SELECT 1 FROM pg_database WHERE datname = '$Db'") -join "")) {
        Psql "swave" "CREATE DATABASE $Db OWNER $PrivateRole;" | Out-Null
        "  created database $Db (owner $PrivateRole)"
    }
    Psql $Db "CREATE EXTENSION IF NOT EXISTS pg_trgm; REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT ALL ON SCHEMA public TO $PrivateRole;" | Out-Null

    "  copying caches: $($CacheTables -join ', ')"
    $exists = Psql $Db ("SELECT string_agg(tablename, ',') FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('" + ($CacheTables -join "','") + "')")
    if (($exists -join "")) { throw "cache tables already exist in ${Db}: $exists (setup already ran?)" }
    $dump = "pg_dump -U $u -d swave --no-owner --no-privileges " + (($CacheTables | ForEach-Object { "-t public.$_" }) -join " ")
    InContainer "$dump | psql -U $u -d $Db -v ON_ERROR_STOP=1 -X -q >/dev/null" | Out-Null
    foreach ($t in $CacheTables) {
        $a = (Psql "swave" "SELECT count(*) FROM public.$t") -join ""; $b = (Psql $Db "SELECT count(*) FROM public.$t") -join ""
        if ($a -ne $b) { throw "cache ${t}: dev $a vs private $b" }
    }
    OwnEverything $Db
    "  music index (first sync, no API on this database yet) ..."
    $syncArgs = @{ Db = $Db; NoApi = $true }
    if ($IndexTables2) { $syncArgs.Tables = $IndexTables2 }
    & "$PSScriptRoot\private-index-sync.ps1" @syncArgs
    if (-not $?) { throw "index sync failed" }
    "create done"
}

if ($MoveUsers) {
    "=== move Access users' data into {0}" -f $Db
    $api = docker inspect -f "{{.State.Running}}" swave-api-1 2>$null
    if ($api -eq "true" -and $Db -eq $PrivateDb) { throw "stop the private API first (make private-down): its users' rows must not change during the move" }
    $schema = "pg_dump -U $u -d swave --no-owner --no-privileges --schema-only " + (($UserTables | ForEach-Object { "-t public.$_" }) -join " ")
    $have = Psql $Db ("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('" + ($UserTables -join "','") + "')")
    if (($have -join "") -ne "0") { throw "user tables already exist in $Db (moved before?)" }
    InContainer "$schema | psql -U $u -d $Db -v ON_ERROR_STOP=1 -X -q >/dev/null" | Out-Null
    foreach ($t in $UserTables) {
        # bash double quotes around the SQL (it contains '%@%'); sent on stdin, so no Windows quoting
        InContainer ("psql -U $u -d swave -X -q -v ON_ERROR_STOP=1 -c `"COPY (SELECT * FROM public.$t WHERE $emailRows) TO STDOUT`" | " +
                     "psql -U $u -d $Db -X -q -v ON_ERROR_STOP=1 -c `"COPY public.$t FROM STDIN`"") | Out-Null
        $a = (Psql "swave" "SELECT count(*) FROM public.$t WHERE $emailRows") -join ""
        $b = (Psql $Db "SELECT count(*) FROM public.$t") -join ""
        if ($a -ne $b) { throw "${t}: dev has $a Access-user rows, private got $b" }
        "  $t : $b rows"
    }
    # bigserial ids were copied as-is: move the sequences past them
    Psql $Db "SELECT setval(pg_get_serial_sequence('public.events', 'id'), greatest((SELECT max(id) FROM public.events), 1));
              SELECT setval(pg_get_serial_sequence('public.preview_attempts', 'id'), greatest((SELECT max(id) FROM public.preview_attempts), 1));" | Out-Null
    OwnEverything $Db
    "move done: start the private instance (make private-up), verify, then -CleanupDev"
}

if ($CleanupDev) {
    "=== delete the moved Access users' rows from dev (swave)"
    foreach ($t in $UserTables) {
        $a = (Psql "swave" "SELECT count(*) FROM public.$t WHERE $emailRows") -join ""
        $b = (Psql $Db "SELECT count(*) FROM public.$t WHERE $emailRows") -join ""
        if ([int]$b -lt [int]$a) { throw "${t}: private has fewer rows ($b) than dev ($a): not deleting" }
        $n = (Psql "swave" "WITH d AS (DELETE FROM public.$t WHERE $emailRows RETURNING 1) SELECT count(*) FROM d") -join ""
        "  ${t}: deleted $n rows from dev"
    }
}
