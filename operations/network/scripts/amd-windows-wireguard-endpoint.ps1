[CmdletBinding()]
param([switch] $Install)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Elevated Administrator or SYSTEM token required'
}

$interface = 're8ch-amdhost'
$serviceName = 'WireGuardTunnel$re8ch-amdhost'
$peer = 'xYQc2PYp2UppOzMkddAK8I0yxd+k7lLDHlD1olJCO2E='
$endpoint = '223.159.210.222:51820'
$wg = 'C:\Program Files\WireGuard\wg.exe'
$log = 'C:\ProgramData\RE8CH\amd-windows-wireguard-endpoint.log'
if (-not (Test-Path -LiteralPath $wg)) { throw 'WireGuard wg.exe is missing' }

$service = Get-Service -Name $serviceName -ErrorAction Stop
if ($service.Status -ne 'Running') { Start-Service -Name $serviceName }
$current = & $wg show $interface endpoints
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect AMD host tunnel' }
$peerLine = @($current | Where-Object { $_.StartsWith("$peer`t") })
if ($peerLine.Count -ne 1) { throw 'RouterOS peer differs from the reviewed key' }
if ($peerLine[0] -ne "$peer`t$endpoint") {
    & $wg set $interface peer $peer endpoint $endpoint
    if ($LASTEXITCODE -ne 0) { throw 'Could not set RouterOS public endpoint' }
    Add-Content -LiteralPath $log -Value ("{0:o} endpoint={1}" -f (Get-Date), $endpoint)
}

if ($Install) {
    $scriptPath = 'C:\ProgramData\RE8CH\amd-windows-wireguard-endpoint.ps1'
    if ($PSCommandPath -ne $scriptPath) { Copy-Item -LiteralPath $PSCommandPath -Destination $scriptPath -Force }
    $action = New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument ('-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "{0}"' -f $scriptPath)
    $boot = New-ScheduledTaskTrigger -AtStartup
    $boot.Delay = 'PT2M'
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    Register-ScheduledTask -TaskName 'RE8CH AMD WireGuard Endpoint' -Action $action -Trigger @($boot, $repeat) -Principal $taskPrincipal -Settings $settings -Force | Out-Null
}

& $wg show $interface endpoints
