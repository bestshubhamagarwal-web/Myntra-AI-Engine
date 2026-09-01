# Register a daily 02:00 Task Scheduler job. Run once from an elevated PowerShell
# if the task must run whether or not you are logged on.
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Script = Join-Path $PSScriptRoot "pipeline.ps1"
$TaskName = "MyntraDiscoveryPipeline"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At 2:00am
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Write-Host "Registered $TaskName (daily 02:00). Do not also run the n8n workflow on this host."
Write-Host "Repo: $RepoRoot"
