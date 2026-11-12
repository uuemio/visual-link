param(
    [Parameter(Mandatory=$true)][string]$Config,
    [string]$InputImage,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [double]$Seconds = 30,
    [ValidateSet('display', 'display-stream', 'bootstrap', 'connect', 'capabilities', 'apply-mode')][string]$Mode = 'display',
    [string]$Python = '.venv-win\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (-not [double]::IsNaN($Seconds) -and -not [double]::IsInfinity($Seconds)) -or $Seconds -le 0) { throw 'Seconds must be finite and positive' }
$outputPath = $OutputDirectory
if (-not [System.IO.Path]::IsPathRooted($outputPath)) { $outputPath = Join-Path $root $outputPath }
$output = [System.IO.Path]::GetFullPath($outputPath)
if (Test-Path $output) { throw 'Output already exists' }
$parent = Split-Path -Parent $output
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$jobFile = "$output-job.json"
if (Test-Path $jobFile) { throw 'Job already exists' }
$job = @{
 config = (Resolve-Path $Config).Path
 input = $(if ($Mode -eq 'bootstrap' -or $Mode -eq 'capabilities' -or (($Mode -eq 'connect' -or $Mode -eq 'apply-mode') -and -not $InputImage)) { $null } else { (Resolve-Path $InputImage).Path })
 output = $output
 seconds = $Seconds
 mode = $Mode
 log = "$output-job.log"
 result = "$output-job-result.json"
}
$job | ConvertTo-Json | Set-Content -Path $jobFile -Encoding UTF8
$taskName = 'VisualLinkDisplay-' + [Guid]::NewGuid().ToString('N')
$pythonPath = (Resolve-Path $Python).Path
$jobScript = Join-Path $PSScriptRoot 'windows_display_job.py'
$arguments = '"' + $jobScript + '" "' + $jobFile + '"'
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $root
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$duration = $Seconds * $(if ($Mode -eq 'connect') { 2 } else { 1 })
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds ($duration+120)) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null
try {
 Start-ScheduledTask -TaskName $taskName
 $deadline = (Get-Date).AddSeconds($duration+100)
 do {
  Start-Sleep -Milliseconds 500
  if (Test-Path $job.result) { break }
 } while ((Get-Date) -lt $deadline)
 if (-not (Test-Path $job.result)) { throw "Display job did not return; inspect task $taskName and restore the recorded display mode" }
 $report = Get-Content $job.result -Raw | ConvertFrom-Json
 $report | ConvertTo-Json -Depth 5
 if ($report.status -ne 'complete') { throw 'Display job failed; see job logs' }
} finally {
 # Avoid killing a running watchdog: it owns restoration of the original mode.
 $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
 if ($task -and $task.State -ne 'Running') { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
 elseif (Test-Path $job.result) {
  Start-Sleep -Milliseconds 500
  $task = Get-ScheduledTask -TaskName $taskName
  if ($task.State -ne 'Running') { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
 }
}
