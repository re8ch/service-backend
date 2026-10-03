$ErrorActionPreference = 'Continue'
$distribution = 'Ubuntu-24.04'
$wsl = Join-Path $env:ProgramFiles 'WSL\wsl.exe'
$logDirectory = Join-Path $env:ProgramData 'RE8CH'
$logPath = Join-Path $logDirectory 'mubao-wsl-lifecycle.log'
$pausePath = 'M:\WSL\pause-autostart'
$anchor = [datetimeoffset]'2026-08-14T12:00:00+08:00'
$periodicMarker = Join-Path $logDirectory 'mubao-wsl-last-periodic.txt'
$fallbackMarker = Join-Path $logDirectory 'mubao-wsl-last-fallback.txt'
$configApplyMarker = Join-Path $logDirectory 'mubao-wsl-apply-config-once'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Invoke-WslBounded([string[]] $arguments, [int] $timeoutSeconds) {
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $wsl
    $process.StartInfo.Arguments = $arguments -join ' '
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        [void]$process.Start()
        if (-not $process.WaitForExit($timeoutSeconds * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            Write-LifecycleLog "status=wsl-timeout seconds=$timeoutSeconds args=$($arguments -join ' ')"
            return $false
        }
        $exitCode = $process.ExitCode
        if ($exitCode -ne 0) {
            Write-LifecycleLog "status=wsl-failed exitCode=$exitCode args=$($arguments -join ' ')"
            foreach ($line in @($process.StandardError.ReadToEnd() -split "`n" | Select-Object -First 5)) {
                Write-LifecycleLog "stderr=$line"
            }
            return $false
        }
        return $true
    } catch {
        Write-LifecycleLog "status=wsl-exception message=$($_.Exception.Message)"
        return $false
    } finally {
        $process.Dispose()
    }
}

function Write-LifecycleLog([string] $message) {
    Add-Content -LiteralPath $logPath -Value ('{0:o} {1}' -f (Get-Date), $message)
}

function Read-Marker([string] $path) {
    try {
        return [datetimeoffset]::Parse((Get-Content -LiteralPath $path -Raw).Trim())
    } catch {
        return [datetimeoffset]::MinValue
    }
}

function Save-Marker([string] $path, [datetimeoffset] $value) {
    $temporary = "$path.tmp"
    Set-Content -LiteralPath $temporary -Value $value.ToString('o') -NoNewline
    Move-Item -LiteralPath $temporary -Destination $path -Force
}

Write-LifecycleLog 'action=start task=RE8CH-WSL-Lifecycle'
$failures = 0
while ($true) {
    if (Test-Path -LiteralPath $pausePath) {
        Write-LifecycleLog 'action=wait reason=pause-file'
        Start-Sleep -Seconds 60
        continue
    }
    if (Test-Path -LiteralPath $configApplyMarker) {
        # A requested shared-VM restart applies .wslconfig only while both
        # surviving APIs are reachable from MUBAO's node identity.
        $a1 = Invoke-WslBounded @('-d', $distribution, '-u', 'root', '--exec', '/usr/bin/curl',
            '--interface', '10.250.0.29', '--noproxy', '*', '-kfsS', '--max-time', '8',
            'https://10.250.0.147:6443/readyz') 20
        $b1 = Invoke-WslBounded @('-d', $distribution, '-u', 'root', '--exec', '/usr/bin/curl',
            '--interface', '10.250.0.29', '--noproxy', '*', '-kfsS', '--max-time', '8',
            'https://10.250.0.125:6443/readyz') 20
        if ($a1 -and $b1) {
            Write-LifecycleLog 'action=apply-wsl-config peerApis=healthy'
            if (Invoke-WslBounded @('--shutdown') 120) {
                Remove-Item -LiteralPath $configApplyMarker -Force
                Start-Sleep -Seconds 15
            }
        } else {
            Write-LifecycleLog 'action=defer-wsl-config reason=peer-api-unhealthy'
            Start-Sleep -Seconds 60
            continue
        }
    }
    $now = [datetimeoffset]::Now
    $minutes = ($now - $anchor).TotalMinutes
    if ($minutes -ge 0) {
        $period = [math]::Floor($minutes / 2160)
        $boundary = $anchor.AddMinutes($period * 2160)
        if ($now -lt $boundary.AddMinutes(15) -and (Read-Marker $periodicMarker) -lt $boundary) {
            # Do not transiently remove an etcd voter unless both surviving
            # control-plane APIs are ready through the declared relay path.
            $a1 = Invoke-WslBounded @('-d', $distribution, '-u', 'root', '--exec', '/usr/bin/curl',
                '--interface', '10.250.0.29', '--noproxy', '*', '-kfsS', '--max-time', '8',
                'https://10.250.0.147:6443/readyz') 20
            $b1 = Invoke-WslBounded @('-d', $distribution, '-u', 'root', '--exec', '/usr/bin/curl',
                '--interface', '10.250.0.29', '--noproxy', '*', '-kfsS', '--max-time', '8',
                'https://10.250.0.125:6443/readyz') 20
            if ($a1 -and $b1) {
                Save-Marker $periodicMarker $boundary
                Write-LifecycleLog 'action=periodic-shutdown intervalHours=36 peerApis=healthy'
                [void](Invoke-WslBounded @('--shutdown') 120)
                Start-Sleep -Seconds 15
            } else {
                Write-LifecycleLog 'action=defer-periodic-shutdown reason=peer-api-unhealthy'
            }
        }
    }
    # The bounded Linux sleep holds the distro open and serves as a host-side
    # HCS/WSL responsiveness probe. Linux handles K3s-specific recovery.
    if (Invoke-WslBounded @('-d', $distribution, '-u', 'root', '--exec', '/bin/sleep', '120') 165) {
        $failures = 0
        continue
    }
    $failures++
    Write-LifecycleLog "status=probe-failed consecutive=$failures"
    if ($failures -eq 3) {
        Write-LifecycleLog 'action=terminate-distro reason=three-probe-failures'
        [void](Invoke-WslBounded @('--terminate', $distribution) 90)
    } elseif ($failures -ge 6 -and ([datetimeoffset]::Now - (Read-Marker $fallbackMarker)).TotalHours -ge 6) {
        # Shared-VM shutdown also restarts Docker Desktop's WSL distributions.
        # Cap it at once in six hours and only after distro recovery failed.
        Write-LifecycleLog 'action=shutdown-shared-vm reason=terminate-failed-to-recover'
        Save-Marker $fallbackMarker ([datetimeoffset]::Now)
        [void](Invoke-WslBounded @('--shutdown') 120)
    }
    Start-Sleep -Seconds 30
}
