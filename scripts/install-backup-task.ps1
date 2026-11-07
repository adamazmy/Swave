# Registers the nightly backup (03:30) as a Windows scheduled task for the current user.
# The folder comes from SWAVE_BACKUP_DIR in .env.private (see scripts\backup.ps1).
# If the PC is off or asleep at 03:30, the backup runs as soon as it's back (StartWhenAvailable).
# Run once:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-backup-task.ps1
# Remove:    Unregister-ScheduledTask -TaskName "Swave nightly backup" -Confirm:$false
param([string]$Time = "03:30")
$ErrorActionPreference = "Stop"
$name = "Swave nightly backup"
$script = Join-Path $PSScriptRoot "backup.ps1"
$logDir = Join-Path $env:LOCALAPPDATA "Swave"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "backup.log"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -Command `"try { & '$script' 2>&1 | " +
               "Out-File -Append -Encoding utf8 '$log' } catch { `$_ | Out-File -Append -Encoding utf8 '$log'; exit 1 }`"") `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Swave: back up user data to SWAVE_BACKUP_DIR (scripts\backup.ps1), 7-day retention" -Force | Out-Null
$info = Get-ScheduledTask -TaskName $name | Get-ScheduledTaskInfo   # throws if registration failed
"Scheduled '$name' daily at $Time; next run $($info.NextRunTime); log: $log"
"(Docker must be running at that time; a missed run starts when the PC is back on.)"
