# Registers the nightly backup (03:30) as a Windows scheduled task for the current user.
# Run once:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-backup-task.ps1
# Remove:    schtasks /Delete /TN "Swave nightly backup" /F
param([string]$Time = "03:30", [string]$Dir = "C:\SwaveBackups")
$script = Join-Path $PSScriptRoot "backup.ps1"
$log = Join-Path $Dir "backup.log"
New-Item -ItemType Directory -Force $Dir | Out-Null
$cmd = "powershell -NoProfile -ExecutionPolicy Bypass -Command `"& '$script' -Dir '$Dir' *>> '$log'`""
schtasks /Create /TN "Swave nightly backup" /TR $cmd /SC DAILY /ST $Time /F
"Scheduled daily at $Time; log: $log (the PC must be on and Docker running at that time)"
