$ErrorActionPreference = 'Continue'
$distribution = 'Ubuntu-24.04'
$wsl = Join-Path $env:ProgramFiles 'WSL\wsl.exe'
$logDirectory = Join-Path $env:ProgramData 'RE8CH'
$logPath = Join-Path $logDirectory 'mubao-wsl-lifecycle.log'
$pausePath = 'M:\WSL\pause-autostart'
$anchor = [datetimeoffset]'2026-08-14T12:00:00+08:00'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Write-LifecycleLog([string] $message) {
    Add-Content -LiteralPath $logPath -Value ('{0:o} {1}' -f (Get-Date), $message)
}

$minutes = ([datetimeoffset]::Now - $anchor).TotalMinutes
$remainder = (($minutes % 2160) + 2160) % 2160
if ($minutes -ge 0 -and ($remainder -le 15 -or $remainder -ge 2155)) {
    Write-LifecycleLog 'action=periodic-shutdown intervalHours=36'
    & $wsl --shutdown 2>&1 | ForEach-Object { Write-LifecycleLog "shutdown=$_" }
    Write-LifecycleLog "status=shutdown-exited exitCode=$LASTEXITCODE"
    Start-Sleep -Seconds 15
}

Write-LifecycleLog 'action=start task=RE8CH-WSL-Lifecycle'
while ($true) {
    if (Test-Path -LiteralPath $pausePath) {
        Write-LifecycleLog 'action=wait reason=pause-file'
        Start-Sleep -Seconds 60
        continue
    }
    & $wsl -d $distribution -u root --exec /bin/true 2>&1 | ForEach-Object { Write-LifecycleLog "startup=$_" }
    if ($LASTEXITCODE -ne 0) {
        Write-LifecycleLog "status=start-failed exitCode=$LASTEXITCODE"
        Start-Sleep -Seconds 60
        continue
    }
    Write-LifecycleLog 'status=started'
    & $wsl -d $distribution -u root --exec /bin/sleep infinity 2>&1 | ForEach-Object { Write-LifecycleLog "hold=$_" }
    Write-LifecycleLog "status=hold-exited exitCode=$LASTEXITCODE"
    Start-Sleep -Seconds 15
}
