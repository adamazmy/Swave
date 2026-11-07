# Roll the private instance back to the images that ran before the last make private-deploy.
# Swaps images only: a database migration that deploy reported is NOT undone (CLAUDE.md:
# migrations must be backward-compatible, so the previous code still works on the new schema).
. "$PSScriptRoot\privdb.ps1"
foreach ($img in "swave-api", "swave-web") {
    docker image inspect "${img}:previous" *> $null
    if ($LASTEXITCODE -ne 0) { throw "no ${img}:previous image: nothing to roll back to" }
}
"=== private-rollback {0:yyyy-MM-dd HH:mm:ss}" -f (Get-Date)
"WARNING: images only. If the last deploy printed 'DB MIGRATION RAN', that migration stays."
foreach ($img in "swave-api", "swave-web") {
    docker tag "${img}:latest" "${img}:rolledback" | Out-Null
    docker tag "${img}:previous" "${img}:latest" | Out-Null
}
$down = Get-Date
docker @Compose up -d --no-deps --no-build --force-recreate api web 2>&1 | Out-Null
if (-not (ApiHealth 180)) { throw "API not healthy after rollback: check make private-logs" }
$py = "import urllib.request,json; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10))['version'])"
$ver = (docker exec swave-api-1 python -c $py 2>&1 | Select-Object -Last 1)
"rolled back to {0} (downtime {1:n0} s); the rolled-back images are tagged :rolledback" -f $ver, ((Get-Date) - $down).TotalSeconds
