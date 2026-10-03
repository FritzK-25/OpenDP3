<#
.SYNOPSIS
    Install or update the single OpenPowerstation watchdog Scheduled Task.

.DESCRIPTION
    The task owns the watchdog only. The watchdog owns the detached recorder and
    bridge workers through start_all.py, preventing a separate recorder task from
    competing for Bluetooth or SQLite.
##>
[CmdletBinding()]
param(
    [string]$TaskName = 'OpenDP3 Recorder',
    [string]$DataDir = (Join-Path $PSScriptRoot '..\data'),
    [string]$Python = (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe')
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$data = (Resolve-Path $DataDir).Path
$watchdog = (Resolve-Path (Join-Path $PSScriptRoot 'watchdog.py')).Path

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python runtime not found: $Python"
}

$action = New-ScheduledTaskAction -Execute $Python `
    -Argument ('"{0}" --data-dir "{1}"' -f $watchdog, $data) `
    -WorkingDirectory $root
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# A repeat trigger also recovers clean/interrupted exits that RestartCount misses.
$user = "$env:USERDOMAIN\$env:USERNAME"
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$retry = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 2)
$triggers = @($logon, $retry)

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Set-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings -Trigger $triggers
} else {
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
        -Settings $settings -Principal $principal `
        -Description 'Single owner for OpenPowerstation telemetry watchdog, recorder, and MQTT bridges.'
}

Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started $TaskName using $watchdog"
