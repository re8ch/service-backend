$ErrorActionPreference = 'Continue'
$distribution = 'Ubuntu-24.04'
$wsl = Join-Path $env:ProgramFiles 'WSL\wsl.exe'
$logDirectory = Join-Path $env:ProgramData 'RE8CH'
$logPath = Join-Path $logDirectory 'mubao-wsl-lifecycle.log'
$pausePath = 'M:\WSL\pause-autostart'
$anchor = [datetimeoffset]'2026-08-14T12:00:00+08:00'
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

$minutes = ([datetimeoffset]::Now - $anchor).TotalMinutes
$remainder = (($minutes % 2160) + 2160) % 2160
if ($minutes -ge 0 -and ($remainder -le 15 -or $remainder -ge 2155)) {
    Write-LifecycleLog 'action=periodic-shutdown intervalHours=36'
    [void](Invoke-WslBounded @('--shutdown') 120)
    Start-Sleep -Seconds 15
}

Write-LifecycleLog 'action=start task=RE8CH-WSL-Lifecycle'
$failures = 0
$lastFallback = [datetimeoffset]::MinValue
while ($true) {
    if (Test-Path -LiteralPath $pausePath) {
        Write-LifecycleLog 'action=wait reason=pause-file'
        Start-Sleep -Seconds 60
        continue
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
    } elseif ($failures -ge 6 -and ([datetimeoffset]::Now - $lastFallback).TotalHours -ge 6) {
        # Shared-VM shutdown also restarts Docker Desktop's WSL distributions.
        # Cap it at once in six hours and only after distro recovery failed.
        Write-LifecycleLog 'action=shutdown-shared-vm reason=terminate-failed-to-recover'
        $lastFallback = [datetimeoffset]::Now
        [void](Invoke-WslBounded @('--shutdown') 120)
    }
    Start-Sleep -Seconds 30
}
