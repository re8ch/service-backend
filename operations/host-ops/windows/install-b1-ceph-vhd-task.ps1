[CmdletBinding(SupportsShouldProcess = $true)]
param([string] $ScriptPath)

$ErrorActionPreference = 'Stop'
if (-not $ScriptPath) { $ScriptPath = Join-Path $PSScriptRoot 'ensure-b1-ceph-vhd.ps1' }
$taskName = 'RE8CH B1 Ceph VHD Reattach'
$targetDirectory = Join-Path $env:ProgramData 'RE8CH'
$target = Join-Path $targetDirectory 'ensure-b1-ceph-vhd.ps1'
if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
    throw "Source script is absent: $ScriptPath"
}
if ($PSCmdlet.ShouldProcess($taskName, 'install bounded Ceph VHDX reattach task')) {
    New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
    if ([IO.Path]::GetFullPath($ScriptPath) -ne [IO.Path]::GetFullPath($target)) {
        Copy-Item -LiteralPath $ScriptPath -Destination $target -Force
    }
    $action = New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument ('-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "{0}"' -f $target)
    $startup = New-ScheduledTaskTrigger -AtStartup
    $startup.Delay = 'PT2M'
    $interval = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    # Administrator owns the WSL distribution. SYSTEM cannot reliably attach to it.
    $principal = New-ScheduledTaskPrincipal -UserId 'Administrator' -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($startup, $interval) -Principal $principal -Settings $settings -Force | Out-Null
}
Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Select-Object TaskName, State
