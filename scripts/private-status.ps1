# One-screen health report for the private instance (make private-status). Read-only.
#
#   containers   state, health, restart policy and uptime of db / api / web / cloudflared
#   tunnel       connections cloudflared registered since it started, and whether
#                https://<domain> answers with the Cloudflare Access login (= tunnel + Access OK;
#                Cloudflare error 1033 / HTTP 530 = tunnel not connected)
#   deployed     the commit the API reports in /health vs this checkout's HEAD
#   backup       newest dump in the backup folder, and the nightly task's last/next run
#   autostart    whether Docker Desktop starts at Windows sign-in (without it nothing comes back
#                after a reboot: the containers' restart policy only applies once Docker runs)
#
# Exit code 1 when something needs attention, so it can be scripted.
. "$PSScriptRoot\privdb.ps1"
$problems = @()
function Line([string]$label, [string]$text) { "  {0,-13} {1}" -f $label, $text }
function Ago([datetime]$t) {
    $d = (Get-Date) - $t
    if ($d.TotalMinutes -lt 1) { return "just now" }
    if ($d.TotalHours -lt 1) { return "{0:n0} min ago" -f $d.TotalMinutes }
    if ($d.TotalDays -lt 2) { return "{0:n1} h ago" -f $d.TotalHours }
    return "{0:n1} days ago" -f $d.TotalDays
}

"=== Swave private status {0:yyyy-MM-dd HH:mm:ss}" -f (Get-Date)

# ── containers ──
"containers"
$null = docker info --format "{{.ServerVersion}}" 2>$null
if ($LASTEXITCODE -ne 0) {
    Line "docker" "NOT RUNNING: start Docker Desktop"
    $problems += "Docker is not running"
} else {
    foreach ($svc in "db", "api", "web", "cloudflared") {
        $name = "swave-$svc-1"
        $fmt = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.HostConfig.RestartPolicy.Name}}|{{.State.StartedAt}}|{{.RestartCount}}"
        $raw = docker inspect -f $fmt $name 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) {
            Line $svc "MISSING (make private-up)"
            $problems += "$svc container missing"
            continue
        }
        $state, $health, $policy, $started, $restarts = "$raw".Split("|")
        $since = try { Ago ([datetime]::Parse($started).ToLocalTime()) } catch { "?" }
        $text = $state
        if ($health) { $text += " ($health)" }
        $text += ", up since $since, restart=$policy"
        if ([int]$restarts -gt 0) { $text += ", $restarts restarts" }
        Line $svc $text
        if ($state -ne "running") { $problems += "$svc is $state" }
        elseif ($health -and $health -ne "healthy") { $problems += "$svc is $health" }
        if ($policy -ne "unless-stopped" -and $policy -ne "always") {
            $problems += "$svc won't come back by itself after a restart (restart=$policy)"
        }
    }
}

# ── tunnel ──
"tunnel"
$started = docker inspect -f "{{.State.StartedAt}}" swave-cloudflared-1 2>$null
if ($LASTEXITCODE -eq 0 -and $started) {
    $log = @(docker logs --since $started swave-cloudflared-1 2>&1 | ForEach-Object { "$_" })
    $reg = @($log | Where-Object { $_ -match "Registered tunnel connection" } |
             ForEach-Object { if ($_ -match "location=(\S+)") { $Matches[1] } })
    $lost = @($log | Where-Object { $_ -match "Unregistered tunnel connection|Connection terminated|Lost connection" }).Count
    $where = if ($reg.Count) { " (" + (($reg | Select-Object -Unique) -join ", ") + ")" } else { "" }
    Line "connections" ("{0} registered since start{1}{2}" -f $reg.Count, $where, $(if ($lost) { ", $lost dropped" } else { "" }))
    if ($reg.Count -eq 0) { $problems += "cloudflared has no tunnel connection" }
} else {
    Line "connections" "cloudflared not running"
}
$domain = (EnvValue "WEB_ORIGINS").Split(",")[0].Trim()
if ($domain) {
    $r = curl.exe -s -m 20 -o NUL -w "%{http_code} %{redirect_url}" "$domain/"
    if ($r -match "^30[0-9] https://[^/]+\.cloudflareaccess\.com/") { Line "public" "$domain -> Cloudflare Access login (reachable)" }
    elseif ($r -match "^530") { Line "public" "$domain -> 530 (Cloudflare 1033: tunnel not connected)"; $problems += "$domain unreachable (tunnel)" }
    else { Line "public" "$domain -> $($r -replace '\?.*','')"; $problems += "$domain did not answer with the Access login" }
} else {
    Line "public" "WEB_ORIGINS not set in .env.private: skipped"
}

