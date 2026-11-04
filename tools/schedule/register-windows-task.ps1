<#
.SYNOPSIS
  Registers, or removes, a Windows scheduled task that runs tools/precompute.py in WSL after the markets close.

.DESCRIPTION
  The task runs on weekdays at -At (local time) and calls:
    wsl.exe [-d <Distro>] -- bash -lc "cd '<RepoPath>' && python3 tools/precompute.py <PrecomputeArgs> >> reports/precompute/task.log 2>&1"
  NSE closes at 15:30 IST and the US market at 16:00 ET. Pick a time after the later close in your time zone;
  for US Pacific time, 13:30 or later.
  The Copilot CLI inside WSL must be logged in once (run `copilot`, then `/login`). Each run uses up to about
  10 of Tapetide's 50 free calls a day; --max-runs caps the number of runs.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\schedule\register-windows-task.ps1 -RepoPath "/mnt/c/Users/me/my_fin" -At 14:00

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\schedule\register-windows-task.ps1 -Remove
#>
param(
  [string]$RepoPath = "",
  [string]$At = "14:00",
  [string]$Distro = "",
  [string]$TaskName = "my_fin precompute",
  [string]$PrecomputeArgs = "--max-runs 4",
  [switch]$Remove
)
$ErrorActionPreference = "Stop"

if ($Remove) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Removed scheduled task '$TaskName'."
  exit 0
}
if (-not $RepoPath) {
  throw "Pass -RepoPath with the repository's path inside WSL, for example /mnt/c/Users/me/my_fin"
}
if ($RepoPath.Contains("'")) {
  throw "-RepoPath must not contain a single quote"
}

$distroArg = if ($Distro) { "-d $Distro " } else { "" }
$command = "cd '$RepoPath' && mkdir -p reports/precompute && python3 tools/precompute.py $PrecomputeArgs >> reports/precompute/task.log 2>&1"
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "$distroArg-- bash -lc `"$command`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Description "Precompute investment-debate runs for holdings and watchlist (my_fin)" -Force | Out-Null
Write-Host "Registered '$TaskName': weekdays at $At, running: wsl.exe $distroArg-- bash -lc `"$command`""
