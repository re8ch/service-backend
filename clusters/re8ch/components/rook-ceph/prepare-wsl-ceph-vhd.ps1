param(
  [string]$VhdPath = "D:\RE8CH-Ceph\ceph-pilot-osd.vhdx",
  [int]$MaximumSizeMiB = 204800
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Administrator privileges are required"
}

$parent = Split-Path -Parent $VhdPath
New-Item -ItemType Directory -Force -Path $parent | Out-Null

if (-not (Test-Path -LiteralPath $VhdPath)) {
  $diskpartScript = Join-Path $env:TEMP "re8ch-ceph-create-vhd.txt"
  @(
    "create vdisk file=`"$VhdPath`" maximum=$MaximumSizeMiB type=expandable",
    "exit"
  ) | Set-Content -LiteralPath $diskpartScript -Encoding ascii
  try {
    & diskpart.exe /s $diskpartScript
    if ($LASTEXITCODE -ne 0) {
      throw "diskpart failed with exit code $LASTEXITCODE"
    }
  }
  finally {
    Remove-Item -LiteralPath $diskpartScript -Force -ErrorAction SilentlyContinue
  }
}

& wsl.exe --mount $VhdPath --vhd --bare
if ($LASTEXITCODE -ne 0) {
  throw "wsl --mount failed with exit code $LASTEXITCODE"
}

Get-Item -LiteralPath $VhdPath | Select-Object FullName, Length
