[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)] [ValidateSet('a1-windows', 'b1-windows')] [string] $NodeName,
    [string] $Distribution = 'Ubuntu-24.04',
    [string] $UserCaPublicKeyPath,
    [string] $InventoryPath = "$PSScriptRoot\..\management-ssh-mesh\inventory.json"
)

$ErrorActionPreference = 'Stop'
$inventory = Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json
$node = @($inventory.spec.nodes | Where-Object name -eq $NodeName)
if ($node.Count -ne 1) { throw "Inventory must contain exactly one node named $NodeName" }
$peer = @($inventory.spec.nodes | Where-Object { $_.platform -eq 'windows-server-2022' -and $_.name -ne $NodeName })
if ($peer.Count -ne 1) { throw 'Inventory must contain exactly one Windows peer' }

$programDirectory = Join-Path $env:ProgramData 'RE8CH'
$watchdogPath = Join-Path $programDirectory 're8ch-control-plane-watchdog.ps1'
$configPath = Join-Path $programDirectory 'control-plane-watchdog.json'
$taskName = $inventory.spec.watchdog.taskName
$config = [ordered]@{
    nodeName = $NodeName
    distribution = $Distribution
    bootGraceSeconds = $inventory.spec.watchdog.bootGraceSeconds
    localWslProbeTimeoutSeconds = $inventory.spec.watchdog.localWslProbeTimeoutSeconds
    failureThreshold = $inventory.spec.watchdog.failureThreshold
    nodeReplyTimeoutSeconds = $inventory.spec.watchdog.nodeReplyTimeoutSeconds
    hostRebootCooldownSeconds = $inventory.spec.watchdog.hostRebootCooldownSeconds
    peerQuorumRequired = $inventory.spec.watchdog.peerQuorumRequired
    minimumHealthyPeers = $inventory.spec.watchdog.minimumHealthyPeers
    hostRebootDelaySeconds = [int] $node[0].peerPriority * [int] $inventory.spec.watchdog.priorityDelaySeconds
    lifecycleTaskName = $inventory.spec.watchdog.lifecycleTaskName
    peerHealthEndpoints = @(
        [ordered]@{address = $peer[0].wsl.nodeAddress; port = 6443},
        [ordered]@{address = '192.0.2.97'; port = 6443}
    )
}

if ($PSCmdlet.ShouldProcess($NodeName, 'install RE8CH control-plane host recovery')) {
    New-Item -ItemType Directory -Path $programDirectory -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 're8ch-control-plane-watchdog.ps1') -Destination $watchdogPath -Force
    $config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
    # WSL memory policy belongs to the Windows account that owns the lifecycle
    # task. dropCache returns reclaimable Linux page cache promptly while the
    # fixed ceiling leaves Windows enough memory to run the independent
    # watchdog and OpenSSH recovery plane. Applying it is deferred until the
    # next declared WSL lifecycle restart or Windows reboot.
    $wslConfigPath = Join-Path $env:USERPROFILE '.wslconfig'
    @(
        '[wsl2]'
        ('memory={0}' -f $node[0].wsl.memory)
        ('swap={0}' -f $node[0].wsl.swap)
        'pageReporting=true'
        ''
        '[experimental]'
        ('autoMemoryReclaim={0}' -f $node[0].wsl.autoMemoryReclaim)
    ) | Set-Content -LiteralPath $wslConfigPath -Encoding ascii
    if (-not (Get-Service -Name sshd -ErrorAction SilentlyContinue)) {
        $capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~192.0.2.79'
        if ($capability.State -ne 'Installed') {
            Add-WindowsCapability -Online -Name $capability.Name | Out-Null
        }
    }
    Set-Service -Name sshd -StartupType Automatic
    Start-Service -Name sshd
    $firewallName = 'RE8CH management SSH mesh'
    Remove-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress $inventory.spec.allowedSourceCidrs | Out-Null

    if ($UserCaPublicKeyPath) {
        $sshDirectory = Join-Path $env:ProgramData 'ssh'
        Copy-Item -LiteralPath $UserCaPublicKeyPath -Destination (Join-Path $sshDirectory 're8ch_user_ca.pub') -Force
        $sshdConfig = Join-Path $sshDirectory 'sshd_config'
        $content = @((Get-Content -LiteralPath $sshdConfig) | Where-Object { $_ -notmatch '^\s*TrustedUserCAKeys\s+' })
        $content += 'TrustedUserCAKeys __PROGRAMDATA__/ssh/re8ch_user_ca.pub'
        $content | Set-Content -LiteralPath $sshdConfig -Encoding ascii
        Restart-Service -Name sshd
    }

    $action = New-ScheduledTaskAction -Execute 'PowerShell.exe' -Argument ('-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "{0}" -ConfigPath "{1}"' -f $watchdogPath, $configPath)
    $startup = New-ScheduledTaskTrigger -AtStartup
    $startup.Delay = 'PT15M'
    $interval = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Seconds $inventory.spec.watchdog.intervalSeconds)
    # WSL lifecycle stays Administrator-owned. The independent observer runs
    # as SYSTEM so Windows reboot does not depend on an S4U user token.
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($startup, $interval) -Principal $principal -Settings $settings -Force | Out-Null
}

Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Get-Service -Name sshd | Select-Object Name, Status, StartType
