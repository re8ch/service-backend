[CmdletBinding()]
param([Parameter(Mandatory = $true)] [string] $ConfigPath)

$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$statePath = Join-Path $env:ProgramData 'RE8CH\control-plane-watchdog-state.json'
$logPath = Join-Path $env:ProgramData 'RE8CH\control-plane-watchdog.log'
New-Item -ItemType Directory -Path (Split-Path -Parent $statePath) -Force | Out-Null

function Write-WatchdogLog([string] $Message) {
    Add-Content -LiteralPath $logPath -Value ('{0:o} node={1} {2}' -f (Get-Date), $config.nodeName, $Message)
}

function Read-WatchdogState {
    if (Test-Path -LiteralPath $statePath) {
        return Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    }
    return [pscustomobject]@{consecutiveFailures = 0; wslRecoveryRequestedAt = $null; lastHostRebootRequest = $null}
}

function Save-WatchdogState($State) {
    $temporaryPath = "$statePath.tmp"
    $State | ConvertTo-Json | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $statePath -Force
}

function Test-TcpEndpoint([string] $Address, [int] $Port, [int] $TimeoutMilliseconds = 2000) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync($Address, $Port)
        return $task.Wait($TimeoutMilliseconds) -and $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Test-LocalWsl {
    $arguments = @('-d', $config.distribution, '--exec', '/bin/sh', '-lc', 'systemctl is-active --quiet k3s && systemctl is-active --quiet ssh && ip -4 addr show | grep -q "10.181.22."')
    $process = Start-Process -FilePath "$env:SystemRoot\System32\wsl.exe" -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $timeoutMilliseconds = [int] $config.localWslProbeTimeoutSeconds * 1000
    if (-not $process.WaitForExit($timeoutMilliseconds)) {
        Write-WatchdogLog "status=probe-timeout timeoutSeconds=$($config.localWslProbeTimeoutSeconds)"
        try { $process.Kill($true) } catch { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        return $false
    }
    return $process.ExitCode -eq 0
}

$state = Read-WatchdogState
$operatingSystem = Get-CimInstance Win32_OperatingSystem
$uptimeSeconds = ((Get-Date) - $operatingSystem.LastBootUpTime).TotalSeconds
if ($uptimeSeconds -lt [int] $config.bootGraceSeconds) {
    Write-WatchdogLog "action=wait reason=boot-grace uptimeSeconds=$([int]$uptimeSeconds)"
    exit 0
}

$sshd = Get-Service -Name sshd -ErrorAction SilentlyContinue
if ($null -ne $sshd -and $sshd.Status -ne 'Running') {
    Start-Service -Name sshd
    Write-WatchdogLog 'action=start-service service=sshd'
}

if (Test-LocalWsl) {
    $state.consecutiveFailures = 0
    $state | Add-Member -NotePropertyName wslRecoveryRequestedAt -NotePropertyValue $null -Force
    Save-WatchdogState $state
    Write-WatchdogLog 'status=healthy'
    exit 0
}

$state.consecutiveFailures = [int] $state.consecutiveFailures + 1
Write-WatchdogLog "status=unhealthy consecutiveFailures=$($state.consecutiveFailures)"
if ($state.consecutiveFailures -lt [int] $config.failureThreshold) {
    Save-WatchdogState $state
    exit 1
}

$healthyPeers = @($config.peerHealthEndpoints | Where-Object { Test-TcpEndpoint -Address $_.address -Port ([int] $_.port) })
if ([bool] $config.peerQuorumRequired -and $healthyPeers.Count -lt [int] $config.minimumHealthyPeers) {
    Save-WatchdogState $state
    Write-WatchdogLog "action=hold reason=insufficient-healthy-peers healthyPeers=$($healthyPeers.Count) required=$($config.minimumHealthyPeers)"
    exit 2
}

if (-not $state.PSObject.Properties['wslRecoveryRequestedAt'] -or -not $state.wslRecoveryRequestedAt) {
    $state | Add-Member -NotePropertyName wslRecoveryRequestedAt -NotePropertyValue (Get-Date).ToString('o') -Force
    $state.consecutiveFailures = 0
    Save-WatchdogState $state
    Write-WatchdogLog 'action=request-wsl-recovery'
    Start-ScheduledTask -TaskName $config.lifecycleTaskName
    exit 3
}

$recoveryAgeSeconds = ((Get-Date) - [datetime] $state.wslRecoveryRequestedAt).TotalSeconds
$requiredWaitSeconds = [int] $config.nodeReplyTimeoutSeconds + [int] $config.hostRebootDelaySeconds
if ($recoveryAgeSeconds -lt $requiredWaitSeconds) {
    Save-WatchdogState $state
    Write-WatchdogLog "action=hold reason=await-node-reply remainingSeconds=$([int]($requiredWaitSeconds - $recoveryAgeSeconds))"
    exit 4
}

$lastRequest = if ($state.lastHostRebootRequest) { [datetime] $state.lastHostRebootRequest } else { [datetime]::MinValue }
$secondsSinceLastRequest = ((Get-Date) - $lastRequest).TotalSeconds
if ($secondsSinceLastRequest -lt [int] $config.hostRebootCooldownSeconds) {
    Save-WatchdogState $state
    Write-WatchdogLog "action=hold reason=reboot-cooldown remainingSeconds=$([int]($config.hostRebootCooldownSeconds - $secondsSinceLastRequest))"
    exit 5
}

$state.lastHostRebootRequest = (Get-Date).ToString('o')
Save-WatchdogState $state
Write-WatchdogLog "action=host-reboot healthyPeers=$($healthyPeers.Count)"
& "$env:SystemRoot\System32\shutdown.exe" /r /f /t 0 /d p:4:1 /c 'RE8CH control-plane node did not recover after WSL lifecycle restart'
if ($LASTEXITCODE -ne 0) {
    Write-WatchdogLog "action=host-reboot-failed exitCode=$LASTEXITCODE"
    exit $LASTEXITCODE
}
