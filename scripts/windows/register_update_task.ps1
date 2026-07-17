<#
.SYNOPSIS
    Registers (or removes) the Windows scheduled task that keeps the NHIT
    dashboard's ETC data current.

.DESCRIPTION
    Creates a current-user scheduled task (no admin needed) that runs
    run_update.bat, which crawls IHMCL for new report PDFs, rebuilds the
    dashboard JSON, and pushes to GitHub (Vercel then redeploys).

    Uses raw Task XML because New-ScheduledTaskTrigger has no monthly
    trigger and schtasks.exe cannot set StartWhenAvailable/battery settings.
    StartWhenAvailable means a run missed while the PC was off fires at the
    next logon.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register_update_task.ps1
    # monthly on day 15 at 10:07 (the default)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register_update_task.ps1 -Cadence Weekly

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register_update_task.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [ValidateSet("Monthly", "Weekly", "Daily")]
    [string]$Cadence = "Monthly",

    # Day of month for -Cadence Monthly (1-28 so it exists in every month).
    # 15th, not the 1st: IHMCL publishes month M's report on an unpredictable
    # day *during* M+1, so a run on the 1st usually finds nothing and the data
    # waits another month. By mid-month the previous month is reliably up.
    [ValidateRange(1, 28)]
    [int]$DayOfMonth = 15,

    # Day of week for -Cadence Weekly.
    [ValidateSet("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]
    [string]$DayOfWeek = "Monday",

    [ValidatePattern("^([01]\d|2[0-3]):[0-5]\d$")]
    [string]$At = "10:07",

    [string]$TaskName = "NHIT ETC Data Refresh",

    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Unregistered scheduled task '$TaskName'."
    return
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$batPath = Join-Path $PSScriptRoot "run_update.bat"
if (-not (Test-Path $batPath)) { throw "run_update.bat not found at $batPath" }
if (-not (Test-Path (Join-Path $repoRoot ".venv\Scripts\python.exe"))) {
    throw "Project venv not found under $repoRoot\.venv - the task would fail."
}

$startBoundary = "{0}T{1}:00" -f (Get-Date -Format "yyyy-MM-dd"), $At

switch ($Cadence) {
    "Monthly" {
        $schedule = @"
      <ScheduleByMonth>
        <DaysOfMonth><Day>$DayOfMonth</Day></DaysOfMonth>
        <Months><January /><February /><March /><April /><May /><June /><July /><August /><September /><October /><November /><December /></Months>
      </ScheduleByMonth>
"@
    }
    "Weekly" {
        $schedule = @"
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek><$DayOfWeek /></DaysOfWeek>
      </ScheduleByWeek>
"@
    }
    "Daily" {
        $schedule = @"
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
"@
    }
}

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Checks IHMCL for new ETC transaction reports, rebuilds the NHIT dashboard JSON, and pushes to GitHub (Vercel auto-deploys). Cadence: $Cadence.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
$schedule
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$userId</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c "$batPath"</Arguments>
      <WorkingDirectory>$repoRoot</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

Register-ScheduledTask -TaskName $TaskName -Xml $taskXml -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Registered scheduled task '$TaskName' ($Cadence at $At)."
Write-Host "Next run time: $($info.NextRunTime)"
Write-Host "Smoke test now with:  schtasks /run /tn `"$TaskName`""
Write-Host "Logs land in:         $repoRoot\logs\scheduled_runs.log"
