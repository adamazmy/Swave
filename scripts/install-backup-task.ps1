# Registers the nightly backup (03:30) as a Windows scheduled task for the current user.
# The folder comes from SWAVE_BACKUP_DIR in .env.private (see scripts\backup.ps1).
# Run once:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-backup-task.ps1
# Remove:    schtasks /Delete /TN "Swave nightly backup" /F
param([string]$Time = "03:30")
$script = Join-Path $PSScriptRoot "backup.ps1"
$logDir = Join-Path $env:LOCALAPPDATA "Swave"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "backup.log"
$cmd = "powershell -NoProfile -ExecutionPolicy Bypass -Command `"& '$script' *>> '$log'`""
schtasks /Create /TN "Swave nightly backup" /TR $cmd /SC DAILY /ST $Time /F
"Scheduled daily at $Time; log: $log (the PC must be on and Docker running at that time)"
