[CmdletBinding()]
param(
    [string] $Distribution = 'Ubuntu-24.04',
    [string] $VhdPath = 'D:\RE8CH-Ceph\ceph-pilot-osd.vhdx',
    [string] $DevicePath = '/dev/disk/by-id/scsi-36002248092fe886403f06a91bb13700e'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $VhdPath -PathType Leaf)) {
    throw "Ceph VHDX is absent: $VhdPath"
}
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'

function Test-CephDevice {
    & $wsl -d $Distribution -u root -- /usr/bin/test -b $DevicePath
    return $LASTEXITCODE -eq 0
}

if (Test-CephDevice) { exit 0 }
& $wsl --mount $VhdPath --vhd --bare
if ($LASTEXITCODE -ne 0) {
    throw "WSL did not attach the existing Ceph VHDX (exit $LASTEXITCODE)"
}
for ($attempt = 0; $attempt -lt 6; $attempt++) {
    if (Test-CephDevice) { exit 0 }
    Start-Sleep -Seconds 5
}
throw 'Ceph VHDX attachment completed, but the declared OSD block device is absent'