# ── deployed commit ──
"deployed"
$head = (git rev-parse --short HEAD).Trim()
$py = "import urllib.request,json; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10)).get('version'))"
$ver = docker exec swave-api-1 python -c $py 2>$null
if ($LASTEXITCODE -eq 0 -and $ver) {
    $ver = "$ver".Trim()
    $subject = git log -1 --format=%s $ver 2>$null
    $text = "$ver" + $(if ($subject) { " '$subject'" } else { "" })
    if ($ver -eq $head) { $text += " (= HEAD)" } else { $text += " (HEAD is ${head}: not deployed yet, or another branch)" }
    Line "commit" $text
} else {
    Line "commit" "API not answering /health"
    $problems += "API /health not answering"
}

# ── backups ──
"backup"
$dir = $env:SWAVE_BACKUP_DIR
if (-not $dir) { $dir = EnvValue "SWAVE_BACKUP_DIR" }
if ($dir) {
    $dir = [Environment]::ExpandEnvironmentVariables($dir)
    $last = Get-ChildItem -Path $dir -Filter "swave-*.dump" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($last) {
        Line "last" ("{0} ({1}, {2:n1} MB)" -f $last.Name, (Ago $last.LastWriteTime), ($last.Length / 1MB))
        if (((Get-Date) - $last.LastWriteTime).TotalHours -gt 36) { $problems += "last backup is over 36 h old" }
    } else {
        Line "last" "none in $dir"
        $problems += "no backup found"
    }
} else {
    Line "last" "SWAVE_BACKUP_DIR not set"
}
$task = Get-ScheduledTask -TaskName "Swave nightly backup" -ErrorAction SilentlyContinue
if ($task) {
    $ti = $task | Get-ScheduledTaskInfo
    $res = if ($ti.LastTaskResult -eq 0) { "ok" } else { "result $($ti.LastTaskResult)" }
    Line "nightly task" ("last run {0:yyyy-MM-dd HH:mm} ({1}), next {2:yyyy-MM-dd HH:mm}" -f $ti.LastRunTime, $res, $ti.NextRunTime)
} else {
    Line "nightly task" "not installed (scripts\install-backup-task.ps1)"
}

# ── Docker Desktop autostart ──
"autostart"
$auto = $null
$store = Join-Path $env:APPDATA "Docker\settings-store.json"
if (Test-Path $store) { $auto = (Get-Content $store -Raw | ConvertFrom-Json).AutoStart }
$approved = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" -ErrorAction SilentlyContinue)."Docker Desktop"
$winOff = $approved -and ($approved[0] % 2 -eq 1)   # odd first byte = disabled in Task Manager > Startup apps
if ($auto -eq $true -and -not $winOff) {
    Line "docker" "starts at sign-in"
} else {
    $why = @()
    if ($auto -ne $true) { $why += "Docker Desktop > Settings > General > 'Start Docker Desktop when you sign in' is off" }
    if ($winOff) { $why += "disabled in Task Manager > Startup apps" }
    Line "docker" ("does NOT start at sign-in: " + ($why -join "; "))
    $problems += "Docker Desktop doesn't start at sign-in: after a reboot swave.party stays down until you open it"
}

""
if ($problems.Count) {
    "NEEDS ATTENTION"
    $problems | ForEach-Object { "  - $_" }
    exit 1
}
"all good"
